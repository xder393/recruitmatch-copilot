"""Real SDK/OTLP snapshot owners, real PG/Redis observations, isolated namespace."""

import os
import time
import uuid
from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.observability.events import operation
from app.observability.instrumentation import activate
from app.observability.operations import OperationalMetrics
from app.observability.otel import Observability
from app.operations.heartbeats import OperationsHeartbeats
from scripts.verify_telemetry import eventually, query, spans, trace


LATEST = 'recruitmatch:worker_live:latest{deployment_environment_name="staging"}'
RAW = 'recruitmatch_worker_live{job="recruitmatch-beat",deployment_environment_name="staging"}'


def snapshot_visible(writer, expected, observed_after):
    """Reject an older owner/sample or a recording rule not yet evaluated after it."""
    newest = query(f"topk(1,timestamp({RAW}))")
    rows = query(LATEST)
    recorded = query(f"timestamp({LATEST})")
    return (
        len(newest) == len(rows) == len(recorded) == 1
        and newest[0]["metric"]["instance"] == writer
        and float(newest[0]["value"][1]) >= observed_after
        and float(recorded[0]["value"][1]) >= float(newest[0]["value"][1])
        and float(rows[0]["value"][1]) == expected
    )


def observe_snapshot(runtime, poller, writer, expected):
    # The SDK replaces the snapshot during poll(), not when Prometheus later
    # observes it. Bracket that source observation before any export/query delay.
    source_started = time.monotonic()
    poller.poll()
    observed_after = time.time()
    runtime.force_flush()
    eventually(lambda: snapshot_visible(writer, expected, observed_after), label="fresh_writer_snapshot")
    return source_started


def producer_writer(trace_id):
    output = spans(trace(trace_id))
    assert output, "missing_gauge_producer_span"
    return output[0]["resource"]["service.instance.id"]


def verify_gauges():
    from opentelemetry import trace as otel_trace

    settings = replace(
        Settings.load(),
        telemetry_enabled=True,
        telemetry_service_name="recruitmatch-beat",
        telemetry_environment="staging",
        otel_exporter_otlp_endpoint="http://otel-collector:4317",
    )
    engine = create_engine("postgresql+psycopg://recruitmatch:recruitmatch@postgres:5432/recruitmatch")
    heartbeats = OperationsHeartbeats("redis://redis:6379/0", namespace="recruitmatch:cp5:" + uuid.uuid4().hex)
    heartbeat_identity = uuid.uuid4().hex
    runtimes = []

    def producer():
        runtime = Observability(settings)
        runtimes.append(runtime)
        with activate(runtime), operation("beat.recover", {"recovery.reason": "queued_stale"}):
            trace_id = f"{otel_trace.get_current_span().get_span_context().trace_id:032x}"
        runtime.force_flush()
        writer = eventually(lambda: producer_writer(trace_id), label="gauge_writer")
        return runtime, writer, OperationalMetrics(runtime, sessionmaker(engine), heartbeats)

    try:
        heartbeats.pulse_worker(heartbeat_identity)
        heartbeats.pulse_beat()
        first, first_id, poller = producer()

        heartbeats.pulse_worker(heartbeat_identity)
        observe_snapshot(first, poller, first_id, 1)
        first.shutdown()
        heartbeats.remove_worker(heartbeat_identity)
        second, second_id, replacement = producer()
        assert first_id != second_id, "gauge_replacement_writer_collision"

        observe_snapshot(second, replacement, second_id, 0)
        assert query(f'recruitmatch_worker_live{{instance="{first_id}"}}'), "missing_positive_predecessor"
        # A failed real Redis connection invalidates only this observation family.
        heartbeats.url = "redis://127.0.0.1:9/0"
        replacement.poll()
        second.force_flush()
        eventually(lambda: not query(LATEST), seconds=110, label="failed_dependency_is_unknown")
        heartbeats.url = "redis://redis:6379/0"
        source_started = observe_snapshot(second, replacement, second_id, 0)
        # Stop source polls but leave SDK exports running. Export time cannot keep
        # an expired 25s source snapshot alive forever.
        eventually(lambda: not query(LATEST), seconds=110, label="unpolled_snapshot_expires")
        source_elapsed = time.monotonic() - source_started
        assert source_elapsed >= 25, f"snapshot_disappeared_before_source_ttl: elapsed={source_elapsed:.3f}"
        print(
            {
                "gauge_writers": [first_id, second_id],
                "positive_to_replacement_zero": True,
                "failed_dependency_unknown": True,
                "snapshot_expiry_unknown": True,
                "source_to_unknown_seconds": round(source_elapsed, 3),
            }
        )
        return True
    finally:
        heartbeats.url = "redis://redis:6379/0"
        heartbeats.remove_worker(heartbeat_identity)
        for runtime in runtimes:
            runtime.shutdown()
        engine.dispose()


if __name__ == "__main__":
    assert os.environ.get("TELEMETRY_E2E") == "1"
    verify_gauges()
