"""Real SDK contract tests; injected exporters never contact an OTLP backend."""

import math
import time
from dataclasses import replace

import pytest

from app.observability.otel import (
    Observability,
    OtelDomainEventRecorder,
    configure_observability,
    shutdown_observability,
)
from app.config import Settings
from app.observability.events import DomainEvent, NoopDomainEventRecorder
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
    yield runtime, exporter, reader
    runtime.shutdown()


def metrics(reader):
    data = reader.get_metrics_data()
    return (
        {
            metric.name: metric
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        if data
        else {}
    )


def test_domain_event_becomes_bounded_metric(telemetry):
    runtime, exporter, reader = telemetry
    assert isinstance(runtime.recorder, OtelDomainEventRecorder)
    runtime.recorder.record(DomainEvent("model.fallback", {"error.code": "model_timeout"}))
    point = metrics(reader)["recruitmatch.model.fallback"].data.data_points[0]
    assert point.value == 1
    assert point.attributes == {"error.code": "model_timeout"}
    runtime.force_flush()
    assert exporter.get_finished_spans()[0].name == "model.fallback"


@pytest.mark.parametrize(
    "event, instrument, unit, value, kind",
    [
        ("task.started", "recruitmatch.task.started", "{task}", 1, "counter"),
        ("task.completed", "recruitmatch.task.completed", "{task}", 1, "counter"),
        ("task.failed", "recruitmatch.task.failed", "{task}", 1, "counter"),
        ("task.retry", "recruitmatch.task.retry", "{task}", 1, "counter"),
        ("task.duration", "recruitmatch.task.duration", "s", 0.25, "histogram"),
        ("queue.depth", "recruitmatch.queue.depth", "{source}", 4, "gauge"),
        ("queue.oldest_age", "recruitmatch.queue.oldest_age", "s", 3, "gauge"),
        ("lease.takeover", "recruitmatch.lease.takeover", "{takeover}", 1, "counter"),
        ("lease.renew_failure", "recruitmatch.lease.renew_failure", "{failure}", 1, "counter"),
        ("worker.live", "recruitmatch.worker.live", "{worker}", 2, "gauge"),
        ("worker.oldest_heartbeat_age", "recruitmatch.worker.oldest_heartbeat_age", "s", 3, "gauge"),
        ("beat.tick_age", "recruitmatch.beat.tick_age", "s", 4, "gauge"),
        ("artifact.operation", "recruitmatch.artifact.operation", "{operation}", 1, "counter"),
        ("artifact.operation.duration", "recruitmatch.artifact.operation.duration", "s", 0.5, "histogram"),
        ("artifact.cleanup_pending", "recruitmatch.artifact.cleanup_pending", "{artifact}", 2, "gauge"),
        ("vector.search.duration", "recruitmatch.vector.search.duration", "s", 0.1, "histogram"),
        ("vector.search.results", "recruitmatch.vector.search.results", "{result}", 3, "histogram"),
        ("vector.indexed_chunks", "recruitmatch.vector.indexed_chunks", "{chunk}", 7, "counter"),
        ("model.request", "recruitmatch.model.request", "{request}", 1, "counter"),
        ("model.duration", "recruitmatch.model.duration", "s", 0.2, "histogram"),
        ("model.tokens", "recruitmatch.model.tokens", "{token}", 11, "counter"),
        ("model.schema_failure", "recruitmatch.model.schema_failure", "{failure}", 1, "counter"),
        ("citation.rejection", "recruitmatch.citation.rejection", "{rejection}", 1, "counter"),
        ("matching.completed", "recruitmatch.match.completed", "{match}", 1, "counter"),
        ("match.duration", "recruitmatch.match.duration", "s", 0.4, "histogram"),
        ("match.score", "recruitmatch.match.score", "1", 85, "histogram"),
    ],
)
def test_instrument_table_observes_values_and_units(telemetry, event, instrument, unit, value, kind):
    runtime, _, reader = telemetry
    runtime.recorder.record(DomainEvent(event, {"outcome": "success"}, value=value))
    metric = metrics(reader)[instrument]
    assert metric.unit == unit
    point = metric.data.data_points[0]
    assert (point.sum if kind == "histogram" else point.value) == value


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1, True])
def test_invalid_observations_do_not_pollute_metrics(telemetry, value):
    runtime, _, reader = telemetry
    runtime.recorder.record(DomainEvent("model.tokens", {}, value=value))
    runtime.recorder.record(DomainEvent("match.score", {}, value=value))
    assert metrics(reader) == {}


def test_unknown_event_and_out_of_range_score_are_dropped(telemetry):
    runtime, exporter, reader = telemetry
    runtime.recorder.record(DomainEvent("secret@example.com", {"outcome": "success"}))
    runtime.recorder.record(DomainEvent("match.score", {}, value=101))
    runtime.force_flush()
    assert metrics(reader) == {}
    assert exporter.get_finished_spans() == ()


