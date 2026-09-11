"""Revision 16 preserves live content and the scope of legacy deletion consent."""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.models.identity import User
from app.security.tokens import Principal
from tests.artifacts.test_privacy_delete import seed_result
from tests.support.migrations import isolated_migration_database, seed_legacy_resume


@pytest.mark.parametrize("artifact_state", ["PENDING", "DELETED"])
def test_migration_preserves_live_data_and_scrubs_existing_deleted_resume_derivatives(postgres_engine, artifact_state):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, artifact, _ = seed_legacy_resume(engine)
        live_tenant, live_owner, _, _ = seed_legacy_resume(engine)
        command.upgrade(config, "20260911_15")
        user = str(uuid4())
        with Session(engine) as session:
            session.add(User(id=user, tenant_id=tenant, email=user + "@test.invalid", password_hash="synthetic"))
            session.commit()
        result_id, feedback_id = seed_result(engine, Principal(user, tenant, Role.ADMIN), owner)
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE resumes SET status='DELETED', profile='{"private":"PRIVATE_SENTINEL"}',
                    extracted_text='PRIVATE_SENTINEL' WHERE id=:id
            """),
                {"id": owner},
            )
            connection.execute(
                text("""
                UPDATE artifacts SET status=:status,sha256=:sha,error_code='storage_unavailable' WHERE id=:id
            """),
                {"id": artifact, "status": artifact_state, "sha": None if artifact_state == "DELETED" else "a" * 64},
            )
            connection.execute(text("CREATE TABLE observed_artifact_states (status varchar(30))"))
            connection.execute(
                text("""
                CREATE FUNCTION capture_artifact_state() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.status IS DISTINCT FROM OLD.status THEN
                    INSERT INTO observed_artifact_states VALUES (NEW.status);
                END IF; RETURN NEW; END $$
            """)
            )
            connection.execute(
                text("""
                CREATE TRIGGER capture_artifact_state AFTER UPDATE ON artifacts
                FOR EACH ROW EXECUTE FUNCTION capture_artifact_state()
            """)
            )
            connection.execute(
                text("""
                INSERT INTO recruiting_chunks(id,tenant_id,source_type,source_id,source_version,generation,
                    citation_id,content,start_offset,end_offset,is_active,created_at,embedding,embedding_model)
                VALUES (:id,:tenant,'resume',:owner,:version,1,:citation,'PRIVATE_SENTINEL',
                    0,16,false,now(),:vector,'fake')
            """),
                {
                    "id": str(uuid4()),
                    "tenant": tenant,
                    "owner": owner,
                    "version": "a" * 64,
                    "citation": str(uuid4()),
                    "vector": "[1," + ",".join(["0"] * 511) + "]",
                },
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            deleted = connection.execute(text("SELECT * FROM resumes WHERE id=:id"), {"id": owner}).mappings().one()
            assert deleted["lifecycle_status"] == "deleted" and deleted["status"] == "FAILED"
            assert deleted["sha256"] is None and deleted["original_filename"] is None
            assert deleted["extracted_text"] is None and deleted["profile"] == {}
            assert (
                connection.scalar(text("SELECT count(*) FROM recruiting_chunks WHERE source_id=:id"), {"id": owner})
                == 0
            )
            assert connection.scalar(text("SELECT summary FROM match_results WHERE id=:id"), {"id": result_id}) is None
            assert connection.scalar(text("SELECT reason FROM feedback WHERE id=:id"), {"id": feedback_id}) is None
            live = connection.execute(text("SELECT * FROM resumes WHERE id=:id"), {"id": live_owner}).mappings().one()
            assert live["tenant_id"] == live_tenant and live["lifecycle_status"] == "active"
            assert live["sha256"] == "a" * 64 and live["original_filename"] == "legacy.txt"
            assert live["extracted_text"] == "legacy" and live["status"] == "SUCCEEDED"
            terminal = connection.execute(
                text("SELECT status,error_code FROM artifacts WHERE id=:id"), {"id": artifact}
            ).one()
            if artifact_state == "DELETED":
                assert tuple(terminal) == ("DELETED", "storage_unavailable")
                assert list(connection.scalars(text("SELECT status FROM observed_artifact_states"))) == []
            else:
                assert tuple(terminal) == ("CLEANUP_PENDING", None)
                assert list(connection.scalars(text("SELECT status FROM observed_artifact_states"))) == [
                    "FAILED",
                    "CLEANUP_PENDING",
                ]
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_17"


def test_legacy_knowledge_broad_redaction_requires_operator_resolution_before_ddl(postgres_engine):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, _, _ = seed_legacy_resume(engine)
        command.upgrade(config, "20260911_15")
        user, document, artifact = (str(uuid4()) for _ in range(3))
        with Session(engine) as session:
            session.add(User(id=user, tenant_id=tenant, email=user + "@test.invalid", password_hash="synthetic"))
            session.commit()
        result_id, _ = seed_result(engine, Principal(user, tenant, Role.ADMIN), owner)
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO artifacts(id,tenant_id,owner_type,owner_id,sha256,media_type,size_bytes,
                    status,created_at,updated_at)
                VALUES (:artifact,:tenant,'knowledge_document',:document,:sha,'text/plain',6,'PENDING',now(),now())
            """),
                {"artifact": artifact, "tenant": tenant, "document": document, "sha": "b" * 64},
            )
            connection.execute(
                text("""
                INSERT INTO knowledge_documents(id,tenant_id,document_type,original_filename,media_type,size_bytes,
                    checksum,artifact_id,status,search_index_status,active_index_generation,created_at,updated_at)
                VALUES (:document,:tenant,'policy','deleted','text/plain',0,:sha,:artifact,
                    'deleted','deleted',0,now(),now())
            """),
                {"artifact": artifact, "tenant": tenant, "document": document, "sha": "b" * 64},
            )
        with pytest.raises(RuntimeError, match="knowledge_privacy_migration_requires_operator_resolution"):
            command.upgrade(config, "head")
        assert "lifecycle_status" not in {c["name"] for c in inspect(engine).get_columns("resumes")}
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT summary FROM match_results WHERE id=:id"), {"id": result_id})
                == "PRIVATE_SENTINEL"
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_15"
