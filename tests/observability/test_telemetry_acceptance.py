"""Backend-independent guard tests for bounded acceptance failures."""

import pytest
import json
import time
import os
import subprocess
import sys

from scripts.verify_telemetry import assert_private, eventually, verify_host_evidence


def test_explicit_gate_rejects_missing_host_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_HOST_EVIDENCE", str(tmp_path / "missing.json"))
    with pytest.raises(AssertionError, match="missing_host_evidence"):
        verify_host_evidence()


def test_private_payload_is_rejected_even_in_nonempty_nested_output():
    with pytest.raises(AssertionError, match="synthetic_privacy_leak"):
        assert_private({"resourceSpans": [{"attributes": {"body": "private-sentinel"}}]}, ["private-sentinel"])


def test_polling_reports_sanitized_finite_failure():
    def failing():
        raise AssertionError("private-sentinel")

    with pytest.raises(AssertionError, match="bounded deadline exceeded") as exc:
        eventually(failing, seconds=0, label="backend_missing")
    assert "private-sentinel" not in str(exc.value)


def test_host_evidence_from_a_different_source_image_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "host.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": time.time(),
                "source_digest": "stale-image",
                "collector_stopped": True,
                "collector_restored": True,
                "business_available": True,
                "logs_private": True,
                "restart_verified": True,
            }
        )
    )
    monkeypatch.setenv("TELEMETRY_HOST_EVIDENCE", str(path))
    with pytest.raises(AssertionError, match="source_identity"):
        verify_host_evidence()


@pytest.mark.parametrize("pytest_addopts", ["", "-q", "-qq", "-v"])
def test_ordinary_collection_omits_live_gate_but_explicit_runner_collects_it(pytest_addopts, tmp_path):
    probe = """
import json, sys, pytest
from pathlib import Path

class CollectionManifest:
    def pytest_collection_finish(self, session):
        Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items]))

raise SystemExit(pytest.main(
    ['tests/observability', '--collect-only', '-q'], plugins=[CollectionManifest()]))
"""
    for enabled in (False, True):
        manifest = tmp_path / f"collected-{enabled}.json"
        environment = {
            **os.environ,
            "TELEMETRY_E2E": "1" if enabled else "0",
            "PYTEST_ADDOPTS": pytest_addopts,
        }
        collected = subprocess.run(
            [sys.executable, "-c", probe, str(manifest)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert collected.returncode == 0, "observability_collection_failed"
        nodeids = json.loads(manifest.read_text())
        assert (
            "tests/observability/test_telemetry_acceptance.py::test_private_payload_is_rejected_even_in_nonempty_nested_output"
            in nodeids
        ), "ordinary_observability_tests_missing"
        assert any(nodeid.startswith("tests/observability/test_telemetry_e2e.py::") for nodeid in nodeids) is enabled


def test_structured_logging_tests_restore_handlers_before_capture_closes():
    probe = """
import io, logging, pytest
stream = io.StringIO()
handler = logging.StreamHandler(stream)
root = logging.getLogger()
root.handlers = [handler]
root.setLevel(logging.WARNING)
result = pytest.main([
    'tests/observability/test_structured_logging.py::test_uvicorn_and_celery_handlers_emit_only_stable_diagnostics',
    'tests/observability/test_structured_logging.py::test_unknown_http_exception_is_private_and_trace_correlated',
    '-q'])
assert result == 0
logging.getLogger('app.observability.operations').warning('telemetry_probe_unavailable')
assert stream.getvalue() == 'telemetry_probe_unavailable\\n', 'original_handler_not_restored'
assert root.handlers == [handler] and root.level == logging.WARNING
"""
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "writer,sample_time,record_time,value,want",
    [
        ("replacement", 102, 103, "0", True),
        ("old", 102, 103, "0", False),
        ("replacement", 99, 103, "0", False),
        ("replacement", 102, 99, "0", False),
        ("replacement", 102, 103, "1", False),
    ],
)
def test_gauge_recovery_requires_current_writer_and_fresh_recording(
    monkeypatch, writer, sample_time, record_time, value, want
):
    from tests.observability import gauge_probe

    def backend(expression):
        if expression.startswith("topk"):
            return [{"metric": {"instance": writer}, "value": [103, str(sample_time)]}]
        if expression.startswith("timestamp"):
            return [{"metric": {"deployment_environment_name": "staging"}, "value": [103, str(record_time)]}]
        # Instant-query pairs use query evaluation time, not stored sample time.
        return [{"metric": {"deployment_environment_name": "staging"}, "value": [103, value]}]

    monkeypatch.setattr(gauge_probe, "query", backend)
    assert gauge_probe.snapshot_visible("replacement", 0, 100) is want


def test_gauge_observation_timer_includes_poll_export_and_backend_delay(monkeypatch):
    from types import SimpleNamespace
    from tests.observability import gauge_probe

    clock = [10.0]
    monkeypatch.setattr(gauge_probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gauge_probe.time, "time", lambda: clock[0] + 1000)

    def poll():
        clock[0] += 2

    def flush():
        clock[0] += 3

    def visible(writer, expected, observed_after):
        assert (writer, expected, observed_after) == ("replacement", 0, 1012)
        clock[0] += 4
        return True

    monkeypatch.setattr(gauge_probe, "snapshot_visible", visible, raising=False)
    started = gauge_probe.observe_snapshot(
        SimpleNamespace(force_flush=flush), SimpleNamespace(poll=poll), "replacement", 0
    )
    assert started == 10.0 and clock[0] == 19.0


@pytest.mark.parametrize("missing", ["raw", "record", "both"])
def test_gauge_recovery_never_accepts_missing_data(monkeypatch, missing):
    from tests.observability import gauge_probe

    def backend(expression):
        raw = expression.startswith("topk")
        if missing == "both" or (raw and missing == "raw") or (not raw and missing == "record"):
            return []
        if raw:
            return [{"metric": {"instance": "replacement"}, "value": [103, "102"]}]
        if expression.startswith("timestamp"):
            return [{"metric": {"deployment_environment_name": "staging"}, "value": [103, "103"]}]
        return [{"metric": {"deployment_environment_name": "staging"}, "value": [103, "0"]}]

    monkeypatch.setattr(gauge_probe, "query", backend)
    assert not gauge_probe.snapshot_visible("replacement", 0, 100)
