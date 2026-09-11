"""Data-preserving local-store cutover with pre-DDL refusal of unsupported data."""

import hashlib

from alembic import command
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.models.retrieval import RecruitingChunk

from tests.support.migrations import isolated_migration_database, seed_legacy_resume


def test_cutover_preserves_valid_artifact_and_source_text(postgres_engine):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, artifact, _ = seed_legacy_resume(engine)
        with engine.connect() as connection:
            before = dict(
                connection.execute(text("SELECT * FROM artifacts WHERE id=:a"), {"a": artifact}).mappings().one()
            )
        command.upgrade(config, "20260911_15")
        with engine.connect() as connection:
            assert (
                dict(connection.execute(text("SELECT * FROM artifacts WHERE id=:a"), {"a": artifact}).mappings().one())
                == before
            )
            assert "extracted_text" in {column["name"] for column in inspect(connection).get_columns("resumes")}
            assert connection.scalar(text("SELECT extracted_text FROM resumes WHERE id=:o"), {"o": owner}) == "legacy"
            assert "resume_artifacts" not in inspect(connection).get_table_names()
            assert "artifact_key" not in {
                column["name"] for column in inspect(connection).get_columns("knowledge_documents")
            }


def test_cutover_rejects_legacy_content_before_ddl(postgres_engine):
    with isolated_migration_database(postgres_engine) as (engine, config):
        _, owner, _, _ = seed_legacy_resume(engine, anchored=False)
        with pytest.raises(RuntimeError, match="artifact_cutover_requires_explicit_reset"):
            command.upgrade(config, "20260911_15")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_14"
            assert (
                connection.scalar(text("SELECT extracted_text FROM resume_artifacts WHERE resume_id=:o"), {"o": owner})
                == "legacy"
            )
            assert "extracted_text" not in {column["name"] for column in inspect(connection).get_columns("resumes")}


@pytest.mark.parametrize("invalid", [False, True])
def test_cutover_preserves_chunk_payload_and_validates_legacy_lineage(postgres_engine, invalid):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, artifact, legacy = seed_legacy_resume(engine)
        with Session(engine) as session:
            chunk = RecruitingChunk(
                tenant_id=tenant,
                source_type="resume",
                source_id=owner,
                source_version="a" * 64,
                document_id="unrelated-artifact" if invalid else legacy,
                generation=1,
                citation_id="synthetic-citation",
                start_offset=0,
                end_offset=6,
                content="legacy",
                embedding=[1.0] + [0.0] * 511,
                embedding_model="synthetic-512",
                is_active=True,
            )
            session.add(chunk)
            session.commit()
        with engine.connect() as connection:
            before = dict(connection.execute(text("SELECT * FROM recruiting_chunks")).mappings().one())
        if invalid:
            with pytest.raises(RuntimeError, match="artifact_cutover_invalid_lineage"):
                command.upgrade(config, "20260911_15")
        else:
            command.upgrade(config, "20260911_15")
        with engine.connect() as connection:
            after = dict(connection.execute(text("SELECT * FROM recruiting_chunks")).mappings().one())
            expected = {**before, "document_id": before["document_id"] if invalid else artifact}
            assert after == expected


def test_inactive_knowledge_without_anchor_is_not_treated_as_privacy_deleted(postgres_engine):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, _, _, _ = seed_legacy_resume(engine)
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO knowledge_documents(id,tenant_id,document_type,original_filename,media_type,size_bytes,
                    checksum,artifact_key,status,active_index_generation,search_index_status,created_at,updated_at)
                VALUES (:t,:t,'policy','private.txt','text/plain',6,:sha,'synthetic/key',
                    'inactive',0,'inactive',now(),now())
            """),
                {"t": tenant, "sha": "b" * 64},
            )
        with pytest.raises(RuntimeError, match="artifact_cutover_requires_explicit_reset"):
            command.upgrade(config, "20260911_15")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_14"
            assert connection.scalar(text("SELECT artifact_key FROM knowledge_documents")) == "synthetic/key"


@pytest.mark.parametrize("sanitized", [True, False])
def test_anchorless_deleted_legacy_source_requires_sanitization(postgres_engine, sanitized):
    with isolated_migration_database(postgres_engine) as (engine, config):
        _, owner, _, _ = seed_legacy_resume(engine, anchored=False)
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE resumes SET status='DELETED',sha256=:sha,original_filename='deleted',
                    size_bytes=0,deleted_at=now()
            """),
                {"sha": hashlib.sha256(("deleted:" + owner).encode()).hexdigest()},
            )
            if sanitized:
                connection.execute(text("UPDATE resume_artifacts SET extracted_text=NULL"))
        if sanitized:
            command.upgrade(config, "20260911_15")
            with engine.connect() as connection:
                row = connection.execute(text("SELECT artifact_id,extracted_text FROM resumes")).one()
                assert tuple(row) == (None, None)
        else:
            with pytest.raises(RuntimeError, match="artifact_cutover_requires_explicit_reset"):
                command.upgrade(config, "20260911_15")


@pytest.mark.parametrize("deleted", [True, False])
@pytest.mark.parametrize("is_active", [True, False])
def test_null_linked_private_chunks_require_retained_source(postgres_engine, deleted, is_active):
    with isolated_migration_database(postgres_engine) as (engine, config):
        tenant, owner, _, _ = seed_legacy_resume(engine, anchored=not deleted)
        if deleted:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                    UPDATE resumes SET status='DELETED',sha256=:sha,original_filename='deleted',
                        size_bytes=0,deleted_at=now() WHERE tenant_id=:t AND id=:o
                """),
                    {"sha": hashlib.sha256(("deleted:" + owner).encode()).hexdigest(), "t": tenant, "o": owner},
                )
                connection.execute(
                    text("UPDATE resume_artifacts SET extracted_text=NULL WHERE resume_id=:o"), {"o": owner}
                )
        with Session(engine) as session:
            session.add(
                RecruitingChunk(
                    tenant_id=tenant,
                    source_type="resume",
                    source_id=owner,
                    source_version="a" * 64,
                    document_id=None,
                    generation=1,
                    citation_id="synthetic-private-citation",
                    start_offset=0,
                    end_offset=20,
                    content="private legacy text\n",
                    embedding=[1.0] + [0.0] * 511,
                    embedding_model="synthetic-512",
                    is_active=is_active,
                )
            )
            session.commit()
        with engine.connect() as connection:
            chunk_before = dict(connection.execute(text("SELECT * FROM recruiting_chunks")).mappings().one())
            source_before = dict(connection.execute(text("SELECT * FROM resumes")).mappings().one())
            legacy_before = dict(connection.execute(text("SELECT * FROM resume_artifacts")).mappings().one())
        if deleted:
            with pytest.raises(RuntimeError, match="artifact_cutover_requires_explicit_reset"):
                command.upgrade(config, "20260911_15")
        else:
            command.upgrade(config, "20260911_15")
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM recruiting_chunks")).mappings().one()) == chunk_before
            if deleted:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_14"
                assert "extracted_text" not in {c["name"] for c in inspect(connection).get_columns("resumes")}
                assert dict(connection.execute(text("SELECT * FROM resumes")).mappings().one()) == source_before
                assert (
                    dict(connection.execute(text("SELECT * FROM resume_artifacts")).mappings().one()) == legacy_before
                )
            else:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260911_15"
                assert connection.scalar(text("SELECT extracted_text FROM resumes")) == "legacy"
