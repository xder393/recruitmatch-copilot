"""Durable retry transitions on real PostgreSQL and exact lease fences."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.orm import Session

from app.processing.outcomes import ProcessDisposition
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.processing import test_leases
from tests.integration.processing.test_leases import committed_lease, claim, expire

source = test_leases.source


def schedule(repo, lease, **kwargs):
    assert hasattr(repo, "schedule_retry"), "missing durable retry transition"
    return repo.schedule_retry(lease, "storage_unavailable", **kwargs)


def test_crash_redelivery_cannot_exceed_claim_budget(postgres_engine, source):
    committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.processing_attempts = 5
        session.commit()
        assert claim(session, source).disposition.value == "duplicate_active"
    expire(postgres_engine, source)
    with Session(postgres_engine) as session:
        assert claim(session, source).disposition.value == "terminal"
        session.commit()
        row = session.get(source[3], source[2])
        assert row.processing_attempts == 5 and row.processing_lease_epoch == 1
        assert row.error_code == "processing_attempts_exhausted"


def test_short_retry_is_durable_and_cannot_run_early(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        outcome = schedule(
            repo, lease, short_delay=timedelta(seconds=30), long_delay=timedelta(seconds=300), max_attempts=5
        )
        assert outcome == ProcessDisposition.RETRY_SHORT
        session.commit()
        assert claim(session, source).disposition.value == "deferred"
        row = session.get(source[3], source[2])
        assert row.processing_lease_owner is None and row.processing_lease_expires_at is None
        assert (row.processing_attempts, row.processing_lease_epoch) == (1, 1)
        assert row.next_retry_at > datetime.now(timezone.utc)
        row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        assert claim(session, source).disposition.value == "claimed"
        session.commit()
        assert (row.processing_attempts, row.processing_lease_epoch) == (2, 2)


def test_long_retry_requires_explicit_guarded_requeue(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        row = session.get(source[3], source[2])
        row.processing_attempts = 3
        session.commit()
        repo = SqlAlchemyUnitOfWork(session).leases
        assert (
            schedule(repo, lease, short_delay=timedelta(seconds=30), long_delay=timedelta(seconds=300), max_attempts=5)
            == ProcessDisposition.COMPLETED
        )
        session.commit()
        assert claim(session, source).disposition.value == "terminal"
        assert not repo.requeue_due(
            *source[:3], expected_epoch=1, expected_artifact_id=lease.artifact_id, max_attempts=5
        )
        row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        assert claim(session, source).disposition.value == "terminal"
        assert not repo.requeue_due(
            *source[:3], expected_epoch=2, expected_artifact_id=lease.artifact_id, max_attempts=5
        )
        assert not repo.requeue_due(*source[:3], expected_epoch=1, expected_artifact_id="wrong", max_attempts=5)
        assert repo.requeue_due(*source[:3], expected_epoch=1, expected_artifact_id=lease.artifact_id, max_attempts=5)
        session.commit()
        assert row.next_retry_at is None
        assert not repo.requeue_due(
            *source[:3], expected_epoch=1, expected_artifact_id=lease.artifact_id, max_attempts=5
        )
        assert claim(session, source).disposition.value == "claimed"
        session.commit()
        assert (row.processing_attempts, row.processing_lease_epoch) == (4, 2)


def test_exhaustion_and_old_worker_never_requeue(postgres_engine, source):
    old = committed_lease(postgres_engine, source)
    expire(postgres_engine, source)
    current = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        args = dict(short_delay=timedelta(seconds=30), long_delay=timedelta(seconds=300), max_attempts=2)
        assert schedule(repo, old, **args) == ProcessDisposition.LEASE_LOST
        assert schedule(repo, replace(current, artifact_id="wrong"), **args) == ProcessDisposition.LEASE_LOST
        assert schedule(repo, current, **args) == ProcessDisposition.COMPLETED
        session.commit()
        row = session.get(source[3], source[2])
        assert row.next_retry_at is None and row.error_code == "storage_unavailable"
        assert not repo.requeue_due(
            *source[:3], expected_epoch=2, expected_artifact_id=current.artifact_id, max_attempts=2
        )
        assert claim(session, source).disposition.value == "terminal"


@pytest.mark.parametrize("blocked", ["deleted", "artifact", "permanent", "budget"])
def test_due_requeue_revalidates_lifecycle_artifact_error_and_budget(postgres_engine, source, blocked):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        assert repo.fail(lease, "storage_unavailable", next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        session.commit()
        row = session.get(source[3], source[2])
        if blocked == "deleted":
            row.lifecycle_status = "deleted"
        elif blocked == "artifact":
            SqlAlchemyUnitOfWork(session).artifacts.mark_cleanup_pending(
                source[0], lease.artifact_id, owner_type=source[1], owner_id=source[2]
            )
        elif blocked == "permanent":
            row.error_code = "resume_parsed_state_invalid"
        else:
            row.processing_attempts = 5
        session.commit()
        assert not repo.requeue_due(
            *source[:3], expected_epoch=1, expected_artifact_id=lease.artifact_id, max_attempts=5
        )
        session.commit()
        assert row.next_retry_at is not None


def test_retry_transition_rollback_keeps_original_lease(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        repo = SqlAlchemyUnitOfWork(session).leases
        assert (
            schedule(repo, lease, short_delay=timedelta(seconds=30), long_delay=timedelta(seconds=300), max_attempts=5)
            == ProcessDisposition.RETRY_SHORT
        )
        session.rollback()
        row = session.get(source[3], source[2])
        assert row.processing_lease_owner == lease.owner and row.error_code is None
        assert row.next_retry_at is None


def test_due_snapshot_is_consumed_once_under_concurrent_requeue(postgres_engine, source):
    lease = committed_lease(postgres_engine, source)
    with Session(postgres_engine) as session:
        assert SqlAlchemyUnitOfWork(session).leases.fail(
            lease, "storage_unavailable", next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        session.commit()
    barrier = Barrier(2)

    def requeue():
        with Session(postgres_engine) as session:
            barrier.wait(timeout=10)
            result = SqlAlchemyUnitOfWork(session).leases.requeue_due(
                *source[:3], expected_epoch=1, expected_artifact_id=lease.artifact_id, max_attempts=5
            )
            session.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(requeue) for _ in range(2)]
        assert sorted(future.result(timeout=10) for future in futures) == [False, True]


def test_actual_redis_channel_uses_visibility_without_result_backend():
    from app.config import Settings
    from app.tasks.celery_app import celery_app
    from celery.backends.base import DisabledBackend

    assert isinstance(celery_app.backend, DisabledBackend)
    with celery_app.connection_for_read() as connection:
        with connection.channel() as channel:
            assert channel.visibility_timeout == Settings.load().redis_visibility_timeout
