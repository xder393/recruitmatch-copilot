"""Lease upgrade preserves source content and recovers pre-lease RUNNING rows."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from tests.support.migrations import isolated_migration_database, seed_legacy_resume


@pytest.mark.parametrize(
    "resume_state,knowledge_state,disposition",
    [
        ("RUNNING", "processing", "claimed"),
        ("QUEUED", "uploaded", "claimed"),
        ("SUCCEEDED", "ready", "terminal"),
        ("FAILED", "failed", "terminal"),
        ("FAILED", "inactive", "terminal"),
    ],
)
def test_lease_upgrade_preserves_data_and_leaves_running_for_explicit_recovery(
    postgres_engine, resume_state, knowledge_state, disposition
):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, artifact, _ = seed_legacy_resume(engine)
        command.upgrade(config, "20260911_17")
        doc, doc_artifact = str(uuid4()), str(uuid4())
        stamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE resumes SET status=:state, updated_at=:stamp WHERE id=:id"),
                {"stamp": stamp, "id": owner, "state": resume_state},
            )
            connection.execute(
                text("""
                INSERT INTO artifacts(id,tenant_id,owner_type,owner_id,sha256,media_type,size_bytes,
                    status,created_at,updated_at)
                VALUES (:a,:t,'knowledge_document',:d,:sha,'text/plain',6,'AVAILABLE',:stamp,:stamp)
            """),
                {"a": doc_artifact, "t": tenant, "d": doc, "sha": "b" * 64, "stamp": stamp},
            )
            connection.execute(
                text("""
                INSERT INTO knowledge_documents(id,tenant_id,document_type,original_filename,media_type,size_bytes,
                    checksum,artifact_id,status,search_index_status,active_index_generation,created_at,updated_at)
                VALUES (:d,:t,'policy','retained.txt','text/plain',6,:sha,:a,:state,'ready',3,:stamp,:stamp)
            """),
                {"a": doc_artifact, "t": tenant, "d": doc, "sha": "b" * 64, "stamp": stamp, "state": knowledge_state},
            )
            snapshots = {
                table: dict(
                    connection.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": key}).mappings().one()
                )
                for table, key in [("resumes", owner), ("knowledge_documents", doc)]
            }
        command.upgrade(config, "head")
        for table, key in [("resumes", owner), ("knowledge_documents", doc)]:
            with engine.connect() as connection:
                row = connection.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": key}).mappings().one()
                assert {name: row[name] for name in snapshots[table]} == snapshots[table]
                assert row["queued_at"] == stamp
                assert (row["processing_attempts"], row["processing_lease_epoch"]) == (0, 0)
                assert row["processing_lease_expires_at"] is None and row["processing_lease_owner"] is None
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260912_18"
            cols = {col["name"]: col for col in inspect(engine).get_columns(table)}
            assert str(cols["processing_lease_epoch"]["type"]) == "BIGINT"
            assert cols["processing_lease_epoch"]["nullable"] is False
        # A normal later claim can recover legacy RUNNING, migration itself did no execution.
        from datetime import timedelta

        with Session(engine) as session:
            repo = SqlAlchemyUnitOfWork(session).leases
            for kind, key in [("resume", owner), ("knowledge_document", doc)]:
                result = repo.claim(tenant, kind, key, "recovery", duration=timedelta(minutes=5))
                assert result.disposition.value == disposition
                if disposition == "claimed":
                    assert result.lease.epoch == 1
            session.rollback()
        command.downgrade(config, "20260911_17")
        assert "processing_lease_epoch" not in {col["name"] for col in inspect(engine).get_columns("resumes")}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT extracted_text FROM resumes WHERE id=:id"), {"id": owner}) == "legacy"
