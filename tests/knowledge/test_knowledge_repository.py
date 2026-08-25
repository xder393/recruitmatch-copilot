from __future__ import annotations

from app.database import Base, create_engine_and_session
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.repositories.knowledge import KnowledgeRepository


def _database(tmp_path):
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'knowledge.db'}")
    Base.metadata.create_all(engine)
    return session_factory


def _document(tenant_id: str, checksum: str = "a" * 64):
    return KnowledgeDocument(
        tenant_id=tenant_id,
        document_type="policy",
        original_filename="policy.txt",
        media_type="text/plain",
        size_bytes=6,
        checksum=checksum,
        artifact_key="knowledge/doc/file.txt",
        status="uploaded",
    )


def test_cross_tenant_document_lookup_returns_none(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        acme = Tenant(name="Acme")
        globex = Tenant(name="Globex")
        session.add_all([acme, globex])
        session.flush()
        document = _document(acme.id)
        session.add(document)
        session.commit()
        assert KnowledgeRepository(session).get_document(globex.id, document.id) is None


def test_deactivate_removes_recruiting_chunks_from_current_search(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        document = _document(tenant.id)
        session.add(document)
        session.flush()
        session.add(
            RecruitingChunk(
                tenant_id=tenant.id,
                document_id=document.id,
                source_type="knowledge_document",
                source_id=document.id,
                source_version=document.checksum,
                generation=1,
                citation_id="citation-1",
                start_offset=0,
                end_offset=3,
                content="new",
                embedding=[1.0] + [0.0] * 511,
                embedding_model="fake-512-v1",
                is_active=True,
            )
        )
        repository = KnowledgeRepository(session)
        repository.deactivate(document)
        session.commit()
        chunk = session.query(RecruitingChunk).one()
        assert chunk.is_active is False
        assert document.status == "inactive"
        assert document.search_index_status == "inactive"


def test_duplicate_checksum_lookup_is_tenant_and_type_scoped(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        document = _document(tenant.id)
        session.add(document)
        session.commit()
        found = KnowledgeRepository(session).by_checksum(tenant.id, "a" * 64, "policy")
        assert found.id == document.id
