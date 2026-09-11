"""Compare migrated and ORM-created PostgreSQL catalogs, including constraints."""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base
from app.models.knowledge import KnowledgeDocument
from tests.support.migrations import isolated_migration_database, seed_legacy_resume
from app.repositories.artifacts import ArtifactRepository


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
            assert "(tenant_id, artifact_owner_type, id, artifact_id)" in anchors[0][1]
            source_columns = list(
                connection.execute(
                    text("""
                SELECT n.nspname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                       a.attgenerated, pg_get_expr(d.adbin, d.adrelid)
                FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE n.nspname IN ('public', :schema) AND c.relname=:table
                  AND a.attname='artifact_owner_type' ORDER BY n.nspname
            """),
                    {"schema": schema, "table": table},
                )
            )
            assert len(source_columns) == 2
            expected_type = "resume" if table == "resumes" else "knowledge_document"
            assert tuple(source_columns[0][1:]) == tuple(source_columns[1][1:])
            assert tuple(source_columns[0][1:]) == (
                "character varying(18)",
                True,
                "s",
                f"'{expected_type}'::character varying",
            )
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def legacy_database(postgres_engine):
    with isolated_migration_database(postgres_engine) as pair:
        yield pair


def test_revision_12_to_14_preserves_local_sources_without_backfill(legacy_database):
    postgres_engine, config = legacy_database
    tenant_id, _, _, _ = seed_legacy_resume(postgres_engine, anchored=False)
    with postgres_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO knowledge_documents(id,tenant_id,document_type,original_filename,media_type,size_bytes,
                checksum,artifact_key,status,active_index_generation,search_index_status,created_at,updated_at)
            VALUES (:t,:t,'policy','policy.txt','text/plain',6,:sha,:key,'uploaded',0,'pending',now(),now())
        """),
            {"t": tenant_id, "sha": "d" * 64, "key": "legacy/" + tenant_id},
        )
    try:
        command.downgrade(config, "20260824_12")
        command.upgrade(config, "20260911_14")
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
        command.upgrade(config, "20260911_14")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})


def _anchored_sources(engine):
    tenant_id, owner_id, resume_artifact_id, _ = seed_legacy_resume(engine)
    with Session(engine) as session:
        repo = ArtifactRepository(session)
        knowledge_artifact = repo.claim_upload(tenant_id, "knowledge_document", owner_id, "a" * 64, "text/plain", 10)
        session.add(
            KnowledgeDocument(
                id=owner_id,
                tenant_id=tenant_id,
                checksum="a" * 64,
                document_type="policy",
                original_filename="policy.txt",
                media_type="text/plain",
                size_bytes=10,
                artifact_id=knowledge_artifact.id,
                status="uploaded",
            )
        )
        ids = tenant_id, owner_id, resume_artifact_id, knowledge_artifact.id
        session.commit()
        return ids


def test_revision_13_upgrade_preserves_valid_typed_anchors(legacy_database):
    postgres_engine, config = legacy_database
    tenant_id, owner_id, resume_artifact_id, knowledge_artifact_id = _anchored_sources(postgres_engine)
    try:
        command.downgrade(config, "20260824_13")
        command.upgrade(config, "20260911_14")
        with postgres_engine.connect() as connection:
            for table, artifact_id, owner_type in (
                ("resumes", resume_artifact_id, "resume"),
                ("knowledge_documents", knowledge_artifact_id, "knowledge_document"),
            ):
                row = connection.execute(
                    text(f"SELECT artifact_id, artifact_owner_type FROM {table} WHERE tenant_id=:tenant AND id=:owner"),
                    {"tenant": tenant_id, "owner": owner_id},
                ).one()
                assert tuple(row) == (artifact_id, owner_type)
    finally:
        command.upgrade(config, "20260911_14")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})


@pytest.mark.parametrize("table", ["resumes", "knowledge_documents"])
def test_revision_13_invalid_typed_anchor_fails_without_rebinding(legacy_database, table):
    postgres_engine, config = legacy_database
    tenant_id, owner_id, resume_artifact_id, knowledge_artifact_id = _anchored_sources(postgres_engine)
    correct, wrong = (
        (resume_artifact_id, knowledge_artifact_id)
        if table == "resumes"
        else (knowledge_artifact_id, resume_artifact_id)
    )
    try:
        command.downgrade(config, "20260824_13")
        with postgres_engine.begin() as connection:
            connection.execute(
                text(f"UPDATE {table} SET artifact_id=:artifact WHERE id=:owner"),
                {"artifact": wrong, "owner": owner_id},
            )
        with pytest.raises(RuntimeError, match="artifact_owner_type_mismatch: " + table):
            command.upgrade(config, "20260911_14")
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260824_13"
            assert (
                connection.scalar(text(f"SELECT artifact_id FROM {table} WHERE id=:owner"), {"owner": owner_id})
                == wrong
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(f"UPDATE {table} SET artifact_id=:artifact WHERE id=:owner"),
                {"artifact": correct, "owner": owner_id},
            )
        command.upgrade(config, "20260911_14")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})
