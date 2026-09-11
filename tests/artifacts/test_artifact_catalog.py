"""Compare migrated and ORM-created PostgreSQL catalogs, including constraints."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume, ResumeArtifact


def catalog(connection, schema):
    columns = list(
        connection.execute(
            text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE n.nspname=:schema AND c.relname='artifacts' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY a.attname
    """),
            {"schema": schema},
        )
    )
    constraints = list(
        connection.execute(
            text("""
        SELECT c.conname, pg_get_constraintdef(c.oid, true)
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=:schema AND t.relname='artifacts' ORDER BY c.conname
    """),
            {"schema": schema},
        )
    )
    indexes = list(
        connection.execute(
            text("""
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname=:schema AND tablename='artifacts' ORDER BY indexname
    """),
            {"schema": schema},
        )
    )
    return (
        [tuple(row) for row in columns],
        [(name, definition.replace(schema + ".", "")) for name, definition in constraints],
        [(name, definition.replace(schema + ".", "")) for name, definition in indexes],
    )


def test_orm_and_migration_have_identical_postgres_artifact_catalog(postgres_engine):
    schema = "artifact_orm_" + uuid4().hex
    with postgres_engine.begin() as connection:
        migrated = catalog(connection, "public")
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        # checkfirst=False: public tables must not mask the test schema's tables.
        Base.metadata.create_all(connection, checkfirst=False)
        actual = catalog(connection, schema)
        assert actual == migrated
        constraints = dict(actual[1])
        assert "CLEANUP_PENDING" in constraints["ck_artifact_status"]
        assert "CLEANUP_FAILED" in constraints["ck_artifact_status"]
        assert "DELETED" in constraints["ck_artifact_status"]
        assert "QUEUED" not in constraints["ck_artifact_status"]
        index = dict(actual[2])["uq_artifact_active_checksum"]
        assert "(tenant_id, owner_type, sha256)" in index
        assert "PENDING" in index and "AVAILABLE" in index and "FAILED" in index
        assert "CLEANUP" not in index and "DELETED" not in index
        for table in ("resumes", "knowledge_documents"):
            anchors = list(
                connection.execute(
                    text("""
                SELECT n.nspname, pg_get_constraintdef(c.oid, true)
                FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname IN ('public', :schema) AND t.relname=:table
                  AND c.conname=:name ORDER BY n.nspname
            """),
                    {"schema": schema, "table": table, "name": "fk_" + table + "_artifact_owner"},
                )
            )
            assert len(anchors) == 2
            assert anchors[0][1].replace(schema + ".", "").replace("public.", "") == anchors[1][1].replace(
                schema + ".", ""
            ).replace("public.", "")
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_revision_12_to_13_preserves_local_sources_without_backfill(postgres_engine):
    tenant_id = str(uuid4())
    with Session(postgres_engine) as session:
        session.add(Tenant(id=tenant_id, name="legacy-preservation"))
        session.flush()
        resume = Resume(
            tenant_id=tenant_id, sha256="c" * 64, original_filename="legacy.txt", media_type="text/plain", size_bytes=6
        )
        session.add(resume)
        session.flush()
        session.add(ResumeArtifact(resume_id=resume.id, storage_key="legacy/" + resume.id, extracted_text="legacy"))
        document = KnowledgeDocument(
            tenant_id=tenant_id,
            checksum="d" * 64,
            document_type="policy",
            original_filename="policy.txt",
            media_type="text/plain",
            size_bytes=6,
            artifact_key="legacy/" + tenant_id,
            status="uploaded",
        )
        session.add(document)
        session.commit()
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "20260824_12")
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT artifact_id FROM resumes WHERE tenant_id=:tenant"), {"tenant": tenant_id}
                )
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT artifact_id FROM knowledge_documents WHERE tenant_id=:tenant"), {"tenant": tenant_id}
                )
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT artifact_key FROM knowledge_documents WHERE tenant_id=:tenant"), {"tenant": tenant_id}
                )
                == "legacy/" + tenant_id
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT extracted_text FROM resume_artifacts a JOIN resumes r "
                        "ON r.id=a.resume_id WHERE r.tenant_id=:tenant"
                    ),
                    {"tenant": tenant_id},
                )
                == "legacy"
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM artifacts WHERE tenant_id=:tenant"), {"tenant": tenant_id})
                == 0
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})
