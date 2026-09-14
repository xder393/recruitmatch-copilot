"""Held rows must not abort actual Beat recovery of unrelated PostgreSQL work."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from celery import Celery
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.artifacts import Artifact
from app.operations.heartbeats import OperationsHeartbeats, redis_client
from app.processing.recovery import RecoveryDispatchUnavailable
from app.processing.recovery_repository import RecoveryRepository
from app.repositories.ports import PersistenceUnavailable
from app.tasks.beat import RecoveryScheduler
from tests.integration.processing.test_leases import source as source_fixture
from tests.integration.processing.test_recovery import RecordingDispatcher


@pytest.fixture(params=["resume", "knowledge_document"])
def recovery_cohort(postgres_engine, request):
    kinds = [request.param, request.param, "knowledge_document" if request.param == "resume" else "resume"]
    with ExitStack() as cleanup:
        rows = []
        for index, kind in enumerate(kinds):
            fixture = source_fixture.__wrapped__(postgres_engine, SimpleNamespace(param=kind))
            rows.append(next(fixture))
            cleanup.callback(lambda fixture=fixture: next(fixture, None))
            with Session(postgres_engine) as session:
                row = session.get(rows[-1][3], rows[-1][2])
                row.queued_at = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
                row.error_code = "processing_timeout"
                session.commit()
        yield rows


def recovery_state(session, source):
    row = session.get(source[3], source[2], populate_existing=True)
    return (
        row.status,
        row.queued_at,
        row.processing_attempts,
        row.processing_lease_epoch,
        row.processing_lease_owner,
        row.processing_lease_expires_at,
        row.next_retry_at,
        row.error_code,
        row.recovery_dispatch_at,
        row.recovery_dispatch_token,
        row.recovery_dispatch_error_code,
    )


@pytest.mark.parametrize("locked_target", ["source", "artifact"])
@pytest.mark.parametrize("phase", ["reserve", "dispatch_failed"])
def test_held_row_does_not_abort_other_candidates_or_beat_observations(
    postgres_engine, recovery_cohort, monkeypatch, locked_target, phase
):
    blocked, same_type, other_type = recovery_cohort
    heartbeats = OperationsHeartbeats(Settings.load().celery_broker_url, namespace="test:" + uuid4().hex)
    maintenance = []
    app = Celery("contention-test")
    # Only broker publication is substituted; production scheduler composition,
    # scanner, repository, PostgreSQL guards and Redis heartbeat remain real.
    monkeypatch.setattr(app, "send_task", lambda name, **kwargs: maintenance.append((name, kwargs)))
    with Session(postgres_engine) as holder:
        before = []

        def hold_row():
            before.append(recovery_state(holder, blocked))
            model = blocked[3] if locked_target == "source" else Artifact
            identity = blocked[2] if locked_target == "source" else holder.get(blocked[3], blocked[2]).artifact_id
            assert holder.scalar(select(model).where(model.id == identity).with_for_update()) is not None

        dispatcher = RecordingDispatcher()

        def late_failure():
            if dispatcher.deliveries[-1] == blocked[:3]:
                hold_row()
                raise RecoveryDispatchUnavailable()

        if phase == "reserve":
            hold_row()
        else:
            dispatcher.failure = late_failure
        monkeypatch.setattr("app.tasks.beat.CeleryTaskDispatcher", lambda: dispatcher)
        scheduler = RecoveryScheduler(app=app, heartbeats=heartbeats)
        try:
            assert scheduler.tick() == 10
            assert same_type[:3] in dispatcher.deliveries
            assert other_type[:3] in dispatcher.deliveries
            assert (blocked[:3] in dispatcher.deliveries) is (phase == "dispatch_failed")
            assert maintenance == [
                (
                    "recruitmatch.reconcile_artifacts",
                    {"args": (), "expires": 60, "argsrepr": "[redacted]", "kwargsrepr": "[redacted]"},
                )
            ]
            assert heartbeats.snapshot()["beat"] == "fresh"
            assert recovery_state(holder, blocked) == before[0]
            with Session(postgres_engine) as observer:
                for source in (same_type, other_type):
                    row = observer.get(source[3], source[2])
                    assert row.recovery_dispatch_token is not None
                    assert (row.processing_attempts, row.processing_lease_epoch) == (0, 0)
                if locked_target == "artifact":
                    # The skipped candidate must release its already-acquired
                    # Source lock while the Artifact holder is still active.
                    assert (
                        observer.scalar(
                            select(blocked[3]).where(blocked[3].id == blocked[2]).with_for_update(nowait=True)
                        )
                        is not None
                    )
            holder.rollback()
            if phase == "reserve":
                # Skipping did not rewrite pacing or queue age: next tick can
                # visit this candidate after the real lock has been released.
                assert scheduler.tick() == 10
                assert dispatcher.deliveries.count(blocked[:3]) == 1
        finally:
            scheduler.close()
            app.close()
            with redis_client(heartbeats.url) as client:
                client.delete(heartbeats.beat_key)


@pytest.mark.parametrize("operation", ["reserve", "dispatch_failed"])
@pytest.mark.parametrize("fault", ["connection", "schema"])
def test_recovery_lock_does_not_hide_genuine_database_faults(postgres_engine, recovery_cohort, operation, fault):
    source = recovery_cohort[0]
    repository = RecoveryRepository(sessionmaker(postgres_engine))
    candidate = next(item for item in repository.candidates() if item.source_id == source[2])
    if fault == "connection":
        engine = create_engine(postgres_engine.url.set(host="127.0.0.1", port=1), connect_args={"connect_timeout": 1})
        expected = PersistenceUnavailable
    else:
        # Read-only connection-local search path makes Source tables genuinely
        # absent without changing any schema or the shared connection settings.
        engine = create_engine(postgres_engine.url, connect_args={"options": "-c search_path=pg_catalog"})
        expected = ProgrammingError
    try:
        with pytest.raises(expected):
            getattr(RecoveryRepository(sessionmaker(engine)), operation)(candidate)
    finally:
        engine.dispose()
