"""Actual server/worker logger paths must emit useful private JSON."""

import json
import logging

from app.core.logging import setup_logging


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
