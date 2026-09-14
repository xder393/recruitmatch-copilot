"""PII-negative tests include positive SDK output to detect a disabled adapter."""

import pytest

from app.observability.otel import Observability
from app.config import Settings
from app.observability.events import DomainEvent
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def telemetry():
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
    yield runtime, exporter, reader
    runtime.shutdown()


@pytest.mark.parametrize(
    "key",
    [
        "tenant_id",
        "url.full",
        "url.query",
        "db.statement",
        "db.query.text",
        "exception.message",
        "exception.stacktrace",
        "messaging.message.id",
        "filename",
        "prompt",
        "model.response",
        "user_id",
        "artifact_id",
    ],
)
def test_forbidden_attributes_are_dropped(telemetry, key):
    runtime, exporter, reader = telemetry
    runtime.recorder.record(DomainEvent("matching.completed", {key: "private@example.com", "outcome": "success"}))
    runtime.force_flush()
    span = exporter.get_finished_spans()[0]
    assert span.attributes == {"outcome": "success"}
    data = reader.get_metrics_data()
    assert "recruitmatch.match.completed" in repr(data)
    assert "private@example.com" not in repr(data)


def test_automatic_span_is_sanitized_before_and_after_start(telemetry):
    runtime, exporter, _ = telemetry
    tracer = runtime.tracer_provider.get_tracer("opentelemetry.instrumentation.httpx", attributes={"secret": "pii"})
    link_context = trace.SpanContext(1, 2, True, trace.TraceFlags(1), trace.TraceState([("secret", "pii")]))
    with tracer.start_as_current_span(
        "https://private@example.com?a=secret",
        attributes={"url.full": "secret", "http.request.method": "GET"},
        links=[trace.Link(link_context, {"secret": "pii", "outcome": "success"})],
    ) as span:
        assert span.is_recording()
        span.set_attribute("db.statement", "secret")
        span.set_attributes({"tenant_id": "secret", "http.response.status_code": 200})
        span.update_name("secret@example.com")
        span.add_event("secret@example.com", {"secret": "pii"})
        span.add_event("model.fallback", {"exception.message": "secret", "error.code": "model_timeout"})
        span.set_status(trace.Status(trace.StatusCode.ERROR, "secret@example.com"))
        span.record_exception(ValueError("secret@example.com"))
        span.add_link(link_context, {"secret": "pii", "outcome": "success"})
    runtime.force_flush()
    exported = exporter.get_finished_spans()[0]
    assert exported.name == "internal.operation"
    assert exported.attributes == {"http.request.method": "GET", "http.response.status_code": 200}
    assert exported.status.description is None
    assert [(e.name, dict(e.attributes)) for e in exported.events] == [
        ("model.fallback", {"error.code": "model_timeout"})
    ]
    assert all(link.attributes == {"outcome": "success"} for link in exported.links)
    assert all(not link.context.trace_state for link in exported.links)
    assert "secret" not in exported.to_json()


@pytest.mark.parametrize(
    "key",
    [
        "outcome",
        "operation",
        "error.code",
        "http.route",
        "task.type",
        "source.type",
        "model.name",
        "model.provider",
        "recovery.reason",
        "service.name",
    ],
)
def test_allowed_keys_reject_unbounded_values(telemetry, key):
    runtime, exporter, reader = telemetry
    runtime.recorder.record(DomainEvent("model.request", {key: "private@example.com", "match.mode": "rules"}))
    runtime.force_flush()
    assert exporter.get_finished_spans()[0].attributes == {"match.mode": "rules"}
    assert "private@example.com" not in repr(reader.get_metrics_data())


def test_resources_ignore_environment_detectors(telemetry, monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "host.name=secret,service.name=private@example.com")
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
    try:
        runtime.recorder.record(DomainEvent("model.request", {}))
        runtime.force_flush()
        resource = exporter.get_finished_spans()[0].resource
        assert dict(resource.attributes) == {
            "service.name": "recruitmatch-api",
            "deployment.environment.name": "development",
        }
        assert "secret" not in repr(reader.get_metrics_data())
    finally:
        runtime.shutdown()


def test_automatic_metric_labels_and_instrument_names_are_bounded(telemetry):
    runtime, _, reader = telemetry
    meter = runtime.meter_provider.get_meter("opentelemetry.instrumentation.httpx")
    histogram = meter.create_histogram("http.client.request.duration", unit="s")
    histogram.record(
        0.2, {"url.full": "private@example.com", "http.request.method": "GET", "outcome": "private@example.com"}
    )
    meter.create_counter("private@example.com").add(1, {"outcome": "success"})
    data = reader.get_metrics_data()
    assert "http.client.request.duration" in repr(data)
    assert "GET" in repr(data)
    assert "private@example.com" not in repr(data)


def test_domain_event_sanitizes_an_existing_foreign_span(telemetry):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    runtime, _, reader = telemetry
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        with provider.get_tracer("foreign").start_as_current_span("foreign"):
            runtime.recorder.record(DomainEvent("model.request", {"tenant_id": "secret", "outcome": "success"}))
        assert exporter.get_finished_spans()[0].events[0].attributes == {"outcome": "success"}
        assert "secret" not in repr(reader.get_metrics_data())
    finally:
        provider.shutdown()


def test_safe_tracer_preserves_async_decorator_parentage(telemetry):
    import asyncio

    runtime, exporter, _ = telemetry
    tracer = runtime.tracer_provider.get_tracer("application")

    @tracer.start_as_current_span("matching.run")
    async def match():
        await asyncio.sleep(0)
        with tracer.start_as_current_span("model.generate"):
            pass

    asyncio.run(match())
    asyncio.run(match())
    runtime.force_flush()
    spans = exporter.get_finished_spans()
    parents = {span.context.span_id: span for span in spans if span.name == "matching.run"}
    children = [span for span in spans if span.name == "model.generate"]
    assert len(parents) == len(children) == 2
    for child in children:
        assert child.parent is not None
        assert child.parent.span_id in parents
        assert child.end_time <= parents[child.parent.span_id].end_time
