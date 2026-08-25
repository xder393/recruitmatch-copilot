"""Adversarial PostgreSQL contract for tenant-authorized pgvector retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy import create_engine, delete, event, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import JobStatus, ResumeStatus
from app.models.identity import Tenant
from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.models.resumes import Resume
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from app.retrieval.ports import SearchScope
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
    search_index_status: str = "ready",
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
        search_index_status="deleted" if deleted else search_index_status,
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
    content: str | None = None,
    embedding: list[float] | None = None,
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
        content=f"evidence:{chunk_id}" if content is None else content,
        embedding=unit_vector() if embedding is None else embedding,
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
                _resume(
                    "resume-pending",
                    TENANT,
                    "resume-pending-v1",
                    active_generation=2,
                    search_index_status="pending",
                ),
                _resume(
                    "resume-failed",
                    TENANT,
                    "resume-failed-v1",
                    active_generation=2,
                    search_index_status="failed",
                ),
                _resume("resume-1-other", "tenant-b", "resume-v1", active_generation=2),
            ]
        )
        job = Job(id="job-1", tenant_id=TENANT, title="AI Engineer", status=JobStatus.ACTIVE, current_version=1)
        job_version = JobVersion(
            id="job-version-authority",
            job=job,
            version=1,
            jd_text="Build recruiting AI",
            profile={},
            active_index_generation=1,
            search_index_status="ready",
        )
        knowledge = KnowledgeDocument(
            id="knowledge-1",
            tenant_id=TENANT,
            document_type="policy",
            original_filename="policy.txt",
            media_type="text/plain",
            size_bytes=20,
            checksum="knowledge-v1",
            artifact_key="knowledge/1",
            status="ready",
            active_index_generation=1,
            search_index_status="ready",
        )
        session.add_all([job, job_version, knowledge])
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
                    "pending-source",
                    "pending-source",
                    source_id="resume-pending",
                    source_version="resume-pending-v1",
                    offset=8,
                ),
                _chunk(
                    "failed-source",
                    "failed-source",
                    source_id="resume-failed",
                    source_version="resume-failed-v1",
                    offset=9,
                ),
                _chunk("empty-content", "empty-content", content="", offset=10),
                RecruitingChunk(
                    id="job-authority",
                    tenant_id=TENANT,
                    source_type="job_version",
                    source_id="job-version-authority",
                    source_version="1",
                    generation=1,
                    citation_id="job-authority",
                    start_offset=0,
                    end_offset=1,
                    content="job evidence",
                    embedding=unit_vector(),
                    embedding_model=MODEL,
                    is_active=True,
                ),
                RecruitingChunk(
                    id="knowledge-authority",
                    tenant_id=TENANT,
                    source_type="knowledge_document",
                    source_id="knowledge-1",
                    source_version="knowledge-v1",
                    generation=1,
                    citation_id="knowledge-authority",
                    start_offset=0,
                    end_offset=1,
                    content="knowledge evidence",
                    embedding=unit_vector(),
                    embedding_model=MODEL,
                    is_active=True,
                ),
                _chunk("next-generation", "next-generation", generation=3, offset=11),
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
    with pg_index.session_factory() as session:
        session.add_all(
            [
                _chunk("ann-c", "ann-c", embedding=unit_vector(0.8, 0.6), offset=30),
                _chunk("ann-d", "ann-d", embedding=unit_vector(0.6, 0.8), offset=31),
                _chunk("ann-e", "ann-e", embedding=unit_vector(0.0, 1.0), offset=32),
            ]
        )
        session.commit()

    exact = PgVectorRecruitingIndex(pg_index.session_factory, exact_search_max_candidates=5).explain_search(
        authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0
    )
    hnsw_index = PgVectorRecruitingIndex(
        pg_index.session_factory,
        exact_search_max_candidates=1,
        ann_candidate_multiplier=2,
        ann_candidate_budget_max=3,
    )
    hnsw = hnsw_index.explain_search(authorized_scope(), unit_vector(), MODEL, top_k=2, min_score=0.0)
    hits = hnsw_index.search(authorized_scope(), unit_vector(), MODEL, top_k=2, min_score=0.0)
    assert exact.authorized_candidate_count == 5
    assert exact.strategy == "exact"
    assert "WITH exact_candidates AS MATERIALIZED" in exact.statement
    assert any(
        index_name in exact.plan
        for index_name in (
            "uq_recruiting_chunk_source_generation_offsets",
            "ix_recruiting_chunk_source_active",
            "ix_recruiting_chunk_tenant_type_active",
        )
    )
    assert "ix_recruiting_chunk_embedding_hnsw_active" not in exact.plan
    assert hnsw.authorized_candidate_count == 5
    assert hnsw.strategy == "hnsw"
    assert hnsw.ann_candidate_budget == 3
    assert "LIMIT 3" in hnsw.statement
    assert "ix_recruiting_chunk_embedding_hnsw_active" in hnsw.plan
    assert [hit.id for hit in hits] == ["chunk-a", "chunk-b"]


def test_exact_search_does_not_override_real_planner_settings(pg_index, postgres_engine) -> None:
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(postgres_engine, "before_cursor_execute", capture)
    try:
        hits = pg_index.search(authorized_scope(), unit_vector(), MODEL, top_k=2, min_score=0.0)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture)

    assert [hit.id for hit in hits] == ["chunk-a", "chunk-b"]
    assert not any("enable_indexscan" in statement or "enable_bitmapscan" in statement for statement in statements)


def test_search_uses_one_repeatable_read_snapshot_for_count_and_results(pg_index, postgres_engine) -> None:
    changed_generation = False

    def switch_generation(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        nonlocal changed_generation
        if changed_generation or "count(*)" not in statement.lower() or "recruiting_chunks" not in statement.lower():
            return
        changed_generation = True
        with postgres_engine.begin() as connection:
            connection.execute(update(Resume).where(Resume.id == "resume-1").values(active_index_generation=3))

    event.listen(postgres_engine, "after_cursor_execute", switch_generation)
    try:
        hits = pg_index.search(authorized_scope(), unit_vector(), MODEL, top_k=2, min_score=0.0)
    finally:
        event.remove(postgres_engine, "after_cursor_execute", switch_generation)

    assert changed_generation is True
    assert [hit.id for hit in hits] == ["chunk-a", "chunk-b"]


def test_job_version_and_knowledge_document_authority_are_real_sql_boundaries(pg_index) -> None:
    scope = SearchScope(
        TENANT,
        frozenset({"job_version", "knowledge_document"}),
        frozenset(
            {
                ("job_version", "job-version-authority", "1"),
                ("knowledge_document", "knowledge-1", "knowledge-v1"),
            }
        ),
    )

    hits = pg_index.search(scope, unit_vector(), MODEL, top_k=10, min_score=0.0)

    assert [hit.id for hit in hits] == ["job-authority", "knowledge-authority"]


def test_postgresql_search_statement_contains_security_and_distance_predicates(pg_index) -> None:
    evidence = pg_index.explain_search(authorized_scope(), unit_vector(), MODEL, top_k=10, min_score=0.0)

    statement = evidence.statement.lower()
    assert "recruiting_chunks.tenant_id" in statement
    assert "recruiting_chunks.embedding_model" in statement
    assert "recruiting_chunks.is_active" in statement
    assert "active_index_generation" in statement
    assert "source_type" in statement and "source_id" in statement and "source_version" in statement
    assert "<=>" in statement
