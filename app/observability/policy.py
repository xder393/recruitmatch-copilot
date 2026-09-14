"""Default-deny telemetry policy, enforced before the SDK receives any data.

Framework instrumentors must receive these providers explicitly. Route templates
and configured model names are trusted composition inputs, never request values.
"""

from __future__ import annotations

import math
import re
from contextlib import ContextDecorator
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Mapping, cast

from opentelemetry import metrics, trace

from app.config import Settings


OPERATIONS = frozenset(
    {
        "resume.upload",
        "artifact.put",
        "artifact.get",
        "artifact.delete",
        "resume.process",
        "resume.parse",
        "embedding.generate",
        "vector.index",
        "vector.search",
        "matching.run",
        "model.generate",
        "citation.validate",
        "lease.claim",
        "lease.renew",
        "lease.finalize",
        "beat.recover",
    }
)

# Instrument type and UCUM unit; this is also the domain-event name registry.
INSTRUMENTS = {
    "task.started": ("counter", "{task}"),
    "task.completed": ("counter", "{task}"),
    "task.failed": ("counter", "{task}"),
    "task.retry": ("counter", "{task}"),
    "task.duration": ("histogram", "s"),
    "queue.depth": ("gauge", "{source}"),
    "queue.oldest_age": ("gauge", "s"),
    "lease.takeover": ("counter", "{takeover}"),
    "lease.renew_failure": ("counter", "{failure}"),
    "worker.live": ("gauge", "{worker}"),
    "worker.oldest_heartbeat_age": ("gauge", "s"),
    "beat.tick_age": ("gauge", "s"),
    "artifact.operation": ("counter", "{operation}"),
    "artifact.operation.duration": ("histogram", "s"),
    "artifact.cleanup_pending": ("gauge", "{artifact}"),
    "vector.search.duration": ("histogram", "s"),
    "vector.search.results": ("histogram", "{result}"),
    "vector.indexed_chunks": ("counter", "{chunk}"),
    "model.request": ("counter", "{request}"),
    "model.duration": ("histogram", "s"),
    "model.tokens": ("counter", "{token}"),
    "model.fallback": ("counter", "{fallback}"),
    "model.schema_failure": ("counter", "{failure}"),
    "citation.rejection": ("counter", "{rejection}"),
    "match.completed": ("counter", "{match}"),
    "match.duration": ("histogram", "s"),
    "match.score": ("histogram", "1"),
}
EVENT_ALIASES = {"matching.completed": "match.completed"}
EVENT_NAMES = frozenset(INSTRUMENTS) | frozenset(EVENT_ALIASES)
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE", "_OTHER"})
ERROR_CODES = frozenset(
    {
        "internal_error",
        "model_timeout",
        "timeout",
        "authentication_failed",
        "rate_limited",
        "transport_error",
        "provider_unavailable",
        "provider_rejected",
        "provider_error",
        "invalid_output",
        "schema_failure",
        "storage_unavailable",
        "embedding_failed",
        "processing_timeout",
        "processing_attempts_exhausted",
        "processing_dispatch_unavailable",
        "task_dispatch_failed",
        "artifact_missing",
        "artifact_access_denied",
        "artifact_checksum_mismatch",
        "artifact_length_mismatch",
        "artifact_too_large",
        "artifact_storage_failure",
        "artifact_invalid_location",
        "artifact_error",
        "artifact_cleanup_pending",
        "lease_lost",
        "citation_rejected",
        "invalid_citation",
        "citation_not_found",
        "generation_inconsistent",
        "validation_error",
        "unsupported_file",
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
    }
)
STANDARD_INSTRUMENTS = {
    "http.server.request.duration": ("histogram", "s"),
    "http.client.request.duration": ("histogram", "s"),
    "http.server.active_requests": ("up_down_counter", "{request}"),
}


def finite_observation(value: Any, *, signed: bool = False) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and (signed or value >= 0)
    except (OverflowError, TypeError):
        return False


