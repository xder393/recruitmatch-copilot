"""Committed business metrics use real PostgreSQL leases, never delivery status."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.processing import test_processors
from tests.integration.processing.test_processors import processor
from tests.observability import test_domain_metrics
from tests.observability.test_otel_adapter import metrics

processing_source = test_processors.processing_source
telemetry = test_domain_metrics.telemetry


def value(reader, name):
    observed = metrics(reader)
    return sum(point.value for point in observed[name].data.data_points) if name in observed else 0


def test_success_and_duplicate_count_only_one_committed_publication(processing_source, telemetry):
    _, _, source, _ = processing_source
    runtime, exporter, reader = telemetry
    service = processor(processing_source)
    assert service.process(source.tenant_id, source.source_id).value == "completed"
    assert service.process(source.tenant_id, source.source_id).value == "terminal"
    assert value(reader, "recruitmatch.task.started") == 1
    assert value(reader, "recruitmatch.task.completed") == 1
    assert value(reader, "recruitmatch.vector.indexed_chunks") == 1
    assert value(reader, "recruitmatch.task.failed") == 0
    runtime.force_flush()
    names = {span.name for span in exporter.get_finished_spans()}
    assert {"embedding.generate", "vector.index", "lease.claim", "lease.finalize"} <= names


def test_all_failed_attempts_and_only_scheduled_retries_count(processing_source, telemetry, monkeypatch):
    factory, store, source, model = processing_source
    _, _, reader = telemetry

    def unavailable(*args):
        raise OSError("PRIVATE storage details")

    monkeypatch.setattr(store, "read_bounded", unavailable)
    service = processor(processing_source)
    for attempt in range(1, 6):
        assert service.process(source.tenant_id, source.source_id).value == (
            "retry_short" if attempt <= 2 else "completed"
        )
        if attempt < 5:
            with factory() as session:
                row = session.get(model, source.source_id)
                row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                session.commit()
                if attempt > 2:
                    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

                    assert SqlAlchemyUnitOfWork(session).leases.requeue_due(
                        source.tenant_id,
                        source.source_type,
                        source.source_id,
                        expected_epoch=attempt,
                        expected_artifact_id=row.artifact_id,
                        max_attempts=5,
                    )
                    session.commit()
    assert value(reader, "recruitmatch.task.started") == 5
    assert value(reader, "recruitmatch.task.failed") == 5
    assert value(reader, "recruitmatch.task.retry") == 4
    assert value(reader, "recruitmatch.task.completed") == 0


@pytest.mark.parametrize("failure", [False, True])
def test_failed_outer_commit_does_not_count_publication(processing_source, telemetry, monkeypatch, failure):
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, store, source, _ = processing_source
    _, _, reader = telemetry
    commits = 0
    if failure:

        def unavailable(*args):
            raise OSError("PRIVATE storage failure")

        monkeypatch.setattr(store, "read_bounded", unavailable)

    def uows():
        uow = SqlAlchemyUnitOfWork(factory(), owns_session=True)
        commit = uow.commit

        def guarded_commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError("synthetic failed publication commit")
            commit()

        uow.commit = guarded_commit
        return uow

    service = processor(processing_source)
    service.uow_factory = uows
    with pytest.raises(RuntimeError, match="synthetic failed publication"):
        service.process(source.tenant_id, source.source_id)
    assert value(reader, "recruitmatch.task.started") == 1
    assert value(reader, "recruitmatch.task.completed") == 0
    assert value(reader, "recruitmatch.vector.indexed_chunks") == 0
    assert value(reader, "recruitmatch.task.failed") == 0
    assert value(reader, "recruitmatch.task.retry") == 0


def test_postgres_queue_and_unknown_heartbeat_snapshots(processing_source, telemetry, monkeypatch):
    from app.observability.operations import OperationalMetrics

    factory, _, source, _ = processing_source
    runtime, exporter, reader = telemetry

    class Heartbeats:
        failed = False

        def snapshot(self):
            return {
                "worker_count": None if self.failed else 2,
                "worker_oldest_heartbeat_age_seconds": None if self.failed else 3,
                "beat_heartbeat_age_seconds": None if self.failed else 4,
            }

    heartbeat = Heartbeats()
    collector = OperationalMetrics(runtime, factory, heartbeat)
    collector.poll()
    runtime.force_flush()
    assert exporter.get_finished_spans() == ()
    data = metrics(reader)
    queue = data["recruitmatch.queue.depth"].data.data_points
    assert next(point.value for point in queue if point.attributes["source.type"] == source.source_type) >= 1
    assert data["recruitmatch.worker.live"].data.data_points[0].value == 2
    assert data["recruitmatch.worker.live"].data.data_points[0].attributes == {}
    heartbeat.failed = True
    collector.poll()
    assert "recruitmatch.worker.live" not in metrics(reader)
    heartbeat.failed = False
    collector.poll()
    assert metrics(reader)["recruitmatch.worker.live"].data.data_points[0].value == 2
    with monkeypatch.context() as patch:

        def unavailable():
            raise RuntimeError("PRIVATE database failure")

        patch.setattr(collector, "session_factory", unavailable)
        collector.poll()
        data = metrics(reader)
        assert "recruitmatch.queue.depth" not in data
        assert "recruitmatch.queue.oldest_age" not in data
        assert "recruitmatch.artifact.cleanup_pending" not in data
        assert data["recruitmatch.worker.live"].data.data_points[0].value == 2
    collector.poll()
    assert "recruitmatch.queue.depth" in metrics(reader)


def test_renewal_thread_retains_safe_parent_and_counts_failure(processing_source, telemetry):
    from app.processing.renewal import LeaseRenewer
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
    from opentelemetry import trace

    factory, _, source, model = processing_source
    runtime, exporter, reader = telemetry
    lease = test_processors.claim(processing_source)
    with factory() as session:
        session.get(model, source.source_id).processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        session.commit()
    with runtime.recorder.operation("resume.process"):
        parent = trace.get_current_span().get_span_context()
        with LeaseRenewer(SqlAlchemyUnitOfWorkFactory(factory), lease, duration=timedelta(seconds=0.03)) as renewer:
            assert renewer.lost.wait(2)
    runtime.force_flush()
    assert value(reader, "recruitmatch.lease.renew_failure") == 1
    span = next(span for span in exporter.get_finished_spans() if span.name == "lease.renew")
    assert span.context.trace_id == parent.trace_id
    assert span.parent.span_id == parent.span_id


@pytest.mark.parametrize("reason", ["lease_expired", "retry_due"])
def test_beat_recovery_has_fresh_root_and_counts_committed_takeover(processing_source, telemetry, reason):
    from app.processing.recovery import RecoveryScanner
    from app.processing.recovery_repository import RecoveryRepository
    from app.observability.events import operation
    from opentelemetry import trace

    factory, _, source, model = processing_source
    runtime, exporter, reader = telemetry
    test_processors.claim(processing_source)
    with factory() as session:
        row = session.get(model, source.source_id)
        row.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if reason == "retry_due":
            row.status = "failed"
            row.error_code = "storage_unavailable"
            row.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            row.processing_lease_owner = row.processing_lease_expires_at = None
        session.commit()

    class Dispatcher:
        def dispatch_resume(self, *args):
            with operation("resume.process"):
                pass

        dispatch_knowledge = dispatch_resume

    with operation("resume.upload"):
        api_trace = trace.get_current_span().get_span_context().trace_id
        report = RecoveryScanner(RecoveryRepository(factory), Dispatcher()).run_once()
    assert report.dispatched == 1
    assert value(reader, "recruitmatch.lease.takeover") == (1 if reason == "lease_expired" else 0)
    runtime.force_flush()
    recovered = next(span for span in exporter.get_finished_spans() if span.name == "beat.recover")
    assert recovered.parent is None
    assert recovered.context.trace_id != api_trace
    assert recovered.attributes["recovery.reason"] == reason


def test_actual_redis_commands_are_traced_without_keys(telemetry):
    import os
    import redis

    runtime, exporter, _ = telemetry
    client = redis.Redis.from_url(os.environ["CELERY_BROKER_URL"])
    try:
        assert client.get("synthetic-PRIVATE-nonexistent") is None
        runtime.force_flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1 and spans[0].kind.name == "CLIENT"
        assert "PRIVATE" not in spans[0].to_json()
    finally:
        client.close()
