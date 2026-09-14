"""Bounded, optional OpenTelemetry adapter and process-local lifecycle."""

from __future__ import annotations

import os
import logging
import time
from contextlib import contextmanager
from threading import Lock, Thread
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.config import Settings
from app.observability.events import DomainEvent, DomainEventRecorder, NoopDomainEventRecorder
from app.observability.policy import (
    EVENT_ALIASES,
    INSTRUMENTS,
    OPERATIONS,
    SafeMeterProvider,
    SafeTracerProvider,
    TelemetryPolicy,
    finite_observation,
)

EXPORT_TIMEOUT_SECONDS = 2
SHUTDOWN_TIMEOUT_SECONDS = 2
MAX_QUEUE_SIZE = 2048
MAX_BATCH_SIZE = 256

DURATION_EVENTS = {
    "resume.process": "task.duration",
    "artifact.put": "artifact.operation.duration",
    "artifact.get": "artifact.operation.duration",
    "artifact.delete": "artifact.operation.duration",
    "vector.search": "vector.search.duration",
    "matching.run": "match.duration",
    "model.generate": "model.duration",
}


class _ExporterLogPrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # The OTLP delegate logs inside export(), before our exception boundary.
        # Keep severity but collect neither exception text nor configured endpoints.
        record.msg = "otel_exporter_diagnostic"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_exporter_log_filter = _ExporterLogPrivacyFilter()


def _configure_exporter_logging() -> None:
    # Public, exact-logger configuration only. This process-wide privacy rule is
    # idempotent and deliberately outlives individual runtimes: other owners and
    # bounded-shutdown background work must never lose protection during teardown.
    logging.getLogger("opentelemetry.exporter.otlp.proto.grpc.exporter").addFilter(_exporter_log_filter)


class _QuietSpanExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter):
        self._exporter = exporter
        self._pid = os.getpid()

    def export(self, spans):
        if self._pid != os.getpid():
            return SpanExportResult.FAILURE
        try:
            return self._exporter.export(spans)
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self):
        try:
            self._exporter.shutdown()
        except Exception:
            pass


class _QuietMetricExporter(MetricExporter):
    def __init__(self, exporter: MetricExporter):
        super().__init__()
        self._exporter = exporter
        self._pid = os.getpid()

    def export(self, metrics_data, timeout_millis=10000, **kwargs):
        if self._pid != os.getpid():
            return MetricExportResult.FAILURE
        try:
            return self._exporter.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception:
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis=10000):
        return True

    def shutdown(self, timeout_millis=30000, **kwargs):
        try:
            self._exporter.shutdown(timeout_millis=timeout_millis, **kwargs)
        except Exception:
            pass


class OtelDomainEventRecorder:
    def __init__(
        self,
        tracer_provider: trace.TracerProvider,
        meter_provider: metrics.MeterProvider,
        policy: TelemetryPolicy | None = None,
    ):
        self._tracer = tracer_provider.get_tracer("recruitmatch")
        self._policy = policy or TelemetryPolicy(Settings())
        meter = meter_provider.get_meter("recruitmatch")
        self._instruments: dict[str, Any] = {
            name: getattr(meter, f"create_{kind}")(f"recruitmatch.{name}", unit=unit)
            for name, (kind, unit) in INSTRUMENTS.items()
        }

    def record(self, event: DomainEvent) -> None:
        name = EVENT_ALIASES.get(event.name, event.name)
        if name not in INSTRUMENTS or not finite_observation(event.value):
            return
        if name == "match.score" and event.value > 100:
            return
        try:
            attributes = self._policy.attributes(event.attributes)
            instrument = self._instruments[name]
            kind = INSTRUMENTS[name][0]
            getattr(instrument, {"counter": "add", "histogram": "record", "gauge": "set"}[kind])(
                event.value, attributes
            )
            current = trace.get_current_span()
            if current.is_recording():
                current.add_event(event.name, attributes)
            else:
                with self._tracer.start_as_current_span(event.name, attributes=attributes):
                    pass
        except Exception:
            # No telemetry failure is allowed to change a committed business result.
            pass

    @contextmanager
    def operation(self, name, attributes=None):
        if name not in OPERATIONS:
            yield
            return
        started = time.perf_counter()
        try:
            span = self._tracer.start_span(name, attributes=self._policy.attributes(attributes))
        except Exception:
            span = trace.INVALID_SPAN
        with trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
            try:
                yield
            except BaseException:
                try:
                    span.set_status(trace.StatusCode.ERROR)
                except Exception:
                    pass
                raise
            finally:
                if name in DURATION_EVENTS:
                    self.record(DomainEvent(DURATION_EVENTS[name], attributes or {}, time.perf_counter() - started))
                try:
                    span.end()
                except Exception:
                    pass


