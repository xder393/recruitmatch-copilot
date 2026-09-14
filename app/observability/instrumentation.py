"""Public framework instrumentation with live owner routing, including prefork."""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from weakref import WeakKeyDictionary, ref

from opentelemetry import metrics, trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from app.observability.context import W3COnlyPropagator, trace_headers, extracted_context
from app.observability.events import bind_recorder, NoopDomainEventRecorder
from app.observability.otel import Observability


_active: ContextVar[Observability | None] = ContextVar("telemetry_owner", default=None)


def current_runtime():
    runtime = _active.get()
    return runtime if runtime is not None and not runtime._closed else None


@contextmanager
def activate(runtime):
    token = _active.set(runtime)
    try:
        if runtime is None or runtime._closed:
            with bind_recorder(NoopDomainEventRecorder()):
                yield
        else:
            with bind_recorder(runtime.recorder, lambda fresh: _continuation(runtime, fresh)):
                yield
    finally:
        _active.reset(token)


def _continuation(runtime, fresh):
    headers = {} if fresh else trace_headers()

    @contextmanager
    def scope():
        with activate(runtime), extracted_context(headers):
            yield

    return scope


class _Tracer(trace.Tracer):
    def __init__(self, owner):
        self.owner = owner

    def _delegate(self):
        runtime = self.owner()
        provider = (
            runtime.tracer_provider if runtime is not None and not runtime._closed else trace.NoOpTracerProvider()
        )
        return provider.get_tracer("recruitmatch")

    def start_span(self, *args, **kwargs):
        return self._delegate().start_span(*args, **kwargs)

    def start_as_current_span(self, *args, **kwargs):
        return self._delegate().start_as_current_span(*args, **kwargs)


class _TracerProvider(trace.TracerProvider):
    def __init__(self, owner=current_runtime):
        self.owner = owner

    def get_tracer(self, *args, **kwargs):
        return _Tracer(self.owner)


class _Instrument:
    def __init__(self, owner, kind, args, kwargs):
        self.owner, self.kind, self.args, self.kwargs = owner, kind, args, kwargs
        self.cache = WeakKeyDictionary()

    def _observe(self, method, *args, **kwargs):
        runtime = self.owner()
        if runtime is None or runtime._closed:
            return
        instrument = self.cache.get(runtime)
        if instrument is None:
            meter = runtime.meter_provider.get_meter("recruitmatch")
            instrument = getattr(meter, self.kind)(*self.args, **self.kwargs)
            self.cache[runtime] = instrument
        if instrument is not None:
            getattr(instrument, method)(*args, **kwargs)

    def add(self, *args, **kwargs):
        self._observe("add", *args, **kwargs)

    def record(self, *args, **kwargs):
        self._observe("record", *args, **kwargs)

    def set(self, *args, **kwargs):
        self._observe("set", *args, **kwargs)


class _Meter:
    def __init__(self, owner):
        self.owner = owner

    def create_counter(self, *args, **kwargs):
        return _Instrument(self.owner, "create_counter", args, kwargs)

    def create_up_down_counter(self, *args, **kwargs):
        return _Instrument(self.owner, "create_up_down_counter", args, kwargs)

    def create_histogram(self, *args, **kwargs):
        return _Instrument(self.owner, "create_histogram", args, kwargs)

    def create_gauge(self, *args, **kwargs):
        return _Instrument(self.owner, "create_gauge", args, kwargs)


class _MeterProvider(metrics.MeterProvider):
    def __init__(self, owner=current_runtime):
        self.owner = owner

    def get_meter(self, *args, **kwargs):
        return _Meter(self.owner)


def instrument_frameworks():
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "http"
    set_global_textmap(W3COnlyPropagator())
    for instrumentor in (
        SQLAlchemyInstrumentor(),
        RedisInstrumentor(),
        HTTPXClientInstrumentor(),
        CeleryInstrumentor(),
    ):
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(tracer_provider=_TracerProvider(), meter_provider=_MeterProvider())


class _OwnerMiddleware:
    def __init__(self, app, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        with activate(self.owner()):
            await self.app(scope, receive, send)


def instrument_app(app):
    instrument_frameworks()
    weak_app = ref(app)

    def owner():
        current = weak_app()
        return getattr(current.state, "observability", None) if current is not None else None

    app.add_middleware(_OwnerMiddleware, owner=owner)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_TracerProvider(owner),
        meter_provider=_MeterProvider(owner),
        excluded_urls="",
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
        exclude_spans=["receive", "send"],
    )


def route_templates(app):
    # FastAPI's flattened OpenAPI paths include nested router templates.
    return frozenset(app.openapi().get("paths", {}))