@pytest.mark.parametrize("entry_point", ["recorder", "meter"])
def test_match_score_bounds_apply_to_both_public_entry_points(telemetry, entry_point):
    runtime, _, reader = telemetry
    histogram = runtime.meter_provider.get_meter("application").create_histogram("recruitmatch.match.score")
    for value in [0, 75, 100, 101, -1, math.nan, math.inf, -math.inf, True]:
        if entry_point == "recorder":
            runtime.recorder.record(DomainEvent("match.score", {"match.mode": "rules"}, value=value))
        else:
            histogram.record(value, {"match.mode": "rules"})
    point = metrics(reader)["recruitmatch.match.score"].data.data_points[0]
    assert point.count == 3
    assert point.sum == 175
    assert (point.min, point.max) == (0, 100)
    assert point.attributes == {"match.mode": "rules"}


def test_operation_records_duration_and_preserves_business_exception(telemetry):
    runtime, exporter, reader = telemetry
    with pytest.raises(ValueError, match="private business failure"):
        with runtime.recorder.operation("model.generate", {"model.provider": "openai-compatible"}):
            raise ValueError("private business failure")
    runtime.force_flush()
    span = exporter.get_finished_spans()[0]
    assert span.name == "model.generate"
    assert span.status.status_code.name == "ERROR"
    assert span.status.description is None
    assert [event.name for event in span.events] == ["model.duration"]
    assert "private business failure" not in span.to_json()
    assert metrics(reader)["recruitmatch.model.duration"].data.data_points[0].count == 1


def test_zero_sampling_keeps_metrics_and_respects_sampled_parent():
    from opentelemetry import trace

    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    runtime = Observability(
        Settings(telemetry_enabled=True, trace_sample_ratio=0), span_exporter=exporter, metric_reader=reader
    )
    try:
        runtime.recorder.record(DomainEvent("model.request", {}))
        parent = trace.NonRecordingSpan(trace.SpanContext(1, 2, True, trace.TraceFlags(1)))
        with trace.use_span(parent):
            runtime.recorder.record(DomainEvent("model.request", {}))
        runtime.force_flush()
        assert metrics(reader)["recruitmatch.model.request"].data.data_points[0].value == 2
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].parent.span_id == 2
    finally:
        runtime.shutdown()


def test_configuration_is_idempotent_and_restartable():
    shutdown_observability()
    try:
        settings = Settings(telemetry_enabled=False)
        first = configure_observability(settings)
        assert isinstance(first.recorder, NoopDomainEventRecorder)
        assert configure_observability(settings) is first
        with first.recorder.operation("model.generate", {}):
            first.recorder.record(DomainEvent("model.request", {}))
        shutdown_observability()
        shutdown_observability()
        assert configure_observability(settings) is not first
    finally:
        shutdown_observability()


def test_configuration_scopes_do_not_share_shutdown_or_stale_settings():
    first_owner, second_owner = object(), object()
    try:
        first = configure_observability(Settings(), owner=first_owner)
        second = configure_observability(Settings(), owner=second_owner)
        assert first is not second
        shutdown_observability(owner=first_owner)
        assert configure_observability(Settings(), owner=second_owner) is second
        changed = configure_observability(Settings(trace_sample_ratio=0.1), owner=second_owner)
        assert changed is not second
    finally:
        shutdown_observability(owner=first_owner)
        shutdown_observability(owner=second_owner)


