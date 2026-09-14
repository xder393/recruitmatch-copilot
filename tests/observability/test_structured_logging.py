"""Actual server/worker logger paths must emit useful private JSON."""

import json
import logging
import pytest

from app.core.logging import setup_logging


@pytest.mark.parametrize("service", ["worker", "beat"])
def test_compose_celery_startup_has_no_raw_banner(service):
    """Removing quiet startup must leak a banner through the real CLI, not a logger."""
    import os
    from pathlib import Path
    import shlex
    import subprocess
    import sys
    import yaml

    command = shlex.split(yaml.safe_load(Path("docker-compose.yml").read_text())["services"][service]["command"])
    # Real CLI/Worker prefork startup and Beat service startup; memory broker and
    # startup signals bound the subprocess without consuming work or calling models.
    probe = """
import logging
import sys
from celery import signals
from app.tasks.celery_app import celery_app
from app.operations.heartbeats import OperationsHeartbeats
# Only unrelated Redis heartbeat I/O is substituted in this offline startup test.
OperationsHeartbeats.pulse_worker = lambda *args: None
OperationsHeartbeats.remove_worker = lambda *args: None
celery_app.conf.worker_concurrency = 1
celery_app.conf.task_default_queue = 'PRIVATE-STARTUP-SENTINEL'
celery_app.conf.beat_schedule_filename = 'PRIVATE-STARTUP-SENTINEL'
def ready(**kwargs):
    logging.getLogger('startup').warning('heartbeat_unavailable')
    raise SystemExit(0)
signals.worker_ready.connect(ready, weak=False)
signals.beat_init.connect(ready, weak=False)
celery_app.start(sys.argv[1:])
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, *command[1:]],
        env={
            **os.environ,
            "CELERY_BROKER_URL": "memory://PRIVATE-STARTUP-SENTINEL//",
            "AI_ENABLED": "false",
            "TELEMETRY_ENABLED": "false",
            "OPENAI_API_KEY": "",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-STARTUP-SENTINEL" not in result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert any(record["event"] == "heartbeat_unavailable" for record in records)
    assert result.stderr == ""


def test_uvicorn_and_celery_handlers_emit_only_stable_diagnostics(capsys):
    import uvicorn
    from celery import Celery

    uvicorn.Config("app.main:app")
    app = Celery("synthetic")
    app.log.setup_logging_subsystem()
    setup_logging()
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "PRIVATE-IP", "GET", "/PRIVATE-SENTINEL", "1.1", 200
    )
    try:
        raise ValueError("PRIVATE-SENTINEL")
    except ValueError:
        logging.getLogger("uvicorn.error").exception("Exception PRIVATE-SENTINEL")
        logging.getLogger("celery.task").exception("Task PRIVATE-SENTINEL")
    logging.getLogger("app.tasks.celery_app").warning("processing_database_unavailable")
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    records = [json.loads(line) for line in output.splitlines()]
    assert len(records) == 4
    assert records[-1]["event"] == "processing_database_unavailable"
    assert records[0]["event"] == "http_access"
    assert records[0]["http.response.status_code"] == 200
    assert records[1]["error.code"] == "internal_error"
    assert all({"request_id", "trace_id", "span_id"} <= record.keys() for record in records)
    app.close()


def test_unknown_http_exception_is_private_and_trace_correlated(tmp_path, monkeypatch, capsys):
    from app.config import Settings
    from app.observability.otel import Observability
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from fastapi.testclient import TestClient
    from tests.support.application import create_sqlite_test_app
    import app.main as main

    exporter = InMemorySpanExporter()
    runtime = Observability(
        Settings(telemetry_enabled=True),
        span_exporter=exporter,
        metric_reader=InMemoryMetricReader(),
        route_templates=frozenset({"/failure/{source}"}),
    )
    monkeypatch.setattr(main, "configure_observability", lambda *args, **kwargs: runtime)
    app = create_sqlite_test_app(Settings(database_url=f"sqlite:///{tmp_path}/failure.db"))

    @app.get("/failure/{source}")
    def failure(source: str):
        raise RuntimeError(source)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/failure/PRIVATE-SENTINEL?token=PRIVATE-SENTINEL")
            assert response.status_code == 500
        runtime.force_flush()
        output = capsys.readouterr().out
        assert "PRIVATE-SENTINEL" not in output
        records = [json.loads(line) for line in output.splitlines()]
        error = next(item for item in records if item["event"] == "internal_error")
        assert error["error.code"] == "internal_error" and error["trace_id"] is not None
        assert "PRIVATE-SENTINEL" not in " ".join(span.to_json() for span in exporter.get_finished_spans())
    finally:
        runtime.shutdown()
