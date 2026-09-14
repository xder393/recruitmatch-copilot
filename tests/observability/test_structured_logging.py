"""Actual server/worker logger paths must emit useful private JSON."""

import json
import logging
import pytest

from app.core.logging import setup_logging


@pytest.fixture(autouse=True)
def restore_process_logging():
    """These tests configure process logging; never leak capsys streams to peers."""
    names = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "celery", "celery.task", "celery.redirected")
    saved = [
        (logger, list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        for logger in (logging.getLogger(name) for name in names)
    ]
    try:
        yield
    finally:
        for logger, handlers, level, propagate, disabled in saved:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


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


def test_real_prefork_sigterm_finishes_inflight_task_and_keeps_stdout_json(tmp_path):
    import os
    import subprocess
    import sys
    import time

    started, finished = tmp_path / "started", tmp_path / "finished"
    probe = """
import logging, sys, time
from pathlib import Path
from celery import signals
from app.tasks.celery_app import celery_app
from app.operations.heartbeats import OperationsHeartbeats
OperationsHeartbeats.pulse_worker = lambda *args: None
OperationsHeartbeats.remove_worker = lambda *args: None
@celery_app.task(name='synthetic.inflight', acks_late=True)
def inflight():
    Path(sys.argv[1]).write_text('started')
    time.sleep(2)
    Path(sys.argv[2]).write_text('finished')
def ready(**kwargs):
    logging.getLogger('startup').warning('heartbeat_unavailable')
    inflight.delay()
signals.worker_ready.connect(ready, weak=False)
celery_app.start(['--quiet', 'worker', '--pool=prefork', '--concurrency=2', '--loglevel=INFO'])
"""
    process = subprocess.Popen(
        [sys.executable, "-c", probe, str(started), str(finished)],
        env={
            **os.environ,
            "CELERY_BROKER_URL": "memory://",
            "TELEMETRY_ENABLED": "false",
            "AI_ENABLED": "false",
            "OPENAI_API_KEY": "",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not started.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists() and not finished.exists(), "no_inflight_prefork_task"
        process.terminate()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0 and finished.read_text() == "finished", "sigterm_lost_inflight_task"
        assert stderr == ""
        records = [json.loads(line) for line in stdout.splitlines()]
        assert any(record["event"] == "heartbeat_unavailable" for record in records)
        assert all({"request_id", "trace_id", "span_id"} <= record.keys() for record in records)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_control_output_binding_is_worker_only_idempotent_and_descriptor_preserving():
    import os
    import subprocess
    import sys

    probe = """
import json, os
from celery.apps import worker
original = worker.safe_say
import app.main
import app.tasks.celery_app
assert worker.safe_say is original, 'non_worker_import_changed_binding'
from app.core.celery_logging import install_worker_control_logging
install_worker_control_logging()
installed = worker.safe_say
install_worker_control_logging()
assert worker.safe_say is installed, 'non_idempotent_binding'
reader, writer = os.pipe()
with os.fdopen(writer, 'w') as stream:
    worker.safe_say('PRIVATE-CONTROL-SENTINEL', stream)
payload = os.read(reader, 1024)
os.close(reader)
assert payload.count(b'\\n') == 1 and not payload.startswith(b'\\n')
assert b'PRIVATE' not in payload
record = json.loads(payload)
assert record == {'level':'INFO', 'event':'application_diagnostic', 'request_id':None, 'trace_id':None, 'span_id':None}
# A closed output stream cannot disrupt a shutdown callback.
worker.safe_say('PRIVATE-CONTROL-SENTINEL', stream)
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "CELERY_BROKER_URL": "memory://", "TELEMETRY_ENABLED": "false"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-CONTROL-SENTINEL" not in result.stdout + result.stderr