class TelemetryPolicy:
    def __init__(self, settings: Settings, *, route_templates: frozenset[str] = frozenset()):
        self.route_templates = route_templates
        # Resource() bypasses the SDK environment/host/process detectors entirely.
        self.resource_attributes = {
            "service.name": settings.telemetry_service_name,
            "deployment.environment.name": settings.telemetry_environment,
        }
        configured_models = {settings.chat_model, settings.embedding_model}
        self.allowed = {
            "service.name": {settings.telemetry_service_name},
            "deployment.environment.name": {settings.telemetry_environment},
            "http.route": route_templates,
            "http.request.method": HTTP_METHODS,
            "task.type": {"resume", "knowledge", "resume.process", "knowledge.process", "recovery", "cleanup"},
            "source.type": {"resume", "knowledge", "knowledge_document"},
            "operation": OPERATIONS | {"put", "get", "delete", "head", "list", "claim", "renew", "finalize"},
            "outcome": {
                "success",
                "failure",
                "error",
                "fallback",
                "retry",
                "deferred",
                "duplicate_active",
                "terminal",
                "lease_lost",
                "rejected",
                "skipped",
                "empty",
                "claimed",
            },
            "error.code": ERROR_CODES,
            "model.provider": {"openai-compatible", "local", "bge"},
            "model.name": {name for name in configured_models if re.fullmatch(r"[A-Za-z0-9_./-]{1,100}", name)},
            "match.mode": {"rules", "semantic", "hybrid", "rules_fallback"},
            "retrieval.strategy": {"pgvector", "semantic", "hybrid", "cosine", "exact"},
            "recovery.reason": {"queued_stale", "lease_expired", "cleanup_pending", "generation_inconsistent"},
        }

    def attributes(self, attributes: Mapping[str, Any] | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in (attributes or {}).items():
            if key == "http.response.status_code":
                if type(value) is int and 100 <= value <= 599:
                    result[key] = value
            elif type(value) is str and value in self.allowed.get(key, ()):
                result[key] = value
        return result

    def span_name(self, name: str) -> str:
        if name in OPERATIONS or name in EVENT_NAMES or name in HTTP_METHODS:
            return name
        method, _, route = name.partition(" ")
        if method in HTTP_METHODS and route in self.route_templates:
            return name
        return "internal.operation"


class SafeSpan(trace.Span):
    def __init__(self, span: trace.Span, policy: TelemetryPolicy):
        self._span, self._policy = span, policy

    def end(self, end_time=None):
        self._span.end(end_time)

    def get_span_context(self):
        return self._span.get_span_context()

    def is_recording(self):
        return self._span.is_recording()

    def set_attribute(self, key, value):
        self.set_attributes({key: value})

    def set_attributes(self, attributes):
        self._span.set_attributes(self._policy.attributes(attributes))

    def update_name(self, name):
        self._span.update_name(self._policy.span_name(name))

    def add_event(self, name, attributes=None, timestamp=None):
        if name in EVENT_NAMES:
            self._span.add_event(name, self._policy.attributes(attributes), timestamp)

    def add_link(self, context, attributes=None):
        self._span.add_link(_link_context(context), self._policy.attributes(attributes))

    def set_status(self, status, description=None):
        code = status.status_code if isinstance(status, trace.Status) else status
        self._span.set_status(trace.Status(code))

    def record_exception(self, exception, attributes=None, timestamp=None, escaped=False):
        # Error status is useful; exception payloads and stack traces are not collected.
        self.set_status(trace.StatusCode.ERROR)


def _link_context(context: trace.SpanContext) -> trace.SpanContext:
    return trace.SpanContext(context.trace_id, context.span_id, context.is_remote, context.trace_flags)


class _SpanScope(ContextDecorator):
    """Keep a safe span current for the full call, including decorated coroutines."""

    def __init__(self, factory, end_on_exit, set_status_on_exception):
        self._factory = factory
        self._end_on_exit = end_on_exit
        self._set_status_on_exception = set_status_on_exception

    def __enter__(self):
        self._context = trace.use_span(
            self._factory(),
            end_on_exit=self._end_on_exit,
            record_exception=False,
            set_status_on_exception=self._set_status_on_exception,
        )
        return self._context.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self._context.__exit__(exc_type, exc_value, traceback)

    def _recreate_cm(self):
        return _SpanScope(self._factory, self._end_on_exit, self._set_status_on_exception)

    def __call__(self, function):
        if not iscoroutinefunction(function):
            return super().__call__(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            with self._recreate_cm():
                return await function(*args, **kwargs)

        return wrapped


class SafeTracer(trace.Tracer):
    def __init__(self, tracer: trace.Tracer, policy: TelemetryPolicy):
        self._tracer, self._policy = tracer, policy

    def start_span(
        self,
        name,
        context=None,
        kind=trace.SpanKind.INTERNAL,
        attributes=None,
        links=None,
        start_time=None,
        record_exception=True,
        set_status_on_exception=True,
    ):
        safe_links = [
            trace.Link(_link_context(link.context), self._policy.attributes(link.attributes)) for link in (links or ())
        ]
        return SafeSpan(
            self._tracer.start_span(
                self._policy.span_name(name),
                context=context,
                kind=kind,
                attributes=self._policy.attributes(attributes),
                links=safe_links,
                start_time=start_time,
                record_exception=False,
                set_status_on_exception=False,
            ),
            self._policy,
        )

    def start_as_current_span(
        self,
        name,
        context=None,
        kind=trace.SpanKind.INTERNAL,
        attributes=None,
        links=None,
        start_time=None,
        record_exception=True,
        set_status_on_exception=True,
        end_on_exit=True,
    ) -> Any:
        # The upstream annotation names a private concrete context-manager class;
        # preserve its public context/decorator contract without importing it.
        return _SpanScope(
            lambda: self.start_span(name, context, kind, attributes, links, start_time),
            end_on_exit,
            set_status_on_exception,
        )


class SafeTracerProvider(trace.TracerProvider):
    def __init__(self, provider: trace.TracerProvider, policy: TelemetryPolicy):
        self._provider, self._policy = provider, policy

    def get_tracer(
        self, instrumenting_module_name, instrumenting_library_version=None, schema_url=None, attributes=None
    ):
        # The instrumentation scope itself must not become a free-form data channel.
        return SafeTracer(self._provider.get_tracer("recruitmatch", "0.2.0"), self._policy)


class SafeInstrument:
    def __init__(self, instrument: Any, policy: TelemetryPolicy, *, signed: bool = False):
        self._instrument, self._policy, self._signed = instrument, policy, signed

    def add(self, amount, attributes=None, context=None):
        if self._instrument is not None and finite_observation(amount, signed=self._signed):
            self._instrument.add(amount, self._policy.attributes(attributes), context)

    def record(self, amount, attributes=None, context=None):
        if self._instrument is not None and finite_observation(amount):
            self._instrument.record(amount, self._policy.attributes(attributes), context)

    def set(self, amount, attributes=None, context=None):
        if self._instrument is not None and finite_observation(amount):
            self._instrument.set(amount, self._policy.attributes(attributes), context)


class SafeMeter:
    def __init__(self, meter: metrics.Meter, policy: TelemetryPolicy):
        self._meter, self._policy = meter, policy

    def _create(self, name, kind, unit, **kwargs):
        domain_name = name.removeprefix("recruitmatch.")
        expected = INSTRUMENTS.get(domain_name) if name.startswith("recruitmatch.") else STANDARD_INSTRUMENTS.get(name)
        instrument = None
        if expected is not None and expected[0] == kind:
            instrument = getattr(self._meter, f"create_{kind}")(name, unit=expected[1], **kwargs)
        return SafeInstrument(instrument, self._policy, signed=kind == "up_down_counter")

    def create_counter(self, name, unit="", description=""):
        return self._create(name, "counter", unit)

    def create_up_down_counter(self, name, unit="", description=""):
        return self._create(name, "up_down_counter", unit)

    def create_histogram(self, name, unit="", description="", *, explicit_bucket_boundaries_advisory=None):
        return self._create(name, "histogram", unit)

    def create_gauge(self, name, unit="", description=""):
        return self._create(name, "gauge", unit)

    # Domain gauges are synchronous snapshots; no approved automatic observable
    # instrument exists yet. Unknown callbacks must never execute or collect PII.
    def create_observable_counter(self, name, callbacks=None, unit="", description=""):
        return None

    def create_observable_up_down_counter(self, name, callbacks=None, unit="", description=""):
        return None

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        return None


class SafeMeterProvider(metrics.MeterProvider):
    def __init__(self, provider: metrics.MeterProvider, policy: TelemetryPolicy):
        self._provider, self._policy = provider, policy

    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        return cast(metrics.Meter, SafeMeter(self._provider.get_meter("recruitmatch", "0.2.0"), self._policy))
