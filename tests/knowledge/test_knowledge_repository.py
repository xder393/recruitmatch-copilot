from __future__ import annotations

from app.database import Base, create_engine_and_session
from app.knowledge.schemas import ChunkInput
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
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


def test_new_generation_atomically_replaces_old_chunks(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        document = _document(tenant.id)
        session.add(document)
        session.flush()
        repository = KnowledgeRepository(session)
        repository.replace_generation(
            document,
            1,
            [ChunkInput(source_type="policy", content="old", start=0, end=3, page=1, vector=[1.0, 0.0])],
        )
        repository.replace_generation(
            document,
            2,
            [ChunkInput(source_type="policy", content="new", start=0, end=3, page=1, vector=[0.0, 1.0])],
        )
        session.commit()
        active = repository.active_chunks(tenant.id, document.id)
        assert [(item.content, item.generation) for item in active] == [("new", 2)]
        assert document.active_generation == 2


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
