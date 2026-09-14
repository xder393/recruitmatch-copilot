"""Recovery exercises real PostgreSQL state without executing model work."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from uuid import uuid4
import pytest

from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import ResumeStatus
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.processing.test_leases import source as source, committed_lease, expire
from tests.integration.processing.test_processors import processing_source as processing_source, processor, claim


class RecordingDispatcher:
    def __init__(self, failure=None):
        self.deliveries = []
        self.failure = failure

    def dispatch_resume(self, tenant, source):
        self.deliveries.append((tenant, "resume", source))
        if self.failure:
            self.failure()

    def dispatch_knowledge(self, tenant, source):
        self.deliveries.append((tenant, "knowledge_document", source))
        if self.failure:
            self.failure()


def scanner(engine, dispatcher):
    from app.processing.recovery import RecoveryScanner
    from app.processing.recovery_repository import RecoveryRepository

    return RecoveryScanner(RecoveryRepository(sessionmaker(engine)), dispatcher)


def test_expired_recovery_commits_queue_before_publish_and_preserves_budget(postgres_engine, source):
    committed_lease(postgres_engine, source)
    expire(postgres_engine, source)

    def observe():
        with Session(postgres_engine) as session:
            row = session.get(source[3], source[2])
            assert row.status == (ResumeStatus.QUEUED if source[1] == "resume" else "uploaded")
            assert (row.processing_attempts, row.processing_lease_epoch) == (1, 1)
            assert row.processing_lease_owner is None and row.next_retry_at is None

    dispatcher = RecordingDispatcher(observe)
    scanner(postgres_engine, dispatcher).run_once()
    assert source[:3] in dispatcher.deliveries
    observe()


def test_publish_failure_is_durable_paced_and_does_not_block_claim(postgres_engine, source):
    committed_lease(postgres_engine, source)
    expire(postgres_engine, source)

    def fail():
        from app.processing.recovery import RecoveryDispatchUnavailable

        raise RecoveryDispatchUnavailable()

    dispatcher = RecordingDispatcher(fail)
    recovery = scanner(postgres_engine, dispatcher)
    recovery.run_once()
    recovery.run_once()
    assert dispatcher.deliveries.count(source[:3]) == 1
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.recovery_dispatch_error_code == "processing_dispatch_unavailable"
        assert row.error_code is None and row.next_retry_at is None
        result = SqlAlchemyUnitOfWork(session).leases.claim(*source[:3], "worker", duration=timedelta(minutes=5))
        assert result.disposition.value == "claimed"
        session.commit()


def test_live_fifth_attempt_is_untouched_but_expired_fifth_is_terminal(postgres_engine, source):
    committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.processing_attempts = 5
        session.commit()
    dispatcher = RecordingDispatcher()
    recovery = scanner(postgres_engine, dispatcher)
    recovery.run_once()
    assert source[:3] not in dispatcher.deliveries
    expire(postgres_engine, source)
    recovery.run_once()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.status == (ResumeStatus.FAILED if source[1] == "resume" else "failed")
        assert row.error_code == "processing_attempts_exhausted" and row.next_retry_at is None
        assert (row.processing_attempts, row.processing_lease_epoch) == (5, 1)
    assert source[:3] not in dispatcher.deliveries


def test_claim_after_scan_cannot_be_clobbered(postgres_engine, source):
    from app.processing.recovery_repository import RecoveryRepository

    with Session(postgres_engine) as session:
        session.get(source[3], source[2]).queued_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        session.commit()
    repository = RecoveryRepository(sessionmaker(postgres_engine))
    candidate = next(item for item in repository.candidates() if item.source_id == source[2])
    lease = committed_lease(postgres_engine, source)
    assert repository.reserve(candidate) is None
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.processing_lease_owner == lease.owner and row.processing_lease_epoch == lease.epoch


def test_late_dispatch_failure_cannot_overwrite_new_claim(postgres_engine, source):
    committed_lease(postgres_engine, source)
    expire(postgres_engine, source)

    def claim_then_fail():
        from app.processing.recovery import RecoveryDispatchUnavailable

        committed_lease(postgres_engine, source)
        raise RecoveryDispatchUnavailable()

    scanner(postgres_engine, RecordingDispatcher(claim_then_fail)).run_once()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.recovery_dispatch_error_code is None
        assert row.processing_attempts == 2 and row.processing_lease_epoch == 2


def test_due_failed_uses_reviewed_retry_transition_and_retains_error(postgres_engine, source):
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.status = ResumeStatus.FAILED if source[1] == "resume" else "failed"
        row.processing_attempts, row.processing_lease_epoch = 3, 3
        row.error_code = "embedding_failed"
        row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    dispatcher = RecordingDispatcher()
    scanner(postgres_engine, dispatcher).run_once()
    assert source[:3] in dispatcher.deliveries
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.error_code == "embedding_failed" and row.next_retry_at is None
        assert (row.processing_attempts, row.processing_lease_epoch) == (3, 3)


@pytest.mark.parametrize(
    "state", ["fresh_queue", "future_retry", "permanent", "exhausted", "succeeded", "deleted", "unavailable"]
)
def test_ineligible_sources_never_dispatch_or_mutate(postgres_engine, source, state):
    from app.models.artifacts import Artifact
    from app.domain.artifacts import ArtifactStatus

    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.queued_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        if state == "fresh_queue":
            row.queued_at = datetime.now(timezone.utc)
        elif state == "future_retry":
            row.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        elif state in {"permanent", "exhausted"}:
            row.status = ResumeStatus.FAILED if source[1] == "resume" else "failed"
            row.error_code = "validation_failed" if state == "permanent" else "embedding_failed"
            row.processing_attempts, row.processing_lease_epoch = 5, 5
            row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif state == "succeeded":
            row.status = ResumeStatus.SUCCEEDED if source[1] == "resume" else "ready"
            row.search_index_status = "failed"
        elif state == "deleted":
            row.lifecycle_status = "deleted"
        else:
            session.get(Artifact, row.artifact_id).status = ArtifactStatus.PENDING
        session.commit()
        before = (row.status, row.queued_at, row.processing_attempts, row.processing_lease_epoch, row.error_code)
    dispatcher = RecordingDispatcher()
    scanner(postgres_engine, dispatcher).run_once()
    assert source[:3] not in dispatcher.deliveries
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert (
            row.status,
            row.queued_at,
            row.processing_attempts,
            row.processing_lease_epoch,
            row.error_code,
        ) == before


@pytest.mark.parametrize(
    "field,value",
    [("tenant_id", "other-tenant"), ("artifact_id", "other-artifact"), ("epoch", 99), ("token", "other-reservation")],
)
def test_reservation_revalidates_full_identity(postgres_engine, source, field, value):
    from app.processing.recovery_repository import RecoveryRepository

    with Session(postgres_engine) as session:
        session.get(source[3], source[2]).queued_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        session.commit()
    repository = RecoveryRepository(sessionmaker(postgres_engine))
    candidate = next(item for item in repository.candidates() if item.source_id == source[2])
    assert repository.reserve(replace(candidate, **{field: value})) is None


def test_fair_pacing_reaches_tail_larger_than_batch_without_changing_queue_age(postgres_engine, source):
    from app.processing.recovery_repository import RecoveryRepository
    from app.processing.recovery import RecoveryScanner

    kind, model = source[1], source[3]
    stamp = datetime.now(timezone.utc) - timedelta(minutes=2)
    ids = [source[2]]
    with Session(postgres_engine) as session:
        session.get(model, source[2]).queued_at = stamp
        for i in range(4):
            identity = str(uuid4())
            ids.append(identity)
            uow = SqlAlchemyUnitOfWork(session)
            artifact = uow.artifacts.claim_upload(source[0], kind, identity, f"{i + 1:064x}", "text/plain", 10)
            uow.artifacts.mark_available(source[0], artifact.id, owner_type=kind, owner_id=identity)
            fields = dict(
                id=identity,
                tenant_id=source[0],
                artifact_id=artifact.id,
                media_type="text/plain",
                size_bytes=10,
                queued_at=stamp,
            )
            session.add(
                model(**fields) if kind == "resume" else model(**fields, document_type="policy", status="uploaded")
            )
        session.commit()
    dispatcher = RecordingDispatcher()
    for _ in range(5):
        RecoveryScanner(RecoveryRepository(sessionmaker(postgres_engine), batch_size=2), dispatcher).run_once()
        # Let previous reservations cool down while fresh instances must still reach unreserved tail.
        with Session(postgres_engine) as session:
            for identity in ids:
                row = session.get(model, identity)
                if row.recovery_dispatch_at is not None:
                    row.recovery_dispatch_at = stamp + timedelta(seconds=1)
            session.commit()
    assert set(ids) <= {identity for tenant, _, identity in dispatcher.deliveries if tenant == source[0]}
    with Session(postgres_engine) as session:
        for identity in ids:
            row = session.get(model, identity)
            assert row.queued_at == stamp and row.next_retry_at is None


@pytest.mark.parametrize("terminal", [False, True])
def test_generation_reconciliation_runs_only_under_new_legitimate_worker_lease(processing_source, terminal):
    from sqlalchemy import select
    from app.models.retrieval import RecruitingChunk
    from app.retrieval.generations import GenerationWriter, StagedChunk
    from app.processing.recovery import RecoveryScanner
    from app.processing.recovery_repository import RecoveryRepository

    factory, _, ref, model = processing_source
    lease = claim(processing_source)
    writer = GenerationWriter(factory)
    history = StagedChunk("history", "Python", 0, 6, [1.0] + [0.0] * 511, "fake")
    writer.stage(ref, 1, [history], fencing_token=lease)
    writer.activate(ref, 1, expected_count=1, embedding_model="fake", fencing_token=lease)
    writer.stage(ref, 2, [replace(history, citation_id="abandoned")], fencing_token=lease)
    with factory() as session:
        row = session.get(model, ref.source_id)
        if terminal:
            row.status = ResumeStatus.SUCCEEDED if ref.source_type == "resume" else "ready"
            row.search_index_status = "failed"
        row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    deliveries = []

    class CurrentWorker:
        def dispatch_resume(self, tenant, identity):
            if (tenant, identity) == (ref.tenant_id, ref.source_id):
                deliveries.append(processor(processing_source).process(tenant, identity))

        dispatch_knowledge = dispatch_resume

    RecoveryScanner(RecoveryRepository(factory), CurrentWorker()).run_once()
    with factory() as session:
        chunks = list(session.scalars(select(RecruitingChunk).where(RecruitingChunk.tenant_id == ref.tenant_id)))
        assert any(chunk.citation_id == "history" for chunk in chunks)
        row = session.get(model, ref.source_id)
        if terminal:
            assert not deliveries and row.processing_lease_epoch == 1
            assert {chunk.citation_id for chunk in chunks} == {"history", "abandoned"}
            assert row.active_index_generation == 1
        else:
            assert len(deliveries) == 1 and row.processing_lease_epoch == 2
            assert all(chunk.citation_id != "abandoned" for chunk in chunks)
            assert row.active_index_generation == 2


def test_older_failed_reservation_precedes_new_unreserved_arrivals(postgres_engine, source):
    from app.processing.recovery_repository import RecoveryRepository

    stamp = datetime.now(timezone.utc)
    newer_id = str(uuid4())
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.queued_at = stamp - timedelta(minutes=10)
        row.recovery_dispatch_at = stamp - timedelta(minutes=5)
        row.recovery_dispatch_token = str(uuid4())
        row.recovery_dispatch_error_code = "processing_dispatch_unavailable"
        uow = SqlAlchemyUnitOfWork(session)
        artifact = uow.artifacts.claim_upload(source[0], source[1], newer_id, "b" * 64, "text/plain", 10)
        uow.artifacts.mark_available(source[0], artifact.id, owner_type=source[1], owner_id=newer_id)
        fields = dict(
            id=newer_id,
            tenant_id=source[0],
            artifact_id=artifact.id,
            media_type="text/plain",
            size_bytes=10,
            queued_at=stamp - timedelta(minutes=2),
        )
        session.add(
            source[3](**fields)
            if source[1] == "resume"
            else source[3](**fields, document_type="policy", status="uploaded")
        )
        session.commit()
    candidates = RecoveryRepository(sessionmaker(postgres_engine), batch_size=1).candidates()
    assert next(item for item in candidates if item.source_type == source[1]).source_id == source[2]
