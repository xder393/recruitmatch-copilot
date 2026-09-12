"""Processor-level ownership races, using real PostgreSQL transactions."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import ResumeStatus
from app.models.identity import Tenant
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.resumes.parser import HeuristicResumeParser
from app.services.resume_processing import ResumeProcessingService
from app.services.knowledge_processing import KnowledgeProcessingService
from app.retrieval.generations import GenerationWriter, SourceRef
from app.retrieval.indexing import SourceIndexer
from tests.fakes.artifacts import FakeArtifactStore
from tests.support.artifacts import attach_artifact
from tests.support.application import DeterministicEmbeddingAdapter


@pytest.fixture(params=["resume", "knowledge_document"])
def processing_source(postgres_engine, request):
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    store = FakeArtifactStore()
    kind = request.param
    model = Resume if kind == "resume" else KnowledgeDocument
    tenant_id, source_id = str(uuid4()), str(uuid4())
    with factory() as session:
        session.add(Tenant(id=tenant_id, name="Processor race"))
        session.flush()
        fields = dict(
            id=source_id, tenant_id=tenant_id, original_filename="source.txt", media_type="text/plain", size_bytes=16
        )
        row = (
            Resume(**fields, status=ResumeStatus.QUEUED)
            if kind == "resume"
            else KnowledgeDocument(**fields, document_type="policy", status="uploaded")
        )
        session.add(row)
        attach_artifact(session, row, b"Python developer", owner_type=kind, store=store)
        session.commit()
        version = row.sha256 if kind == "resume" else row.checksum
    yield factory, store, SourceRef(tenant_id, kind, source_id, version), model
    with Session(postgres_engine) as session:
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()


def processor(data, *, parser=None, embedder=None):
    factory, store, source, _ = data
    indexer = SourceIndexer(GenerationWriter(factory), embedder or DeterministicEmbeddingAdapter())
    uows = SqlAlchemyUnitOfWorkFactory(factory)
    if source.source_type == "resume":
        return ResumeProcessingService(uows, store, parser or HeuristicResumeParser(), indexer)
    return KnowledgeProcessingService(uows, store, indexer)


def test_expired_worker_cannot_publish_after_takeover(processing_source):
    data = processing_source
    factory, _, source, model = data

    class TakeoverEmbedder(DeterministicEmbeddingAdapter):
        def embed_documents(self, texts):
            with factory() as session:
                row = session.get(model, source.source_id)
                row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
                session.commit()
            processor(data).process(source.tenant_id, source.source_id)
            raise RuntimeError("old worker must not publish failure")

    processor(data, embedder=TakeoverEmbedder()).process(source.tenant_id, source.source_id)
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.search_index_error_code is None
        assert row.active_index_generation == 1
        assert row.processing_lease_epoch == 2
        assert row.processing_attempts == 2
        assert row.status == (ResumeStatus.SUCCEEDED if source.source_type == "resume" else "ready")
        assert row.error_code is None
        chunks = list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.tenant_id == source.tenant_id)))
        assert len(chunks) == 1 and chunks[0].is_active


def test_live_duplicate_is_noop_with_no_attempt_increment(processing_source):
    factory, _, source, model = processing_source
    with SqlAlchemyUnitOfWorkFactory(factory)() as uow:
        uow.leases.claim(
            source.tenant_id, source.source_type, source.source_id, "other-worker", duration=timedelta(minutes=5)
        )
        uow.commit()
    result = processor(processing_source).process(source.tenant_id, source.source_id)
    assert result.value == "duplicate_active"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.processing_attempts == 1 and row.processing_lease_epoch == 1


@pytest.mark.parametrize("operation", ["stage", "activate", "fail"])
def test_unfenced_async_writes_are_rejected(processing_source, operation):
    factory, _, source, _ = processing_source
    from app.knowledge.chunking import chunk_document
    from app.processing.outcomes import LeaseOwnershipLost
    from app.retrieval.generations import IndexFailureCode

    with pytest.raises(LeaseOwnershipLost):
        writer = GenerationWriter(factory)
        if operation == "stage":
            SourceIndexer(writer, DeterministicEmbeddingAdapter()).index(source, 1, chunk_document("Python developer"))
        elif operation == "activate":
            writer.activate(source, 1, expected_count=1, embedding_model="fake")
        else:
            writer.fail(source, IndexFailureCode.EMBEDDING_FAILED)


def claim(data, owner="worker-a"):
    factory, _, source, _ = data
    with SqlAlchemyUnitOfWorkFactory(factory)() as uow:
        lease = uow.leases.claim(
            source.tenant_id, source.source_type, source.source_id, owner, duration=timedelta(minutes=5)
        ).lease
        uow.commit()
    return lease


def test_takeover_reconciles_only_unpublished_staging(processing_source):
    from app.processing.outcomes import LeaseOwnershipLost
    from app.retrieval.generations import StagedChunk, GenerationValidationError

    factory, _, source, model = processing_source
    old = claim(processing_source)
    writer = GenerationWriter(factory)
    chunk = StagedChunk("history", "Python", 0, 6, [1.0] + [0.0] * 511, "fake")
    writer.stage(source, 1, [chunk], fencing_token=old)
    writer.activate(source, 1, expected_count=1, embedding_model="fake", fencing_token=old)
    from dataclasses import replace

    abandoned = replace(chunk, citation_id="abandoned")
    writer.stage(source, 2, [abandoned], fencing_token=old)
    with pytest.raises(GenerationValidationError):
        writer.stage(source, 2, [replace(abandoned, content="changed")], fencing_token=old)
    with factory() as session:
        session.get(model, source.source_id).processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        session.commit()
    current = claim(processing_source, "worker-b")
    with pytest.raises(LeaseOwnershipLost):
        writer.reconcile_staging(source, 2, fencing_token=old)
    with pytest.raises(LeaseOwnershipLost):
        writer.stage(source, 2, [abandoned], fencing_token=old)
    with pytest.raises(LeaseOwnershipLost):
        writer.activate(source, 2, expected_count=1, embedding_model="fake", fencing_token=old)
    from app.retrieval.generations import IndexFailureCode

    with pytest.raises(LeaseOwnershipLost):
        writer.fail(source, IndexFailureCode.EMBEDDING_FAILED, fencing_token=old)
    assert writer.reconcile_staging(source, 2, fencing_token=current) == 1
    replacement = replace(chunk, citation_id="current", embedding=[0.0, 1.0] + [0.0] * 510)
    writer.stage(source, 2, [replacement], fencing_token=current)
    writer.activate(source, 2, expected_count=1, embedding_model="fake", fencing_token=current)
    with factory() as session:
        chunks = list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.tenant_id == source.tenant_id)))
        assert {(item.citation_id, item.generation, item.is_active) for item in chunks} == {
            ("history", 1, False),
            ("current", 2, True),
        }


@pytest.mark.parametrize("reason", ["reference", "limit", "active"])
def test_reconciliation_fails_closed(processing_source, monkeypatch, reason):
    from app.retrieval.generations import StagedChunk, GenerationValidationError
    from app.models.jobs import Job, JobVersion
    from app.models.matching import MatchRun, MatchResult

    factory, _, source, _ = processing_source
    lease = claim(processing_source)
    writer = GenerationWriter(factory)
    writer.stage(source, 1, [StagedChunk("reserved", "Python", 0, 6, [1.0] + [0.0] * 511, "fake")], fencing_token=lease)
    with factory() as session:
        if reason == "active":
            session.scalar(
                select(RecruitingChunk).where(RecruitingChunk.tenant_id == source.tenant_id)
            ).is_active = True
        elif reason == "reference":
            resume = (
                session.get(Resume, source.source_id)
                if source.source_type == "resume"
                else Resume(tenant_id=source.tenant_id, media_type="text/plain", size_bytes=1)
            )
            if source.source_type != "resume":
                session.add(resume)
            job = Job(tenant_id=source.tenant_id, title="test")
            session.add(job)
            session.flush()
            version = JobVersion(job_id=job.id, version=1, jd_text="test")
            run = MatchRun(tenant_id=source.tenant_id, resume_id=resume.id)
            session.add_all([version, run])
            session.flush()
            session.add(
                MatchResult(
                    run_id=run.id,
                    job_version_id=version.id,
                    rank=1,
                    total_score=0,
                    interview_questions=[{"citation_ids": ["reserved"]}],
                )
            )
        else:
            monkeypatch.setattr(GenerationWriter, "MAX_RECONCILE_CHUNKS", 0, raising=False)
        session.commit()
    with pytest.raises(GenerationValidationError, match="staging_reconciliation"):
        writer.reconcile_staging(source, 1, fencing_token=lease)
    with factory() as session:
        assert (
            session.scalar(select(RecruitingChunk.citation_id).where(RecruitingChunk.tenant_id == source.tenant_id))
            == "reserved"
        )


@pytest.mark.parametrize("processing_source", ["resume"], indirect=True)
def test_stale_parser_cannot_overwrite_profile_or_publish_model_trace(processing_source):
    from app.ai.resume_parser import LLMResumeParser
    from app.models.operations import ModelTrace
    from tests.ai.fakes import FakeStructuredModel

    factory, store, source, model = processing_source
    traced = LLMResumeParser(
        FakeStructuredModel(
            {"resume_extract": {"skills": [{"name": "Python", "evidence": {"start": 0, "end": 6, "text": "Python"}}]}}
        ),
        HeuristicResumeParser(),
    )

    class OldParser:
        def parse_with_metadata(self, text):
            with factory() as session:
                row = session.get(model, source.source_id)
                row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
                session.commit()
            ResumeProcessingService(SqlAlchemyUnitOfWorkFactory(factory), store, traced).process(
                source.tenant_id, source.source_id
            )
            result = traced.parse_with_metadata(text)
            result.profile.experience_years = 99
            return result

    result = ResumeProcessingService(SqlAlchemyUnitOfWorkFactory(factory), store, OldParser()).process(
        source.tenant_id, source.source_id
    )
    assert result.value == "lease_lost"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.profile["experience_years"] is None
        assert row.profile["skills"][0]["name"] == "Python"
        assert row.processing_lease_epoch == 2 and row.error_code is None
        assert len(list(session.scalars(select(ModelTrace).where(ModelTrace.tenant_id == source.tenant_id)))) == 1


def test_expiry_during_activation_rolls_back_entire_processor_publication(processing_source):
    from app.models.operations import ModelTrace
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, _, source, model = processing_source

    def publication_factory():
        session = factory()
        uow = SqlAlchemyUnitOfWork(session, owns_session=True)

        def expire_during_activation():
            session.get(model, source.source_id).processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )

        uow.generations.activation_checkpoint = expire_during_activation
        return uow

    service = processor(processing_source)
    service.uow_factory = publication_factory
    assert service.process(source.tenant_id, source.source_id).value == "lease_lost"
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.active_index_generation == 0
        assert row.status == (ResumeStatus.RUNNING if source.source_type == "resume" else "processing")
        if source.source_type == "resume":
            assert row.profile == {} and row.extracted_text is None
        assert not list(session.scalars(select(ModelTrace).where(ModelTrace.tenant_id == source.tenant_id)))
        chunks = list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.tenant_id == source.tenant_id)))
        assert len(chunks) == 1 and not chunks[0].is_active


def test_staging_reclamation_rolls_back_when_lease_expires_before_commit(processing_source, postgres_engine):
    from sqlalchemy import event, update
    from app.processing.outcomes import LeaseOwnershipLost
    from app.retrieval.generations import StagedChunk

    factory, _, source, model = processing_source
    lease = claim(processing_source)
    writer = GenerationWriter(factory)
    writer.stage(source, 1, [StagedChunk("reserved", "Python", 0, 6, [1.0] + [0.0] * 511, "fake")], fencing_token=lease)

    def expire_after_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM recruiting_chunks"):
            connection.execute(
                update(model)
                .where(model.id == source.source_id)
                .values(processing_lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )

    event.listen(postgres_engine, "after_cursor_execute", expire_after_delete)
    try:
        with pytest.raises(LeaseOwnershipLost):
            writer.reconcile_staging(source, 1, fencing_token=lease)
    finally:
        event.remove(postgres_engine, "after_cursor_execute", expire_after_delete)
    with factory() as session:
        assert (
            session.scalar(select(RecruitingChunk.citation_id).where(RecruitingChunk.tenant_id == source.tenant_id))
            == "reserved"
        )


@pytest.mark.parametrize("processing_source", ["resume"], indirect=True)
def test_trace_storage_failure_does_not_masquerade_as_soft_index_failure(processing_source):
    from app.ai.resume_parser import LLMResumeParser
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
    from app.models.operations import ModelTrace
    from tests.ai.fakes import FakeStructuredModel

    factory, _, source, model = processing_source
    parser = LLMResumeParser(FakeStructuredModel({"resume_extract": {"skills": []}}), HeuristicResumeParser())
    failed = False

    def uows():
        session = factory()
        uow = SqlAlchemyUnitOfWork(session, owns_session=True)
        real_write = uow.model_traces.succeeded

        def write(*args, **kwargs):
            nonlocal failed
            real_write(*args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("synthetic trace storage failure")

        uow.model_traces.succeeded = write
        return uow

    service = processor(processing_source, parser=parser)
    service.uow_factory = uows
    with pytest.raises(RuntimeError, match="synthetic trace storage failure"):
        service.process(source.tenant_id, source.source_id)
    with factory() as session:
        row = session.get(model, source.source_id)
        assert row.profile == {} and row.active_index_generation == 0
        assert row.search_index_error_code is None
        assert not list(session.scalars(select(ModelTrace).where(ModelTrace.tenant_id == source.tenant_id)))
