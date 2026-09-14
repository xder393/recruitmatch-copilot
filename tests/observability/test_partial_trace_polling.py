"""Ingestion may deliver spans incrementally; missing required spans must retry."""

import base64

import pytest

from scripts import verify_telemetry as gate
from tests.observability import gauge_probe


def trace_body(*, server=True):
    def encoded(value):
        return base64.b64encode(bytes.fromhex(value)).decode()

    names = [
        "http",
        "publish",
        "consume",
        "resume.process",
        "model.generate",
        "resume.upload",
        "artifact.put",
        "artifact.get",
        "resume.parse",
        "embedding.generate",
        "vector.index",
        "lease.claim",
        "lease.finalize",
    ]
    output = []
    for index, name in enumerate(names, 1):
        span = {
            "name": name,
            "spanId": encoded(f"{index:016x}"),
            "traceId": encoded("1" * 32),
            "parentSpanId": encoded("2" * 16 if index == 1 else f"{index - 1:016x}"),
            "kind": {1: "SPAN_KIND_SERVER", 2: "SPAN_KIND_PRODUCER", 3: "SPAN_KIND_CONSUMER"}.get(
                index, "SPAN_KIND_INTERNAL"
            ),
        }
        if index == 1 and not server:
            continue
        output.append(
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "recruitmatch-api" if index < 3 else "recruitmatch-worker"},
                        },
                        {"key": "service.instance.id", "value": {"stringValue": "api" if index < 3 else "worker"}},
                    ]
                },
                "scopeSpans": [{"spans": [span]}],
            }
        )
    return {"batches": output}


def test_missing_server_retries_then_checks_real_parentage():
    responses = iter([trace_body(server=False), trace_body()])
    assert gate.eventually(lambda: gate.verify_parentage(next(responses), "1" * 32, "2" * 16), interval=0) == (
        "worker",
        "api",
    )


def test_missing_server_deadline_is_sanitized():
    with pytest.raises(AssertionError, match="parentage: bounded deadline exceeded"):
        gate.eventually(
            lambda: gate.verify_parentage(trace_body(server=False), "1" * 32, "2" * 16), seconds=0, label="parentage"
        )


def test_empty_gauge_trace_retries_until_producer_exists(monkeypatch):
    responses = iter([{}, trace_body()])
    monkeypatch.setattr(gauge_probe, "trace", lambda trace_id: next(responses))
    assert gate.eventually(lambda: gauge_probe.producer_writer("1" * 32), interval=0) == "api"


def test_empty_gauge_trace_deadline_is_sanitized(monkeypatch):
    monkeypatch.setattr(gauge_probe, "trace", lambda trace_id: {})
    with pytest.raises(AssertionError, match="gauge_writer: bounded deadline exceeded"):
        gate.eventually(lambda: gauge_probe.producer_writer("1" * 32), seconds=0, label="gauge_writer")
