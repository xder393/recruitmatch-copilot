from __future__ import annotations


from app.database import Base, create_engine_and_session
from tests.fakes.artifacts import FakeArtifactStore
from tests.support.artifacts import attach_artifact
from app.knowledge.chunking import chunk_document
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.repositories.knowledge import KnowledgeRepository
from app.services.knowledge_processing import KnowledgeProcessingService


def _database(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ingestion.db'}"
    engine, factory = create_engine_and_session(database_url)
    Base.metadata.create_all(engine)
    from tests.fakes.retrieval import sqlite_retrieval_dependencies
    from tests.support.application import DeterministicEmbeddingAdapter

    index, source_indexer = sqlite_retrieval_dependencies(database_url, DeterministicEmbeddingAdapter())
    return factory, index, source_indexer


class BrokenSourceIndexer:
    def reconcile_staging(self, *args, **kwargs):
        raise RuntimeError("embedding secret")

    def fail(self, *args, **kwargs):
        return None


def _uow_factory(session_factory, source_indexer=None):
    from tests.fakes.processing import FakeProcessingUnitOfWorkFactory

    return FakeProcessingUnitOfWorkFactory(session_factory, source_indexer.writer if source_indexer else None)


def _stored_document(tmp_path, factory, content=b"policy text"):
    store = FakeArtifactStore()
    with factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        document = KnowledgeDocument(
            tenant_id=tenant.id,
            document_type="policy",
            original_filename="policy.txt",
            media_type="text/plain",
            size_bytes=len(content),
            checksum="a" * 64,
            status="uploaded",
        )
        session.add(document)
        attach_artifact(session, document, content, owner_type="knowledge_document", store=store)
        session.commit()
        return store, tenant.id, document.id


def test_chunk_offsets_resolve_to_normalized_source():
    text = "A" * 900
    chunks = chunk_document(text)
    assert len(chunks) == 2
    assert all(text[item.start : item.end] == item.content for item in chunks)
    assert chunks[1].start == 600


def test_processing_activates_embedded_generation(tmp_path):
    factory, index, source_indexer = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"Python interview policy")
    KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1
        assert document.search_index_status == "ready"
    assert index._chunks[0].content == "Python interview policy"


def test_processor_does_not_process_missing_artifact_anchor(tmp_path):
    factory, index, source_indexer = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory)
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.artifact_id = None
        session.commit()
    KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "uploaded"
        assert document.error_code is None
        assert document.active_generation == 0
    assert not index._chunks


def test_duplicate_worker_delivery_does_not_reprocess_ready_document(tmp_path):
    factory, _, source_indexer = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"stable policy")
    KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer).process(
        tenant_id, document_id
    )
    KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, BrokenSourceIndexer()).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1


def test_processing_duplicate_is_acked_then_expired_work_is_recovered(tmp_path):
    from datetime import datetime, timedelta, timezone

    factory, _, source_indexer = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"recoverable policy")
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.status = "processing"
        document.processing_lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        session.commit()
    service = KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer)
    assert service.process(tenant_id, document_id).value == "duplicate_active"

    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()
    assert service.process(tenant_id, document_id).value == "completed"
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1


def test_transient_reindex_retry_keeps_previous_generation_active(tmp_path, monkeypatch):
    factory, index, source_indexer = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"new policy")
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.status = "uploaded"
        session.commit()
    KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.status = "uploaded"
        session.commit()

    def embedding_unavailable(texts):
        raise RuntimeError("embedding secret")

    monkeypatch.setattr(source_indexer.embedder, "embed_documents", embedding_unavailable)
    result = KnowledgeProcessingService(_uow_factory(factory, source_indexer), store, source_indexer).process(
        tenant_id, document_id
    )
    assert result.value == "retry_short"
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "uploaded"
        assert document.next_retry_at is not None
        assert document.error_code == "embedding_failed"
        assert document.active_generation == 1
        assert document.search_index_status == "ready"
        assert "embedding secret" not in (document.error_message or "")
    assert index._chunks[0].is_active is True
