from __future__ import annotations

import pytest

from app.core.exceptions import UnsupportedFileError
from app.database import Base, create_engine_and_session
from app.knowledge.artifacts import KnowledgeArtifactStore
from app.knowledge.chunking import chunk_document
from app.knowledge.schemas import ChunkInput
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.repositories.knowledge import KnowledgeRepository
from app.services.knowledge_processing import KnowledgeProcessingService


class FakeIndexer:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def embed(self, chunks: list[ChunkInput]) -> list[ChunkInput]:
        if self.error:
            raise self.error
        return [item.model_copy(update={"vector": [1.0, 0.0]}) for item in chunks]


def _database(tmp_path):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'ingestion.db'}")
    Base.metadata.create_all(engine)
    return factory


def _uow_factory(session_factory):
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory

    return SqlAlchemyUnitOfWorkFactory(session_factory)


def _stored_document(tmp_path, factory, content=b"policy text"):
    store = KnowledgeArtifactStore(tmp_path / "artifacts")
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
            artifact_key="pending",
            status="uploaded",
        )
        session.add(document)
        session.flush()
        document.artifact_key = store.put(tenant.id, document.id, "policy.txt", content).key
        session.commit()
        return store, tenant.id, document.id


def test_chunk_offsets_resolve_to_normalized_source():
    text = "A" * 900
    chunks = chunk_document(text, "policy")
    assert len(chunks) == 2
    assert all(text[item.start : item.end] == item.content for item in chunks)
    assert chunks[1].start == 600


def test_artifact_key_hides_original_filename_and_rejects_escape(tmp_path):
    store = KnowledgeArtifactStore(tmp_path)
    stored = store.put("tenant-1", "document-1", "机密制度.docx", b"content")
    assert "机密制度" not in stored.key
    assert stored.key.startswith("knowledge/tenant-1/document-1/")
    with pytest.raises(UnsupportedFileError):
        store.read("../../outside")


def test_processing_activates_embedded_generation(tmp_path):
    factory = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"Python interview policy")
    KnowledgeProcessingService(_uow_factory(factory), store, FakeIndexer()).process(tenant_id, document_id)
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1
        chunks = KnowledgeRepository(session).active_chunks(tenant_id, document_id)
        assert chunks[0].content == "Python interview policy"
        assert chunks[0].vector == [1.0, 0.0]


def test_duplicate_worker_delivery_does_not_reprocess_ready_document(tmp_path):
    factory = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"stable policy")
    KnowledgeProcessingService(_uow_factory(factory), store, FakeIndexer()).process(tenant_id, document_id)
    KnowledgeProcessingService(_uow_factory(factory), store, FakeIndexer(RuntimeError("must not embed twice"))).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1


def test_processing_lease_is_retried_then_stale_work_is_recovered(tmp_path):
    from datetime import datetime, timedelta, timezone

    factory = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"recoverable policy")
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.status = "processing"
        document.updated_at = datetime.now(timezone.utc)
        session.commit()
    service = KnowledgeProcessingService(_uow_factory(factory), store, FakeIndexer())
    assert service.process(tenant_id, document_id) is False

    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        document.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()
    assert service.process(tenant_id, document_id) is True
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "ready"
        assert document.active_generation == 1


def test_failed_reindex_keeps_previous_generation_active(tmp_path):
    factory = _database(tmp_path)
    store, tenant_id, document_id = _stored_document(tmp_path, factory, b"new policy")
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        KnowledgeRepository(session).replace_generation(
            document,
            1,
            [ChunkInput(source_type="policy", content="old", start=0, end=3, vector=[1.0])],
        )
        document.status = "uploaded"
        session.commit()
    KnowledgeProcessingService(_uow_factory(factory), store, FakeIndexer(RuntimeError("embedding secret"))).process(
        tenant_id, document_id
    )
    with factory() as session:
        document = KnowledgeRepository(session).get_document(tenant_id, document_id)
        assert document.status == "failed"
        assert document.active_generation == 1
        assert KnowledgeRepository(session).active_chunks(tenant_id, document_id)[0].content == "old"
        assert "embedding secret" not in document.error_message
