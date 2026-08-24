"""Executable PostgreSQL catalog contract for the pgvector schema."""

from __future__ import annotations

from collections.abc import Mapping
import os
import subprocess
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from app.database_migrations import upgrade_database


SOURCE_STATE_COLUMNS = {
    "active_index_generation": "integer",
    "search_index_status": "character varying",
    "search_index_error_code": "character varying",
    "search_indexed_at": "timestamp with time zone",
}


def _columns(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table
                """
            ),
            {"table": table},
        )
    return {row.column_name: row.data_type for row in rows}


def _indexes(engine: Engine) -> Mapping[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'recruiting_chunks'
                """
            )
        )
    return {row.indexname: row.indexdef for row in rows}


def _constraints(engine: Engine) -> Mapping[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT catalog_constraint.conname,
                       pg_get_constraintdef(catalog_constraint.oid, true) AS definition
                FROM pg_constraint AS catalog_constraint
                WHERE catalog_constraint.conrelid = 'recruiting_chunks'::regclass
                  AND catalog_constraint.contype IN ('u', 'c')
                """
            )
        )
    return {row.conname: " ".join(row.definition.split()) for row in rows}


def test_bootstrap_is_idempotent_on_one_pgvector_alembic_head(postgres_engine: Engine) -> None:
    subprocess.run([sys.executable, "scripts/bootstrap.py"], check=True)
    subprocess.run([sys.executable, "scripts/bootstrap.py"], check=True)

    with postgres_engine.connect() as connection:
        heads = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        template_count = connection.scalar(text("SELECT count(*) FROM job_templates"))

    assert heads == ["20260824_11"]
    assert template_count == 30


def test_explicit_and_environment_urls_target_postgresql(postgres_engine: Engine, monkeypatch) -> None:
    postgres_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/must-not-run-pgvector.db")
    upgrade_database(postgres_url)

    monkeypatch.setenv("DATABASE_URL", postgres_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite:////tmp/must-not-run-pgvector.ini.db")
    command.upgrade(config, "head")

    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == ["20260824_11"]


def test_pgvector_extension_and_recruiting_chunk_shape(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        extension_version = connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        embedding_type = connection.scalar(
            text(
                """
                SELECT format_type(attribute.atttypid, attribute.atttypmod)
                FROM pg_attribute AS attribute
                JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname = 'recruiting_chunks'
                  AND attribute.attname = 'embedding'
                  AND NOT attribute.attisdropped
                """
            )
        )

    assert extension_version is not None
    assert tuple(int(part) for part in extension_version.split(".")[:2]) >= (0, 8)
    assert embedding_type == "vector(512)"

    assert _columns(postgres_engine, "recruiting_chunks") == {
        "id": "character varying",
        "tenant_id": "character varying",
        "document_id": "character varying",
        "source_type": "character varying",
        "source_id": "character varying",
        "source_version": "character varying",
        "generation": "integer",
        "citation_id": "character varying",
        "page_number": "integer",
        "section": "character varying",
        "start_offset": "integer",
        "end_offset": "integer",
        "content": "text",
        "embedding": "USER-DEFINED",
        "embedding_model": "character varying",
        "is_active": "boolean",
        "created_at": "timestamp with time zone",
    }


def test_all_sources_own_consistent_index_state(postgres_engine: Engine) -> None:
    for table in ("resumes", "job_versions", "knowledge_documents"):
        columns = _columns(postgres_engine, table)
        assert {name: columns.get(name) for name in SOURCE_STATE_COLUMNS} == SOURCE_STATE_COLUMNS


def test_recruiting_chunk_constraints_and_indexes(postgres_engine: Engine) -> None:
    assert _constraints(postgres_engine) == {
        "uq_recruiting_chunk_tenant_citation": "UNIQUE (tenant_id, citation_id)",
        "uq_recruiting_chunk_source_generation_offsets": (
            "UNIQUE (tenant_id, source_type, source_id, source_version, generation, start_offset, end_offset)"
        ),
        "ck_recruiting_chunk_source_type": (
            "CHECK (source_type::text = ANY (ARRAY['resume'::character varying, "
            "'job_version'::character varying, 'knowledge_document'::character varying]::text[]))"
        ),
        "ck_recruiting_chunk_generation_positive": "CHECK (generation > 0)",
        "ck_recruiting_chunk_offsets": "CHECK (start_offset >= 0 AND end_offset > start_offset)",
    }

    indexes = _indexes(postgres_engine)
    assert set(indexes) >= {
        "ix_recruiting_chunk_tenant_type_active",
        "ix_recruiting_chunk_source_active",
        "ix_recruiting_chunk_document_generation_active",
        "ix_recruiting_chunk_embedding_hnsw_active",
    }
    assert "(tenant_id, source_type, is_active)" in indexes["ix_recruiting_chunk_tenant_type_active"]
    assert (
        "(tenant_id, source_type, source_id, source_version, is_active)" in indexes["ix_recruiting_chunk_source_active"]
    )
    assert (
        "(tenant_id, document_id, generation, is_active) WHERE (document_id IS NOT NULL)"
        in indexes["ix_recruiting_chunk_document_generation_active"]
    )
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_recruiting_chunk_embedding_hnsw_active"]
    assert "WHERE (is_active = true)" in indexes["ix_recruiting_chunk_embedding_hnsw_active"]