def test_app_lifecycle_owns_its_runtime(tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.application import create_sqlite_test_app

    first = create_sqlite_test_app(Settings(database_url=f"sqlite:///{tmp_path}/first.db"))
    second = create_sqlite_test_app(Settings(database_url=f"sqlite:///{tmp_path}/second.db"))
    with TestClient(first), TestClient(second):
        assert first.state.observability is not second.state.observability
        assert isinstance(first.state.event_recorder, NoopDomainEventRecorder)


@pytest.mark.parametrize("ratio", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_sampling_config_is_rejected(ratio):
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        replace(Settings(), trace_sample_ratio=ratio).validate()


def test_environment_config_loads(monkeypatch):
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("TRACE_SAMPLE_RATIO", "0.1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    settings = Settings.load()
    assert settings.telemetry_enabled
    assert settings.trace_sample_ratio == 0.1
    assert settings.otel_exporter_otlp_endpoint == "http://otel-collector:4317"


def test_exporter_failure_does_not_escape_or_log_secrets(caplog):
    class FailedExporter(InMemorySpanExporter):
        def export(self, spans):
            raise RuntimeError("credential=secret")

    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=FailedExporter(), metric_reader=InMemoryMetricReader()
    )
    try:
        for _ in range(100):
            runtime.recorder.record(DomainEvent("model.request", {}))
        runtime.force_flush()
    finally:
        runtime.shutdown()
    assert "credential=secret" not in caplog.text


def test_periodic_metric_export_failure_preserves_business_result(caplog):
    from opentelemetry.sdk.metrics.export import MetricExporter

    class FailedMetricExporter(MetricExporter):
        def __init__(self):
            super().__init__()
            self.observed = []

        def export(self, metrics_data, timeout_millis=10000, **kwargs):
            self.observed.append(metrics_data)
            raise RuntimeError("credential=secret")

        def force_flush(self, timeout_millis=10000):
            return True

        def shutdown(self, timeout_millis=30000, **kwargs):
            pass

    exporter = FailedMetricExporter()
    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=InMemorySpanExporter(), metric_exporter=exporter
    )
    try:
        runtime.recorder.record(DomainEvent("model.request", {"outcome": "success"}))
        runtime.force_flush()
        metric = exporter.observed[0].resource_metrics[0].scope_metrics[0].metrics[0]
        assert metric.name == "recruitmatch.model.request"
        assert metric.data.data_points[0].value == 1
    finally:
        runtime.shutdown()
    assert "credential=secret" not in caplog.text


def test_real_otlp_failures_are_private_across_runtime_shutdown(monkeypatch, caplog):
    import logging

    from grpc import RpcError, StatusCode
    from opentelemetry.exporter.otlp.proto.grpc import metric_exporter, trace_exporter

    sentinel = "synthetic-credential-REVIEW-SENTINEL"
    requests = {"spans": [], "metrics": []}

    class UnknownRpcError(RpcError):
        def code(self):
            return StatusCode.UNKNOWN

        def trailing_metadata(self):
            return ()

    class FailedRpcStub:
        def __init__(self, kind):
            self.kind = kind

        def Export(self, request, metadata, timeout):
            requests[self.kind].append(request)
            raise UnknownRpcError(sentinel)

    # Replace only the generated RPC transport constructors. The real OTLP
    # translation, failure classification and logger all execute without a backend.
    monkeypatch.setattr(trace_exporter, "TraceServiceStub", lambda channel: FailedRpcStub("spans"))
    monkeypatch.setattr(metric_exporter, "MetricsServiceStub", lambda channel: FailedRpcStub("metrics"))

    def runtime():
        return Observability(
            Settings(telemetry_enabled=True),
            span_exporter=trace_exporter.OTLPSpanExporter(endpoint="http://127.0.0.1:1", timeout=0.01),
            metric_exporter=metric_exporter.OTLPMetricExporter(endpoint="http://127.0.0.1:1", timeout=0.01),
        )

    first, second = runtime(), runtime()
    try:
        first.recorder.record(DomainEvent("model.request", {"outcome": "success"}))
        first.force_flush()
        first.shutdown()
        second.recorder.record(DomainEvent("model.request", {"outcome": "success"}))
        second.force_flush()
    finally:
        first.shutdown()
        second.shutdown()

    spans = [
        span
        for request in requests["spans"]
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    metrics = [
        metric
        for request in requests["metrics"]
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    assert len(spans) == 2
    assert all(span.name == "model.request" for span in spans)
    assert metrics and all(metric.name == "recruitmatch.model.request" for metric in metrics)
    assert all(metric.sum.data_points[0].as_int == 1 for metric in metrics)

    # A timed-out shutdown may leave an exporter emitting a late diagnostic;
    # its logger remains protected even after every owning runtime has closed.
    logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").error("late %s", sentinel)
    logging.getLogger("app.unrelated").warning("application diagnostic %s", "preserved")
    assert "application diagnostic preserved" in caplog.text
    assert sentinel not in caplog.text
    exporter_records = [
        record for record in caplog.records if record.name == "opentelemetry.exporter.otlp.proto.grpc.exporter"
    ]
    assert exporter_records
    assert all(record.exc_info is None and record.exc_text is None for record in exporter_records)


def test_forked_worker_gets_a_new_process_runtime():
    import os

    owner = object()
    inherited = configure_observability(Settings(), owner=owner)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        runtime = configure_observability(Settings(), owner=owner)
        os.write(write_fd, b"fresh" if runtime is not inherited else b"inherited")
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 20) == b"fresh"
    finally:
        os.close(read_fd)
        os.waitpid(child, 0)
        shutdown_observability(owner=owner)


def test_stuck_exporter_does_not_block_shutdown():
    from threading import Event

    entered, release = Event(), Event()

    class HeldExporter(InMemorySpanExporter):
        def export(self, spans):
            entered.set()
            release.wait(10)
            return super().export(spans)

    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=HeldExporter(), metric_reader=InMemoryMetricReader()
    )
    try:
        runtime.recorder.record(DomainEvent("model.request", {}))
        assert entered.wait(2)
        started = time.monotonic()
        runtime.shutdown()
        assert time.monotonic() - started < 2.5
    finally:
        release.set()
        runtime.shutdown()


def test_saturated_exporter_never_blocks_recording():
    from threading import Event

    entered, release = Event(), Event()

    class HeldExporter(InMemorySpanExporter):
        def export(self, spans):
            entered.set()
            release.wait(5)
            return super().export(spans)

    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=HeldExporter(), metric_reader=InMemoryMetricReader()
    )
    try:
        runtime.recorder.record(DomainEvent("model.request", {}))
        assert entered.wait(2)
        started = time.monotonic()
        for _ in range(3000):
            runtime.recorder.record(DomainEvent("model.request", {}))
        assert time.monotonic() - started < 2
    finally:
        release.set()
        runtime.shutdown()
