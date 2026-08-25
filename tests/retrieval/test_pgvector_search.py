"""Adversarial PostgreSQL contract for tenant-authorized pgvector retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import ResumeStatus
from app.models.identity import Tenant
from app.models.retrieval import RecruitingChunk
from app.models.resumes import Resume
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from tests.retrieval.test_retrieval_contract import (
    MODEL,
    TENANT,
    RetrievalContract,
    authorized_scope,
    unit_vector,
)


@pytest.fixture(scope="session")
def postgres_engine():
    database_url = os.environ["DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("PostgreSQL retrieval tests require the Compose integration database")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    yield engine
    engine.dispose()


def _resume(
    resume_id: str,
    tenant_id: str,
    source_version: str,
    *,
    active_generation: int,
    deleted: bool = False,
) -> Resume:
    return Resume(
        id=resume_id,
        tenant_id=tenant_id,
        sha256=source_version,
        original_filename=f"{resume_id}.pdf",
        media_type="application/pdf",
        size_bytes=10,
        status=ResumeStatus.DELETED if deleted else ResumeStatus.SUCCEEDED,
        profile={},
        active_index_generation=active_generation,
        search_index_status="deleted" if deleted else "ready",
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )


def _chunk(
    chunk_id: str,
    citation_id: str,
    *,
    tenant_id: str = TENANT,
    source_id: str = "resume-1",
    source_version: str = "resume-v1",
    generation: int = 2,
    active: bool = True,
    model: str = MODEL,
    offset: int,
) -> RecruitingChunk:
    return RecruitingChunk(
        id=chunk_id,
        tenant_id=tenant_id,
        source_type="resume",
        source_id=source_id,
        source_version=source_version,
        generation=generation,
        citation_id=citation_id,
        start_offset=offset,
        end_offset=offset + 1,
        content=f"evidence:{chunk_id}",
        embedding=unit_vector(),
        embedding_model=model,
        is_active=active,
    )


@pytest.fixture
def pg_index(postgres_engine):
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    with Session(postgres_engine) as session:
        session.execute(delete(RecruitingChunk))
        session.execute(delete(Resume))
        session.execute(delete(Tenant))
        session.add_all([Tenant(id=TENANT, name="Acme"), Tenant(id="tenant-b", name="Globex")])
        session.add_all(
            [
                _resume("resume-1", TENANT, "resume-v1", active_generation=2),
                _resume("resume-2", TENANT, "resume-v2", active_generation=2),
                _resume("resume-deleted", TENANT, "resume-deleted-v1", active_generation=1, deleted=True),
                _resume("resume-1-other", "tenant-b", "resume-v1", active_generation=2),
            ]
        )
        session.add_all(
            [
                _chunk("chunk-b", "citation-b", offset=0),
                _chunk("chunk-a", "citation-a", offset=1),
                _chunk("other-tenant", "other-tenant", tenant_id="tenant-b", source_id="resume-1-other", offset=2),
                _chunk(
                    "unauthorized",
                    "unauthorized",
                    source_id="resume-2",
                    source_version="resume-v2",
                    offset=3,
                ),
                _chunk("inactive", "inactive", generation=1, active=False, offset=4),
                _chunk("stale-authority", "stale-authority", generation=1, offset=5),
                _chunk("wrong-model", "wrong-model", model="other-model@512", offset=6),
                _chunk(
                    "privacy-deleted",
                    "privacy-deleted",
                    source_id="resume-deleted",
                    source_version="resume-deleted-v1",
                    generation=1,
                    active=False,
                    offset=7,
                ),
            ]
        )
        session.commit()
    yield PgVectorRecruitingIndex(factory)
    with Session(postgres_engine) as session:
        session.execute(delete(RecruitingChunk))
        session.execute(delete(Resume))
        session.execute(delete(Tenant))
        session.commit()


class TestPgVectorRetrievalContract(RetrievalContract):
    @pytest.fixture(autouse=True)
    def _index(self, pg_index) -> None:
        self.index = pg_index


def test_exact_threshold_boundary_and_hnsw_explain_are_reproducible(pg_index) -> None:
    exact = pg_index.explain_search(authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0)
    hnsw = PgVectorRecruitingIndex(pg_index.session_factory, exact_search_max_candidates=1).explain_search(
        authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0
    )
    assert exact.authorized_candidate_count == 2
    assert exact.strategy == "exact"
    assert "Seq Scan" in exact.plan
    assert hnsw.authorized_candidate_count == 2
    assert hnsw.strategy == "hnsw"
    assert "ix_recruiting_chunk_embedding_hnsw_active" in hnsw.plan


def test_postgresql_search_statement_contains_security_and_distance_predicates(pg_index) -> None:
    evidence = pg_index.explain_search(authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0)

    statement = evidence.statement.lower()
    assert "recruiting_chunks.tenant_id" in statement
    assert "recruiting_chunks.embedding_model" in statement
    assert "recruiting_chunks.is_active" in statement
    assert "active_index_generation" in statement
    assert "source_type" in statement and "source_id" in statement and "source_version" in statement
    assert "<=>" in statement
