"""W3C-only transport context. Business payloads and baggage never propagate."""

from contextlib import contextmanager
import re

from opentelemetry.context import Context, attach, detach
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagators.textmap import TextMapPropagator, default_getter, default_setter


_W3C = TraceContextTextMapPropagator()
_PARENT = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}\Z")
_STATE_KEY = re.compile(r"(?:[a-z][_0-9a-z*/-]{0,255}|[a-z0-9][_0-9a-z*/-]{0,240}@[a-z][_0-9a-z*/-]{0,13})\Z")
_STATE_VALUE = re.compile(r"[\x20-\x2b\x2d-\x3c\x3e-\x7e]{0,255}[\x21-\x2b\x2d-\x3c\x3e-\x7e]\Z")


class W3COnlyPropagator(TextMapPropagator):
    @property
    def fields(self):
        return {"traceparent", "tracestate"}

    def extract(self, carrier, context=None, getter=default_getter):
        headers = {}
        for key in self.fields:
            values = getter.get(carrier, key)
            if values is None and isinstance(getattr(carrier, "headers", None), dict):
                values = default_getter.get(carrier.headers, key)
            if values and len(values) == 1:
                headers[key] = values[0]
        return _W3C.extract(validated_headers(headers), context=Context())

    def inject(self, carrier, context=None, setter=default_setter):
        if isinstance(carrier, dict):
            carrier.pop("baggage", None)
        headers = {}
        _W3C.inject(headers, context=context)
        for key, value in validated_headers(headers).items():
            setter.set(carrier, key, value)


def trace_headers() -> dict[str, str]:
    carrier: dict[str, str] = {}
    _W3C.inject(carrier)
    return validated_headers(carrier)


def validated_headers(carrier) -> dict[str, str]:
    if not isinstance(carrier, dict):
        return {}
    parent = carrier.get("traceparent")
    if not isinstance(parent, str) or not _PARENT.fullmatch(parent):
        return {}
    if parent[3:35] == "0" * 32 or parent[36:52] == "0" * 16:
        return {}
    result = {"traceparent": parent}
    state = carrier.get("tracestate")
    if isinstance(state, str) and len(state) <= 512:
        # Validate W3C member grammar before the public parser: its invalid-input
        # diagnostic otherwise echoes untrusted header text, even without logging setup.
        members = [member.strip(" \t") for member in state.split(",") if member.strip(" \t")]
        pairs = [member.partition("=") for member in members]
        if (
            len(pairs) <= 32
            and len({key for key, _, _ in pairs}) == len(pairs)
            and all(
                _STATE_KEY.fullmatch(key) and equals and _STATE_VALUE.fullmatch(value) for key, equals, value in pairs
            )
        ):
            context = _W3C.extract({"traceparent": parent, "tracestate": state}, context=Context())
            from opentelemetry.trace import get_current_span

            parsed = get_current_span(context).get_span_context().trace_state.to_header()
            if parsed:
                result["tracestate"] = parsed
    return result


@contextmanager
def extracted_context(carrier=None):
    token = attach(_W3C.extract(validated_headers(carrier), context=Context()))
    try:
        yield
    finally:
        detach(token)
