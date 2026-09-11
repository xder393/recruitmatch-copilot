"""PostgreSQL contract for atomic recruiting-index generation replacement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import math
import os
from queue import Queue
from threading import Barrier, Event
import time

import pytest
from sqlalchemy import Engine, create_engine, delete, event, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import JobStatus, ResumeStatus
from app.models.identity import Tenant
from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.models.resumes import Resume
from app.models.artifacts import Artifact
from app.domain.artifacts import ArtifactStatus
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


def persisted_chunks(session: Session, reference: SourceRef) -> list[RecruitingChunk]:
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
        staged = [chunk for chunk in persisted_chunks(session, reference) if chunk.generation == next_generation]
        state = source_state(session, reference)
        assert state.active_index_generation == active_generation
        assert state.search_index_status == ("ready" if active_generation else "pending")
        assert [chunk.is_active for chunk in staged] == [False, False]

    writer.activate(reference, next_generation, expected_count=2, embedding_model=MODEL)

    with session_factory() as session:
        state = source_state(session, reference)
        chunks = persisted_chunks(session, reference)
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
        assert persisted_chunks(session, reference) == []


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
        assert [chunk.generation for chunk in persisted_chunks(session, reference)] == [1]


@pytest.mark.parametrize(
    ("source_type", "document_mode", "expected_document_id"),
    [
        ("knowledge_document", "none", "knowledge-generation"),
        ("knowledge_document", "source", "knowledge-generation"),
        ("job_version", "none", None),
        ("resume", "none", None),
        ("resume", "owned-artifact", "artifact-owned"),
    ],
)
def test_stage_normalizes_only_source_owned_document_cleanup_keys(
    session_factory, source_type: str, document_mode: str, expected_document_id: str | None
) -> None:
    reference = seed_source(session_factory, source_type)
    if source_type == "resume":
        with session_factory() as session:
            session.add(
                Artifact(
                    id="artifact-owned",
                    tenant_id=reference.tenant_id,
                    owner_type="resume",
                    owner_id=reference.source_id,
                    sha256="a" * 64,
                    media_type="text/plain",
                    size_bytes=10,
                    status=ArtifactStatus.AVAILABLE,
                )
            )
            session.flush()
            session.get(Resume, reference.source_id).artifact_id = "artifact-owned"
            session.commit()
    document_id = {
        "none": None,
        "source": reference.source_id,
        "owned-artifact": "artifact-owned",
    }[document_mode]

    GenerationWriter(session_factory).stage(
        reference,
        1,
        [replace(staged_chunk(f"document-{source_type}"), document_id=document_id)],
    )

    with session_factory() as session:
        chunks = persisted_chunks(session, reference)
        assert len(chunks) == 1
        assert chunks[0].document_id == expected_document_id


@pytest.mark.parametrize(
    ("source_type", "document_id"),
    [
        ("knowledge_document", "another-knowledge-document"),
        ("job_version", "job-artifact-not-supported"),
        ("resume", "artifact-owned-by-another-resume"),
    ],
)
def test_stage_rejects_document_cleanup_keys_not_owned_by_the_locked_source(
    session_factory, source_type: str, document_id: str
) -> None:
    reference = seed_source(session_factory, source_type)
    if source_type == "resume":
        with session_factory() as session:
            other_resume = Resume(
                id="other-resume",
                tenant_id=TENANT,
                sha256="other-resume-sha",
                original_filename="other.pdf",
                media_type="application/pdf",
                size_bytes=10,
                status=ResumeStatus.SUCCEEDED,
                profile={},
            )
            session.add(other_resume)
            session.add(
                Artifact(
                    id=document_id,
                    tenant_id=TENANT,
                    owner_type="resume",
                    owner_id=other_resume.id,
                    sha256="b" * 64,
                    media_type="text/plain",
                    size_bytes=10,
                    status=ArtifactStatus.AVAILABLE,
                )
            )
            session.flush()
            other_resume.artifact_id = document_id
            session.commit()

    with pytest.raises(GenerationValidationError, match="document"):
        GenerationWriter(session_factory).stage(
            reference,
            1,
            [replace(staged_chunk(f"invalid-document-{source_type}"), document_id=document_id)],
        )

    with session_factory() as session:
        assert persisted_chunks(session, reference) == []


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
        chunks = persisted_chunks(session, reference)
        assert state.active_index_generation == 1
        assert state.search_index_status == "ready"
        assert [(chunk.generation, chunk.is_active) for chunk in chunks] == [(1, True), (2, False)]


def test_exact_staging_replay_resumes_activation_without_duplicate_citations(session_factory) -> None:
    reference = seed_source(session_factory, "resume")
    attempts = 0

    def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient activation failure")

    writer = GenerationWriter(session_factory, activation_checkpoint=fail_once)
    chunks = [staged_chunk("retry")]
    assert writer.stage(reference, 1, chunks) == 1
    with pytest.raises(RuntimeError, match="transient activation"):
        writer.activate(reference, 1, expected_count=1, embedding_model=MODEL)

    assert writer.stage(reference, 1, chunks) == 1
    writer.activate(reference, 1, expected_count=1, embedding_model=MODEL)

    with session_factory() as session:
        persisted = persisted_chunks(session, reference)
        assert len(persisted) == 1
        assert persisted[0].citation_id == "citation-retry"
        assert persisted[0].is_active is True
        assert source_state(session, reference).active_index_generation == 1


def test_partial_or_mismatched_staging_replay_fails_closed(session_factory) -> None:
    reference = seed_source(session_factory, "knowledge_document")
    writer = GenerationWriter(session_factory)
    writer.stage(reference, 1, [staged_chunk("one")])

    with pytest.raises(GenerationValidationError, match="partial or mismatched"):
        writer.stage(reference, 1, [staged_chunk("one"), staged_chunk("two", offset=10)])
    with pytest.raises(GenerationValidationError, match="partial or mismatched"):
        writer.stage(reference, 1, [staged_chunk("one", content="changed")])


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
        assert [(chunk.generation, chunk.is_active) for chunk in persisted_chunks(session, reference)] == [(1, False)]


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
        chunks = persisted_chunks(session, reference)
        assert state.active_index_generation == 1
        assert state.search_index_status == "ready"
        assert [(chunk.generation, chunk.is_active) for chunk in chunks] == [(1, True)]


def test_concurrent_staging_maps_only_tenant_citation_conflict_to_stable_validation_error(
    postgres_engine: Engine, session_factory
) -> None:
    resume = seed_source(session_factory, "resume")
    knowledge = seed_source(session_factory, "knowledge_document")
    writer = GenerationWriter(session_factory)
    stage_start = Barrier(2)
    citation_precheck = Barrier(2)

    def synchronize_citation_prechecks(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().startswith("SELECT") and "recruiting_chunks.citation_id IN" in statement:
            citation_precheck.wait(timeout=5)

    def stage(reference: SourceRef, suffix: str) -> tuple[str, str]:
        stage_start.wait()
        try:
            writer.stage(reference, 1, [staged_chunk(suffix, citation_id="shared-tenant-citation")])
        except GenerationValidationError as exc:
            return "validation", str(exc)
        except Exception as exc:  # capture unexpected DB leakage as observable test evidence
            return "unexpected", f"{type(exc).__name__}: {exc}"
        return "winner", ""

    event.listen(postgres_engine, "before_cursor_execute", synchronize_citation_prechecks)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda item: stage(*item),
                    [(resume, "resume-collision"), (knowledge, "knowledge-collision")],
                )
            )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", synchronize_citation_prechecks)

    assert sorted(outcomes) == [
        ("validation", "citation ID already exists for this tenant"),
        ("winner", ""),
    ]
    with session_factory() as session:
        chunks = list(
            session.scalars(
                select(RecruitingChunk).where(
                    RecruitingChunk.tenant_id == TENANT,
                    RecruitingChunk.citation_id == "shared-tenant-citation",
                )
            )
        )
        assert len(chunks) == 1
        assert chunks[0].is_active is False
        assert source_state(session, resume).active_index_generation == 0
        assert source_state(session, knowledge).active_index_generation == 0


@pytest.mark.parametrize("activation_fails", [False, True])
def test_job_activation_locks_tenant_authority_until_commit_or_rollback(
    postgres_engine: Engine, session_factory, activation_fails: bool
) -> None:
    reference = seed_source(session_factory, "job_version")
    activation_paused = Event()
    release_activation = Event()

    def checkpoint() -> None:
        activation_paused.set()
        assert release_activation.wait(timeout=5)
        if activation_fails:
            raise RuntimeError("rollback activation after both Source rows are locked")

    writer = GenerationWriter(session_factory, activation_checkpoint=checkpoint)
    writer.stage(reference, 1, [staged_chunk("job-lock")])

    mover_pid: Queue[int] = Queue()

    def move_job_tenant_then_rollback() -> None:
        with session_factory() as session:
            session.begin()
            mover_pid.put(int(session.scalar(select(text("pg_backend_pid()"))) or 0))
            session.execute(
                update(Job).where(Job.id == "job-generation", Job.tenant_id == TENANT).values(tenant_id=OTHER_TENANT)
            )
            session.rollback()

    with ThreadPoolExecutor(max_workers=2) as executor:
        activation = executor.submit(
            writer.activate,
            reference,
            1,
            expected_count=1,
            embedding_model=MODEL,
        )
        assert activation_paused.wait(timeout=5)
        mover = executor.submit(move_job_tenant_then_rollback)
        pid = mover_pid.get(timeout=5)

        deadline = time.monotonic() + 5
        blocked_on_job_lock = False
        while time.monotonic() < deadline:
            with postgres_engine.connect() as connection:
                blocked_on_job_lock = (
                    connection.execute(
                        select(text("wait_event_type")).select_from(text("pg_stat_activity")).where(text("pid = :pid")),
                        {"pid": pid},
                    ).scalar_one_or_none()
                    == "Lock"
                )
            if blocked_on_job_lock:
                break
            if mover.done():
                break
            time.sleep(0.01)
        assert blocked_on_job_lock, "Job tenant update was not blocked by activation's Source authority lock"
        release_activation.set()
        if activation_fails:
            with pytest.raises(RuntimeError, match="rollback activation"):
                activation.result(timeout=5)
        else:
            activation.result(timeout=5)
        mover.result(timeout=5)

    with session_factory() as session:
        job_tenant = session.scalar(select(Job.tenant_id).where(Job.id == "job-generation"))
        state = source_state(session, reference)
        chunks = persisted_chunks(session, reference)
        assert job_tenant == TENANT
        assert state.active_index_generation == (0 if activation_fails else 1)
        assert state.search_index_status == ("pending" if activation_fails else "ready")
        assert [(chunk.tenant_id, chunk.generation, chunk.is_active) for chunk in chunks] == [
            (TENANT, 1, not activation_fails)
        ]


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
        chunks = persisted_chunks(session, reference)
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
        assert persisted_chunks(session, reference) == []


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
        assert [(chunk.generation, chunk.is_active) for chunk in persisted_chunks(session, reference)] == [
            (1, True),
            (2, False),
        ]