class Observability:
    """Own SDK providers per process; tests can inject independent in-memory sinks.

    Pass the public safe providers to instrumentors. Do not install the underlying
    SDK providers as globals: SDK globals are write-once and leak across app tests.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        span_exporter: SpanExporter | None = None,
        metric_reader: MetricReader | None = None,
        metric_exporter: MetricExporter | None = None,
        route_templates: frozenset[str] = frozenset(),
    ):
        self._closed = False
        self._trace_sdk: TracerProvider | None = None
        self._metric_sdk: MeterProvider | None = None
        self.recorder: DomainEventRecorder = NoopDomainEventRecorder()
        self.tracer_provider: trace.TracerProvider = trace.NoOpTracerProvider()
        self.meter_provider: metrics.MeterProvider = metrics.NoOpMeterProvider()
        self.policy = TelemetryPolicy(settings, route_templates=route_templates)
        if not settings.telemetry_enabled:
            return
        settings.validate()
        _configure_exporter_logging()
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "http"
        # Never enable header/body/SQL capture based on ambient instrumentation env.
        os.environ["OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST"] = ""
        os.environ["OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE"] = ""
        resource = Resource(self.policy.resource_attributes)
        try:
            self._trace_sdk = TracerProvider(
                resource=resource,
                sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_ratio)),
                span_limits=SpanLimits(max_attributes=16, max_events=32, max_links=16, max_attribute_length=120),
                shutdown_on_exit=False,
            )
            exporter = (
                span_exporter
                if span_exporter is not None
                else OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    timeout=EXPORT_TIMEOUT_SECONDS,
                    headers={},
                )
            )
            self._trace_sdk.add_span_processor(
                BatchSpanProcessor(
                    _QuietSpanExporter(exporter),
                    max_queue_size=MAX_QUEUE_SIZE,
                    max_export_batch_size=MAX_BATCH_SIZE,
                    schedule_delay_millis=200,
                    export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
                )
            )
            if metric_reader is None:
                if metric_exporter is None:
                    metric_exporter = OTLPMetricExporter(
                        endpoint=settings.otel_exporter_otlp_endpoint,
                        timeout=EXPORT_TIMEOUT_SECONDS,
                        headers={},
                    )
                metric_reader = PeriodicExportingMetricReader(
                    _QuietMetricExporter(metric_exporter),
                    export_interval_millis=5000,
                    export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
                )
            self._metric_sdk = MeterProvider(resource=resource, metric_readers=[metric_reader], shutdown_on_exit=False)
            self.tracer_provider = SafeTracerProvider(self._trace_sdk, self.policy)
            self.meter_provider = SafeMeterProvider(self._metric_sdk, self.policy)
            self.recorder = OtelDomainEventRecorder(self.tracer_provider, self.meter_provider, self.policy)
        except Exception:
            self.shutdown()

    def force_flush(self, timeout_millis: int = 2000) -> None:
        for provider in (self._trace_sdk, self._metric_sdk):
            if provider is not None:
                try:
                    provider.force_flush(timeout_millis=timeout_millis)
                except Exception:
                    pass

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.recorder = NoopDomainEventRecorder()
        if self._trace_sdk is None and self._metric_sdk is None:
            return

        def close():
            for provider in (self._metric_sdk, self._trace_sdk):
                if provider is not None:
                    try:
                        provider.shutdown()
                    except Exception:
                        pass

        # Even a broken third-party exporter cannot delay process shutdown forever.
        thread = Thread(target=close, name="recruitmatch-telemetry-shutdown", daemon=True)
        thread.start()
        thread.join(SHUTDOWN_TIMEOUT_SECONDS)


_runtimes: dict[object | None, tuple[Settings, frozenset[str], Observability]] = {}
_runtime_lock = Lock()


def _after_fork() -> None:
    # Children must configure their own runtime in Celery's process-init hook.
    # Never join inherited SDK threads or keep an inherited locked lifecycle lock.
    global _runtimes, _runtime_lock
    _runtimes = {}
    _runtime_lock = Lock()


os.register_at_fork(after_in_child=_after_fork)


def configure_observability(
    settings: Settings, *, owner: object | None = None, route_templates: frozenset[str] = frozenset()
) -> Observability:
    """Idempotent within an owner (FastAPI app or the default worker process)."""
    with _runtime_lock:
        previous = _runtimes.get(owner)
        if previous is not None and previous[:2] == (settings, route_templates) and not previous[2]._closed:
            return previous[2]
        runtime = Observability(settings, route_templates=route_templates)
        _runtimes[owner] = (settings, route_templates, runtime)
    if previous is not None:
        previous[2].shutdown()
    return runtime


def shutdown_observability(*, owner: object | None = None) -> None:
    with _runtime_lock:
        previous = _runtimes.pop(owner, None)
    if previous is not None:
        previous[2].shutdown()
