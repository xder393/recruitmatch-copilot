"""PostgreSQL contract for atomic recruiting-index generation replacement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import os
from threading import Barrier

import pytest
from sqlalchemy import Engine, create_engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import JobStatus, ResumeStatus
from app.models.identity import Tenant
from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.models.resumes import Resume
from app.retrieval.generations import (
    FencingNotSupportedError,
    GenerationConflictError,
    GenerationValidationError,
    GenerationWriter,
    IndexFailureCode,
    SourceNotFoundError,
    SourceRef,
    StagedChunk,
)
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from app.retrieval.ports import SearchScope


TENANT = "generation-tenant"
OTHER_TENANT = "generation-other-tenant"
MODEL = "BAAI/bge-small-en-v1.5@512"


def unit_vector(x: float = 1.0, y: float = 0.0) -> list[float]:
    vector = [0.0] * 512
    vector[0] = x
    vector[1] = y
    return vector


def staged_chunk(
    suffix: str,
    *,
    offset: int = 0,
    model: str = MODEL,
    embedding: list[float] | None = None,
    content: str | None = None,
    citation_id: str | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> StagedChunk:
    start = offset if start_offset is None else start_offset
    end = offset + 5 if end_offset is None else end_offset
    return StagedChunk(
        citation_id=f"citation-{suffix}" if citation_id is None else citation_id,
        content=f"text-{suffix}" if content is None else content,
        start_offset=start,
        end_offset=end,
        embedding=unit_vector() if embedding is None else embedding,
        embedding_model=model,
        page_number=1,
        section="skills",
    )


@pytest.fixture(scope="session")
def postgres_engine() -> Engine:
    database_url = os.environ["DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("generation tests require the Compose PostgreSQL database")
    engine = create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(postgres_engine: Engine):
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    with Session(postgres_engine) as session:
        session.execute(delete(RecruitingChunk))
        session.execute(delete(Resume))
        session.execute(delete(JobVersion))
        session.execute(delete(Job))
        session.execute(delete(KnowledgeDocument))
        session.execute(delete(Tenant))
        session.add_all([Tenant(id=TENANT, name="Generation Inc"), Tenant(id=OTHER_TENANT, name="Other Inc")])
        session.commit()
    yield factory
    with Session(postgres_engine) as session:
        session.execute(delete(RecruitingChunk))
        session.execute(delete(Resume))
        session.execute(delete(JobVersion))
        session.execute(delete(Job))
        session.execute(delete(KnowledgeDocument))
        session.execute(delete(Tenant))
        session.commit()


def seed_source(
    factory,
    source_type: str,
    *,
    tenant_id: str = TENANT,
    active_generation: int = 0,
    status: str = "pending",
    deleted: bool = False,
) -> SourceRef:
    with factory() as session:
        if source_type == "resume":
            source = Resume(
                id="resume-generation",
                tenant_id=tenant_id,
                sha256="resume-sha",
                original_filename="resume.pdf",
                media_type="application/pdf",
                size_bytes=10,
                status=ResumeStatus.DELETED if deleted else ResumeStatus.SUCCEEDED,
                profile={},
                deleted_at=datetime.now(timezone.utc) if deleted else None,
                active_index_generation=active_generation,
                search_index_status="deleted" if deleted else status,
            )
            reference = SourceRef(tenant_id, source_type, source.id, source.sha256)
        elif source_type == "job_version":
            job = Job(
                id="job-generation",
                tenant_id=tenant_id,
                title="AI Engineer",
                status=JobStatus.ACTIVE,
                current_version=1,
            )
            source = JobVersion(
                id="job-version-generation",
                job=job,
                version=1,
                jd_text="Build recruiting systems",
                profile={},
                active_index_generation=active_generation,
                search_index_status="deleted" if deleted else status,
            )
            reference = SourceRef(tenant_id, source_type, source.id, str(source.version))
        else:
            source = KnowledgeDocument(
                id="knowledge-generation",
                tenant_id=tenant_id,
                document_type="policy",
                original_filename="policy.txt",
                media_type="text/plain",
                size_bytes=10,
                checksum="knowledge-sha",
                artifact_key="knowledge/policy",
                status="deleted" if deleted else "ready",
                active_index_generation=active_generation,
                search_index_status="deleted" if deleted else status,
            )
            reference = SourceRef(tenant_id, source_type, source.id, source.checksum)
        session.add(source)
        if active_generation:
            session.add(
                RecruitingChunk(
                    tenant_id=tenant_id,
                    source_type=reference.source_type,
                    source_id=reference.source_id,
                    source_version=reference.source_version,
                    generation=active_generation,
                    citation_id=f"old-{source_type}-{active_generation}",
                    start_offset=100,
                    end_offset=105,
                    content="old searchable evidence",
                    embedding=unit_vector(),
                    embedding_model=MODEL,
                    is_active=True,
                )
            )
        session.commit()
    return reference


def source_state(session: Session, reference: SourceRef):
    if reference.source_type == "resume":
        return session.scalar(
            select(Resume).where(
                Resume.tenant_id == reference.tenant_id,
                Resume.id == reference.source_id,
                Resume.sha256 == reference.source_version,
            )
        )
    if reference.source_type == "job_version":
        return session.scalar(
            select(JobVersion)
            .join(Job, Job.id == JobVersion.job_id)
            .where(
                Job.tenant_id == reference.tenant_id,
                JobVersion.id == reference.source_id,
                JobVersion.version == int(reference.source_version),
            )
        )
    return session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == reference.tenant_id,
            KnowledgeDocument.id == reference.source_id,
            KnowledgeDocument.checksum == reference.source_version,
        )
    )


def source_chunks(session: Session, reference: SourceRef) -> list[RecruitingChunk]:
    return list(
        session.scalars(
            select(RecruitingChunk)
            .where(
                RecruitingChunk.tenant_id == reference.tenant_id,
                RecruitingChunk.source_type == reference.source_type,
                RecruitingChunk.source_id == reference.source_id,
                RecruitingChunk.source_version == reference.source_version,
            )
            .order_by(RecruitingChunk.generation, RecruitingChunk.start_offset)
        )
    )


@pytest.mark.parametrize("source_type", ["resume", "job_version", "knowledge_document"])
@pytest.mark.parametrize("active_generation", [0, 1])
def test_first_and_refresh_activation_leave_exactly_one_authoritative_generation(
    session_factory, source_type: str, active_generation: int
) -> None:
    reference = seed_source(
        session_factory,
        source_type,
        active_generation=active_generation,
        status="ready" if active_generation else "pending",
    )
    writer = GenerationWriter(session_factory)
    next_generation = active_generation + 1

    assert (
        writer.stage(
            reference,
            next_generation,
            [staged_chunk(f"{source_type}-a", offset=0), staged_chunk(f"{source_type}-b", offset=10)],
        )
        == 2
    )
    with session_factory() as session:
        staged = [chunk for chunk in source_chunks(session, reference) if chunk.generation == next_generation]
        state = source_state(session, reference)
        assert state.active_index_generation == active_generation
        assert state.search_index_status == ("ready" if active_generation else "pending")
        assert [chunk.is_active for chunk in staged] == [False, False]

    writer.activate(reference, next_generation, expected_count=2, embedding_model=MODEL)

    with session_factory() as session:
        state = source_state(session, reference)
        chunks = source_chunks(session, reference)
        assert state.active_index_generation == next_generation
        assert state.search_index_status == "ready"
        assert state.search_index_error_code is None
        assert state.search_indexed_at is not None
        assert {chunk.generation for chunk in chunks if chunk.is_active} == {next_generation}
        assert len([chunk for chunk in chunks if chunk.is_active]) == 2


@pytest.mark.parametrize(
    "chunks",
    [
        [staged_chunk("mixed-a"), staged_chunk("mixed-b", offset=10, model="other-model@512")],
        [staged_chunk("short-vector", embedding=[1.0, 0.0])],
        [staged_chunk("nan-vector", embedding=unit_vector(math.nan, 0.0))],
        [staged_chunk("unnormalized-vector", embedding=unit_vector(0.5, 0.0))],
        [staged_chunk("blank-content", content="   ")],
        [staged_chunk("blank-citation", citation_id="   ")],
        [staged_chunk("bad-offset", start_offset=5, end_offset=5)],
        [staged_chunk("dup-a", citation_id="duplicate"), staged_chunk("dup-b", offset=10, citation_id="duplicate")],
        [staged_chunk("dup-offset-a"), staged_chunk("dup-offset-b")],
    ],
)
def test_stage_rejects_incomplete_mixed_or_invalid_chunks_without_partial_writes(session_factory, chunks) -> None:
    reference = seed_source(session_factory, "resume")

    with pytest.raises(GenerationValidationError):
        GenerationWriter(session_factory).stage(reference, 1, chunks)

    with session_factory() as session:
        assert source_chunks(session, reference) == []


def test_stage_rejects_wrong_tenant_version_stale_generation_and_privacy_deleted_source(session_factory) -> None:
    reference = seed_source(session_factory, "resume", active_generation=1, status="ready")
    deleted_reference = seed_source(
        session_factory,
        "knowledge_document",
        tenant_id=OTHER_TENANT,
        deleted=True,
    )
    writer = GenerationWriter(session_factory)

    bad_references = [
        SourceRef(OTHER_TENANT, reference.source_type, reference.source_id, reference.source_version),
        SourceRef(reference.tenant_id, reference.source_type, reference.source_id, "wrong-version"),
        SourceRef(reference.tenant_id, reference.source_type, "missing", reference.source_version),
        deleted_reference,
    ]
    for bad_reference in bad_references:
        with pytest.raises(SourceNotFoundError):
            writer.stage(bad_reference, 2 if bad_reference is not deleted_reference else 1, [staged_chunk("bad")])
    with pytest.raises(GenerationConflictError):
        writer.stage(reference, 1, [staged_chunk("stale")])
    with pytest.raises(GenerationConflictError):
        writer.stage(reference, 3, [staged_chunk("skipped")])
    for invalid_generation in (0, -1, 1.5, True):
        with pytest.raises(GenerationValidationError):
            writer.stage(reference, invalid_generation, [staged_chunk("invalid-generation")])  # type: ignore[arg-type]

    with session_factory() as session:
        assert [chunk.generation for chunk in source_chunks(session, reference)] == [1]


def test_activate_rejects_zero_incomplete_mixed_identity_and_noninactive_staging(session_factory) -> None:
    writer = GenerationWriter(session_factory)
    zero_reference = seed_source(session_factory, "resume")
    with pytest.raises(GenerationValidationError):
        writer.activate(zero_reference, 1, expected_count=1, embedding_model=MODEL)

    reference = seed_source(session_factory, "knowledge_document")
    writer.stage(reference, 1, [staged_chunk("only")])
    with pytest.raises(GenerationValidationError):
        writer.activate(reference, 1, expected_count=2, embedding_model=MODEL)
    with pytest.raises(GenerationValidationError):
        writer.activate(reference, 1, expected_count=1, embedding_model="other-model@512")
    with session_factory() as session:
        session.execute(
            update(RecruitingChunk)
            .where(RecruitingChunk.tenant_id == reference.tenant_id, RecruitingChunk.generation == 1)
            .values(is_active=True)
        )
        session.commit()
    with pytest.raises(GenerationValidationError):
        writer.activate(reference, 1, expected_count=1, embedding_model=MODEL)

    with session_factory() as session:
        state = source_state(session, reference)
        assert state.active_index_generation == 0
        assert state.search_index_status == "pending"


def test_failure_after_old_deactivation_rolls_back_chunks_and_source_authority(session_factory) -> None:
    reference = seed_source(session_factory, "resume", active_generation=1, status="ready")

    def injected_failure() -> None:
        raise RuntimeError("injected activation failure")

    writer = GenerationWriter(session_factory, activation_checkpoint=injected_failure)
    writer.stage(reference, 2, [staged_chunk("new")])

    with pytest.raises(RuntimeError, match="injected activation failure"):
        writer.activate(reference, 2, expected_count=1, embedding_model=MODEL)

    with session_factory() as session:
        state = source_state(session, reference)
        chunks = source_chunks(session, reference)
        assert state.active_index_generation == 1
        assert state.search_index_status == "ready"
        assert [(chunk.generation, chunk.is_active) for chunk in chunks] == [(1, True), (2, False)]


def test_privacy_deleted_source_cannot_activate_previously_staged_rows(session_factory) -> None:
    reference = seed_source(session_factory, "resume")
    writer = GenerationWriter(session_factory)
    writer.stage(reference, 1, [staged_chunk("deleted-before-activation")])
    with session_factory() as session:
        session.execute(
            update(Resume)
            .where(Resume.tenant_id == reference.tenant_id, Resume.id == reference.source_id)
            .values(
                status=ResumeStatus.DELETED,
                search_index_status="deleted",
                deleted_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    with pytest.raises(SourceNotFoundError):
        writer.activate(reference, 1, expected_count=1, embedding_model=MODEL)

    with session_factory() as session:
        assert [(chunk.generation, chunk.is_active) for chunk in source_chunks(session, reference)] == [(1, False)]


def test_concurrent_activation_has_one_winner_and_consistent_final_authority(session_factory) -> None:
    reference = seed_source(session_factory, "job_version")
    writer = GenerationWriter(session_factory)
    writer.stage(reference, 1, [staged_chunk("concurrent")])
    barrier = Barrier(2)

    def activate() -> str:
        barrier.wait()
        try:
            writer.activate(reference, 1, expected_count=1, embedding_model=MODEL)
        except GenerationConflictError:
            return "stale"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: activate(), range(2)))

    assert sorted(outcomes) == ["stale", "winner"]
    with session_factory() as session:
        state = source_state(session, reference)
        chunks = source_chunks(session, reference)
        assert state.active_index_generation == 1
        assert state.search_index_status == "ready"
        assert [(chunk.generation, chunk.is_active) for chunk in chunks] == [(1, True)]


@pytest.mark.parametrize(
    ("active_generation", "initial_status", "expected_status"),
    [(0, "pending", "failed"), (1, "ready", "ready")],
)
def test_fail_records_stable_code_and_preserves_generation_and_searchability(
    session_factory, active_generation: int, initial_status: str, expected_status: str
) -> None:
    reference = seed_source(
        session_factory,
        "resume",
        active_generation=active_generation,
        status=initial_status,
    )
    writer = GenerationWriter(session_factory)
    if active_generation:
        writer.stage(reference, 2, [staged_chunk("failed-refresh")])

    with pytest.raises(GenerationValidationError):
        writer.fail(reference, "raw exception text")  # type: ignore[arg-type]

    writer.fail(reference, IndexFailureCode.EMBEDDING_FAILED)

    with session_factory() as session:
        state = source_state(session, reference)
        chunks = source_chunks(session, reference)
        assert state.active_index_generation == active_generation
        assert state.search_index_status == expected_status
        assert state.search_index_error_code == "embedding_failed"
        assert {chunk.generation for chunk in chunks if chunk.is_active} == ({1} if active_generation else set())

    hits = PgVectorRecruitingIndex(session_factory).search(
        SearchScope(
            tenant_id=reference.tenant_id,
            source_types=frozenset({reference.source_type}),
            authorized_sources=frozenset({(reference.source_type, reference.source_id, reference.source_version)}),
        ),
        unit_vector(),
        MODEL,
        top_k=5,
        min_score=0.0,
    )
    assert [hit.citation_id for hit in hits] == ([f"old-resume-{active_generation}"] if active_generation else [])


@pytest.mark.parametrize("operation", ["stage", "activate", "fail"])
def test_non_null_fencing_token_fails_closed_without_mutation(session_factory, operation: str) -> None:
    reference = seed_source(session_factory, "resume")
    writer = GenerationWriter(session_factory)

    with pytest.raises(FencingNotSupportedError):
        if operation == "stage":
            writer.stage(reference, 1, [staged_chunk("fenced")], fencing_token=7)
        elif operation == "activate":
            writer.activate(reference, 1, expected_count=1, embedding_model=MODEL, fencing_token=7)
        else:
            writer.fail(reference, IndexFailureCode.VALIDATION_FAILED, fencing_token=7)

    with session_factory() as session:
        state = source_state(session, reference)
        assert state.active_index_generation == 0
        assert state.search_index_status == "pending"
        assert state.search_index_error_code is None
        assert source_chunks(session, reference) == []


def test_wrong_tenant_or_version_activation_cannot_mutate_the_real_source(session_factory) -> None:
    reference = seed_source(session_factory, "knowledge_document", active_generation=1, status="ready")
    writer = GenerationWriter(session_factory)
    writer.stage(reference, 2, [staged_chunk("isolated")])
    wrong_refs = [
        SourceRef(OTHER_TENANT, reference.source_type, reference.source_id, reference.source_version),
        SourceRef(reference.tenant_id, reference.source_type, reference.source_id, "wrong-version"),
    ]

    for wrong_reference in wrong_refs:
        with pytest.raises(SourceNotFoundError):
            writer.activate(wrong_reference, 2, expected_count=1, embedding_model=MODEL)

    with session_factory() as session:
        state = source_state(session, reference)
        assert state.active_index_generation == 1
        assert [(chunk.generation, chunk.is_active) for chunk in source_chunks(session, reference)] == [
            (1, True),
            (2, False),
        ]
