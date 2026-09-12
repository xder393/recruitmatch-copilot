"""Real independent PostgreSQL transactions exercise processing ownership fences."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.domain.enums import ResumeStatus
from app.models.identity import Tenant
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork


@pytest.fixture(params=["resume", "knowledge_document"])
def source(postgres_engine, request):
    tenant, source_id = str(uuid4()), str(uuid4())
    kind = request.param
    model = Resume if kind == "resume" else KnowledgeDocument
    with Session(postgres_engine) as session:
        session.add(Tenant(id=tenant, name="lease-test"))
        session.flush()
        uow = SqlAlchemyUnitOfWork(session)
        artifact = uow.artifacts.claim_upload(tenant, kind, source_id, "a" * 64, "text/plain", 10)
        uow.artifacts.mark_available(tenant, artifact.id, owner_type=kind, owner_id=source_id)
        fields = dict(id=source_id, tenant_id=tenant, artifact_id=artifact.id, media_type="text/plain", size_bytes=10)
        row = (
            Resume(**fields)
            if kind == "resume"
            else KnowledgeDocument(**fields, document_type="policy", status="uploaded")
        )
        session.add(row)
        session.commit()
    yield tenant, kind, source_id, model
    with Session(postgres_engine) as session:
        session.execute(delete(Tenant).where(Tenant.id == tenant))
        session.commit()


def claim(session, source, owner="worker-a", duration=timedelta(minutes=5)):
    return SqlAlchemyUnitOfWork(session).leases.claim(*source[:3], owner, duration=duration)


def committed_lease(engine, source, duration=timedelta(minutes=5)):
    with Session(engine) as session:
        result = claim(session, source, duration=duration)
        session.commit()
        return result.lease


def expire(engine, source):
    with Session(engine) as session:
        row = session.get(source[3], source[2])
        row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()


def test_live_duplicate_does_not_change_attempt_epoch_errors_or_timestamps(postgres_engine, source):
    # Missing lease arbitration must fail here, before imports of the new module.
    with Session(postgres_engine) as session:
        first = claim(session, source)
        assert first.disposition.value == "claimed"
        session.commit()
        row = session.get(source[3], source[2])
        row.error_code = "retained_code"
        session.commit()
        before = (row.processing_attempts, row.processing_lease_epoch, row.updated_at, row.processing_lease_expires_at)
    with Session(postgres_engine) as session:
        result = claim(session, source, "worker-b")
        assert result.disposition.value == "duplicate_active" and result.lease is None
        session.commit()
        row = session.get(source[3], source[2])
        assert (
            row.processing_attempts,
            row.processing_lease_epoch,
            row.updated_at,
            row.processing_lease_expires_at,
        ) == before
        assert row.error_code == "retained_code"
        assert row.processing_lease_owner == "worker-a"


def test_simultaneous_claim_has_one_winner(postgres_engine, source):
    barrier = Barrier(2)

    def worker(owner):
        with Session(postgres_engine) as session:
            barrier.wait(timeout=10)
            result = claim(session, source, owner)
            session.commit()
            return result.disposition.value

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(worker, ["worker-a", "worker-b"])) == ["claimed", "duplicate_active"]
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert (row.processing_attempts, row.processing_lease_epoch) == (1, 1)


def test_expired_takeover_fences_old_owner_and_epoch(postgres_engine, source):
    old = committed_lease(postgres_engine, source)
    expire(postgres_engine, source)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        assert not repo.renew(old, duration=timedelta(minutes=5))
        assert not repo.finalize(old)
        assert not repo.fail(old, "timeout")
        session.commit()
        current = claim(session, source, "worker-b").lease
        assert current.epoch == 2
        session.commit()
        assert not repo.renew(old, duration=timedelta(minutes=5))
        assert not repo.finalize(old)
        assert not repo.fail(old, "timeout")
        assert repo.renew(current, duration=timedelta(minutes=10))
        assert repo.finalize(current)
        session.commit()
        row = session.get(source[3], source[2])
        assert (row.processing_attempts, row.processing_lease_epoch) == (2, 2)
        assert row.status == (ResumeStatus.SUCCEEDED if source[1] == "resume" else "ready")
        assert row.processing_lease_owner is None and row.processing_lease_expires_at is None


@pytest.mark.parametrize(
    "field,value", [("tenant_id", "other-tenant"), ("source_id", "missing"), ("owner", "worker-b"), ("epoch", 2)]
)
def test_all_ownership_methods_reject_wrong_identity(postgres_engine, source, field, value):
    lease = replace(committed_lease(postgres_engine, source), **{field: value})
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        assert not repo.renew(lease, duration=timedelta(minutes=5))
        assert not repo.finalize(lease)
        assert not repo.fail(lease, "timeout")
        session.commit()
        row = session.get(source[3], source[2])
        assert row.processing_attempts == 1 and row.error_code is None


def test_type_boundary_with_identical_ids(postgres_engine, source):
    from app.processing.outcomes import LeaseOwnershipLost

    lease = committed_lease(postgres_engine, source)
    other_kind = "knowledge_document" if source[1] == "resume" else "resume"
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        repo = uow.leases
        other = replace(lease, source_type=other_kind)
        assert (
            repo.claim(source[0], other_kind, source[2], "worker-b", duration=timedelta(minutes=5)).disposition.value
            == "terminal"
        )
        artifact = uow.artifacts.claim_upload(source[0], other_kind, source[2], "b" * 64, "text/plain", 10)
        uow.artifacts.mark_available(source[0], artifact.id, owner_type=other_kind, owner_id=source[2])
        fields = dict(
            id=source[2], tenant_id=source[0], artifact_id=artifact.id, media_type="text/plain", size_bytes=10
        )
        row = (
            Resume(**fields)
            if other_kind == "resume"
            else KnowledgeDocument(**fields, document_type="policy", status="uploaded")
        )
        session.add(row)
        session.commit()
        correct = repo.claim(source[0], other_kind, source[2], "worker-a", duration=timedelta(minutes=5)).lease
        session.commit()
        assert correct.epoch == lease.epoch and correct.owner == lease.owner
        assert not repo.renew(other, duration=timedelta(minutes=5))
        assert not repo.finalize(other)
        assert not repo.fail(other, "timeout")
        with pytest.raises(LeaseOwnershipLost), repo.finalize_owned(other):
            pytest.fail("wrong source type authorized publication")
        assert (
            repo.claim(
                "other-tenant", source[1], source[2], "worker-a", duration=timedelta(minutes=5)
            ).disposition.value
            == "terminal"
        )


@pytest.mark.parametrize(
    "source,state",
    [(kind, state) for kind in ("resume", "knowledge_document") for state in ("succeeded", "failed", "deleted")]
    + [("knowledge_document", "inactive")],
    indirect=["source"],
)
def test_terminal_and_deactivated_sources_are_not_claimed(postgres_engine, source, state):
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.status = ResumeStatus(state) if source[1] == "resume" else {"succeeded": "ready"}.get(state, state)
        if state == "deleted":
            row.lifecycle_status = "deleted"
        session.commit()
        assert claim(session, source).disposition.value == "terminal"
        session.commit()
        session.refresh(row)
        assert (row.processing_attempts, row.processing_lease_epoch) == (0, 0)


@pytest.mark.parametrize(
    "source,change",
    [(kind, change) for kind in ("resume", "knowledge_document") for change in ("deleted", "unavailable")]
    + [("knowledge_document", "inactive")],
    indirect=["source"],
)
def test_ownership_loss_blocks_every_write(postgres_engine, source, change):
    from app.processing.outcomes import LeaseOwnershipLost

    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        if change == "deleted":
            row.lifecycle_status = "deleted"
        elif change == "inactive":
            row.status = "inactive"
        else:
            SqlAlchemyUnitOfWork(session).artifacts.mark_cleanup_pending(
                source[0], row.artifact_id, owner_type=source[1], owner_id=source[2]
            )
        session.commit()
        repo = SqlAlchemyUnitOfWork(session).leases
        assert not repo.renew(lease, duration=timedelta(minutes=5))
        assert not repo.finalize(lease)
        assert not repo.fail(lease, "timeout")
        with pytest.raises(LeaseOwnershipLost), repo.finalize_owned(lease):
            pytest.fail("lost ownership authorized derived writes")


@pytest.mark.parametrize("change", ["future_retry", "unavailable", "missing_artifact"])
def test_temporarily_ineligible_queued_source_is_deferred(postgres_engine, source, change):
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        if change == "future_retry":
            row.next_retry_at = datetime.now(timezone.utc) + timedelta(days=1)
        elif change == "missing_artifact":
            row.artifact_id = None
        else:
            SqlAlchemyUnitOfWork(session).artifacts.mark_cleanup_pending(
                source[0], row.artifact_id, owner_type=source[1], owner_id=source[2]
            )
        session.commit()
        assert claim(session, source).disposition.value == "deferred"
        session.commit()
        session.refresh(row)
        assert (row.processing_attempts, row.processing_lease_epoch) == (0, 0)


def test_failure_stores_bounded_code_and_requires_explicit_requeue(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    retry = datetime.now(timezone.utc) - timedelta(seconds=1)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        assert repo.fail(lease, "model_timeout", next_retry_at=retry)
        session.commit()
        assert claim(session, source).disposition.value == "terminal"
        row = session.get(source[3], source[2])
        assert row.error_code == "model_timeout" and row.next_retry_at == retry
        assert row.processing_lease_owner is None and row.processing_lease_expires_at is None


def test_claim_and_terminal_writes_follow_caller_rollback(postgres_engine, source):
    with Session(postgres_engine) as session:
        claim(session, source)
        session.rollback()
        row = session.get(source[3], source[2])
        assert (row.processing_attempts, row.processing_lease_epoch) == (0, 0)
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        assert SqlAlchemyUnitOfWork(session).leases.finalize(lease)
        session.rollback()
        assert SqlAlchemyUnitOfWork(session).leases.fail(lease, "timeout")
        session.rollback()
        assert SqlAlchemyUnitOfWork(session).leases.renew(lease, duration=timedelta(minutes=5))


def test_guard_commits_derived_data_and_terminal_state_together(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with uow.leases.finalize_owned(lease):
            session.get(source[3], source[2]).active_index_generation = 7
        uow.commit()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.active_index_generation == 7
        assert row.status == (ResumeStatus.SUCCEEDED if source[1] == "resume" else "ready")


def test_guard_rolls_back_derived_data_if_lease_expires_during_publication(postgres_engine, source):
    from app.processing.outcomes import LeaseOwnershipLost

    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(LeaseOwnershipLost), uow.leases.finalize_owned(lease):
            row = session.get(source[3], source[2])
            row.active_index_generation = 7
            row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.flush()
        uow.commit()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.active_index_generation == 0
        assert row.status == (ResumeStatus.RUNNING if source[1] == "resume" else "processing")


def wait_until_blocked(engine, pid):
    deadline = monotonic() + 10
    with engine.connect() as connection:
        while monotonic() < deadline:
            if connection.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                return
    pytest.fail("claim never blocked on Source lock")


def test_clock_is_observed_after_waiting_for_source_lock(postgres_engine, source):
    committed_lease(postgres_engine, source)
    started = Event()
    pids = []

    def blocked_claim():
        with Session(postgres_engine) as session:
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            result = claim(session, source, "worker-b")
            session.commit()
            return result

    with Session(postgres_engine) as holder, ThreadPoolExecutor(1) as pool:
        row = holder.scalar(select(source[3]).where(source[3].id == source[2]).with_for_update())
        future = pool.submit(blocked_claim)
        assert started.wait(10)
        wait_until_blocked(postgres_engine, pids[0])
        # Expiry is after the contender's transaction began, but before its lock is acquired.
        row.processing_lease_expires_at = holder.scalar(text("SELECT clock_timestamp()"))
        holder.commit()
        assert future.result(timeout=10).disposition.value == "claimed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", ""),
        ("source_id", "x" * 37),
        ("source_type", "job_version"),
        ("owner", ""),
        ("owner", "x" * 101),
        ("duration", timedelta(0)),
        ("duration", timedelta(seconds=-1)),
    ],
)
def test_claim_rejects_malformed_input_without_writes(postgres_engine, source, field, value):
    params = dict(
        tenant_id=source[0], source_type=source[1], source_id=source[2], owner="worker", duration=timedelta(minutes=5)
    )
    params[field] = value
    with Session(postgres_engine) as session:
        with pytest.raises(ValueError):
            SqlAlchemyUnitOfWork(session).leases.claim(**params)
        session.commit()
        assert session.get(source[3], source[2]).processing_attempts == 0


def test_publication_requires_guard_before_pending_writes(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.active_index_generation = 7
        with pytest.raises(ValueError, match="processing_publication_requires_clean_session"):
            with SqlAlchemyUnitOfWork(session).leases.finalize_owned(lease):
                pytest.fail("publication must start before derived writes")
        session.rollback()
    with Session(postgres_engine) as session:
        assert session.get(source[3], source[2]).active_index_generation == 0


def test_publication_body_exception_rolls_back_derived_writes(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(RuntimeError, match="synthetic"), uow.leases.finalize_owned(lease):
            session.get(source[3], source[2]).active_index_generation = 7
            session.flush()
            raise RuntimeError("synthetic")
        uow.commit()
    with Session(postgres_engine) as session:
        assert session.get(source[3], source[2]).active_index_generation == 0


@pytest.mark.parametrize("method", ["renew", "finalize", "fail"])
def test_dirty_stale_source_cannot_flush_before_ownership_check(postgres_engine, source, method):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine, expire_on_commit=False) as stale:
        row = stale.get(source[3], source[2])
        stale.commit()
        with Session(postgres_engine) as current:
            current.get(source[3], source[2]).lifecycle_status = "deleted"
            current.commit()
        row.active_index_generation = 7
        row.lifecycle_status = "active"
        repo = SqlAlchemyUnitOfWork(stale).leases
        args = (lease, "timeout") if method == "fail" else (lease,)
        kwargs = {"duration": timedelta(minutes=5)} if method == "renew" else {}
        assert not getattr(repo, method)(*args, **kwargs)
        stale.commit()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.lifecycle_status == "deleted" and row.active_index_generation == 0


@pytest.mark.parametrize(
    "field,value", [("epoch", 0), ("epoch", True), ("owner", " "), ("expires_at", datetime(2026, 1, 1))]
)
def test_malformed_ownership_token_is_rejected(postgres_engine, source, field, value):
    lease = replace(committed_lease(postgres_engine, source), **{field: value})
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        for operation in [
            lambda: repo.renew(lease, duration=timedelta(minutes=5)),
            lambda: repo.finalize(lease),
            lambda: repo.fail(lease, "timeout"),
        ]:
            with pytest.raises(ValueError):
                operation()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"error_code": "private text"},
        {"error_code": "x" * 101},
        {"error_code": "timeout", "next_retry_at": datetime(2026, 1, 1)},
    ],
)
def test_failure_rejects_unbounded_error_or_naive_retry_without_writes(postgres_engine, source, kwargs):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        with pytest.raises(ValueError):
            SqlAlchemyUnitOfWork(session).leases.fail(lease, **kwargs)
        session.commit()
        row = session.get(source[3], source[2])
        assert row.error_code is None and row.processing_lease_owner == "worker-a"


@pytest.mark.parametrize("method", ["renew", "finalize", "fail"])
def test_ownership_write_rechecks_expiry_after_lock_wait(postgres_engine, source, method):
    lease = committed_lease(postgres_engine, source)
    started = Event()
    pids = []

    def contender():
        with Session(postgres_engine) as session:
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            repo = SqlAlchemyUnitOfWork(session).leases
            args = (lease, "timeout") if method == "fail" else (lease,)
            kwargs = {"duration": timedelta(minutes=5)} if method == "renew" else {}
            result = getattr(repo, method)(*args, **kwargs)
            session.commit()
            return result

    with Session(postgres_engine) as holder, ThreadPoolExecutor(1) as pool:
        row = holder.scalar(select(source[3]).where(source[3].id == source[2]).with_for_update())
        future = pool.submit(contender)
        assert started.wait(10)
        wait_until_blocked(postgres_engine, pids[0])
        row.processing_lease_expires_at = holder.scalar(text("SELECT clock_timestamp()"))
        holder.commit()
        assert future.result(timeout=10) is False


def test_epoch_survives_bigint_range_and_takeover_rollback(postgres_engine, source):
    committed_lease(postgres_engine, source)
    expire(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.processing_lease_epoch = 2147483648
        session.commit()
        lease = claim(session, source, "worker-b").lease
        assert lease.epoch == 2147483649
        session.rollback()
        session.refresh(row)
        assert (row.processing_attempts, row.processing_lease_epoch) == (1, 2147483648)


def test_successful_guard_still_obeys_outer_rollback(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        uow = SqlAlchemyUnitOfWork(session)
        with uow.leases.finalize_owned(lease):
            session.get(source[3], source[2]).active_index_generation = 7
        uow.rollback()
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        assert row.active_index_generation == 0 and row.processing_lease_owner == "worker-a"
