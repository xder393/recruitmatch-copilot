"""Real exported writer and W3C carrier contracts."""

from uuid import UUID

from app.config import Settings
from app.observability.events import DomainEvent
from app.observability.otel import Observability
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import baggage, trace
from celery import Celery, signals
from app.tasks.dispatcher import CeleryTaskDispatcher


def test_resources_distinguish_writers_and_reject_ambient_identity(monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.instance.id=private-host,tenant_id=secret")
    identities = []
    for _ in range(2):
        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
        try:
            for _ in range(2):
                runtime.recorder.record(DomainEvent("model.request", {"service.instance.id": "fake"}))
            runtime.force_flush()
            spans = exporter.get_finished_spans()
            identity = spans[0].resource.attributes.get("service.instance.id")
            assert identity is not None, "each actual SDK writer needs a telemetry-only identity"
            assert str(UUID(identity)) == identity
            assert spans[1].resource.attributes["service.instance.id"] == identity
            assert all("service.instance.id" not in span.attributes for span in spans)
            assert reader.get_metrics_data().resource_metrics[0].resource.attributes["service.instance.id"] == identity
            identities.append(identity)
        finally:
            runtime.shutdown()
    assert identities[0] != identities[1]


def test_real_dispatcher_injects_only_w3c_context():
    exporter = InMemorySpanExporter()
    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=InMemoryMetricReader()
    )
    app = Celery("synthetic", broker="memory://")
    captured = []

    @app.task(name="recruitmatch.process_resume")
    def task(tenant, source):
        pass

    def published(headers, **kwargs):
        captured.append(dict(headers))

    signals.before_task_publish.connect(published, weak=False)
    try:
        with runtime.recorder.operation("resume.upload"):
            parent = trace.get_current_span().get_span_context()
            from opentelemetry.context import attach, detach

            token = attach(baggage.set_baggage("tenant_id", "PRIVATE-SENTINEL"))
            try:
                CeleryTaskDispatcher._publish(task, ("synthetic-tenant", "synthetic-resume"))
            finally:
                detach(token)
        assert captured[0].get("traceparent", "").split("-")[1:3] == [
            f"{parent.trace_id:032x}",
            f"{parent.span_id:016x}",
        ]
        assert "baggage" not in captured[0]
        assert "PRIVATE-SENTINEL" not in str(captured[0])
    finally:
        signals.before_task_publish.disconnect(published)
        app.close()
        runtime.shutdown()


def test_actual_http_and_sql_follow_each_app_lifespan_owner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from tests.support.application import create_sqlite_test_app
    import app.main as main

    runtimes = []

    def configured(settings, **kwargs):
        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        runtime = Observability(
            settings,
            span_exporter=exporter,
            metric_reader=reader,
            route_templates=frozenset({"/private/{source}", "/api/v1/health/live"}),
        )
        runtimes.append((runtime, exporter, reader))
        return runtime

    monkeypatch.setattr(main, "configure_observability", configured)
    app = create_sqlite_test_app(Settings(telemetry_enabled=True, database_url=f"sqlite:///{tmp_path}/owner.db"))

    @app.get("/private/{source}")
    def query(source: str):
        with app.state.database_engine.connect() as connection:
            assert connection.scalar(text("SELECT :secret"), {"secret": source}) == source
        return {"ok": True}

    try:
        for _ in range(2):
            with TestClient(app) as client:
                assert client.get("/private/PRIVATE-SENTINEL?token=PRIVATE-SENTINEL").status_code == 200
                runtime, exporter, reader = runtimes[-1]
                runtime.force_flush()
                spans = exporter.get_finished_spans()
                assert any(span.kind.name == "SERVER" for span in spans), "FastAPI requests need a real server span"
                assert any(span.kind.name == "CLIENT" for span in spans), "SQL execution needs a client span"
                assert "PRIVATE-SENTINEL" not in " ".join(span.to_json() for span in spans)
                data = reader.get_metrics_data()
                metrics = [
                    metric
                    for resource in data.resource_metrics
                    for scope in resource.scope_metrics
                    for metric in scope.metrics
                ]
                http = next(metric for metric in metrics if metric.name == "http.server.request.duration")
                assert any(0 < value < 1 for value in http.data.data_points[0].explicit_bounds)
            runtime.shutdown()
        assert len(runtimes[0][1].get_finished_spans()) == len(runtimes[1][1].get_finished_spans())
    finally:
        for runtime, _, _ in runtimes:
            runtime.shutdown()


def test_real_celery_consumer_continues_validated_publisher_trace():
    from app.observability.instrumentation import activate, instrument_frameworks
    from app.observability.context import trace_headers
    from app.observability.events import operation

    exporter = InMemorySpanExporter()
    runtime = Observability(
        Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=InMemoryMetricReader()
    )
    app = Celery("synthetic", broker="memory://")
    instrument_frameworks()

    @app.task(name="recruitmatch.process_knowledge")
    def consumer():
        assert baggage.get_all() == {}
        with operation("knowledge.process", {"task.type": "knowledge_document"}):
            return 7

    try:
        with activate(runtime):
            with operation("resume.upload"):
                parent = trace.get_current_span().get_span_context()
                headers = {**trace_headers(), "baggage": "tenant_id=PRIVATE"}
            assert consumer.apply(headers=headers, throw=True).get() == 7
        runtime.force_flush()
        spans = exporter.get_finished_spans()
        child = next(span for span in spans if span.kind.name == "CONSUMER")
        assert child.context.trace_id == parent.trace_id
        assert child.parent.span_id == parent.span_id
        domain = next(span for span in spans if span.name == "knowledge.process")
        assert domain.parent.span_id == child.context.span_id
    finally:
        app.close()
        runtime.shutdown()


def test_enabled_worker_init_after_fork_rebinds_exported_writer(monkeypatch):
    import os
    import json
    from app.tasks import celery_app as worker
    from app.observability.instrumentation import activate
    from app.observability.events import record

    sinks = []

    def configured(*args, **kwargs):
        exporter = InMemorySpanExporter()
        runtime = Observability(
            Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=InMemoryMetricReader()
        )
        sinks.append((runtime, exporter))
        return runtime

    monkeypatch.setattr(worker, "configure_observability", configured, raising=False)
    parent = configured()
    read_fd, write_fd = os.pipe()
    try:
        with activate(parent):
            record("task.started", {"task.type": "resume"})
            parent.force_flush()
            parent_id = sinks[0][1].get_finished_spans()[0].resource.attributes["service.instance.id"]
            import warnings

            # This specifically verifies the application's documented prefork contract.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="This process.*multi-threaded", category=DeprecationWarning)
                child = os.fork()
            if child == 0:
                os.close(read_fd)
                signals.worker_process_init.send(sender=None)
                record("task.started", {"task.type": "resume"})
                runtime, exporter = sinks[-1]
                runtime.force_flush()
                spans = exporter.get_finished_spans()
                result = {
                    "new_runtime": runtime is not parent,
                    "identity": spans[-1].resource.attributes["service.instance.id"],
                }
                os.write(write_fd, json.dumps(result).encode())
                os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            result = json.loads(os.read(read_fd, 1024))
            _, status = os.waitpid(child, 0)
            assert status == 0
            assert result["new_runtime"]
            assert result["identity"] != parent_id
    finally:
        os.close(read_fd)
        parent.shutdown()


def test_invalid_w3c_context_and_retry_headers_never_carry_baggage():
    from app.observability.context import W3COnlyPropagator, validated_headers

    parent = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    assert validated_headers({"traceparent": "00-" + "0" * 32 + "-" + "2" * 16 + "-01"}) == {}
    assert validated_headers({"traceparent": [parent], "baggage": "PRIVATE"}) == {}
    assert validated_headers({"traceparent": parent, "tracestate": "key=ok", "baggage": "PRIVATE"}) == {
        "traceparent": parent,
        "tracestate": "key=ok",
    }
    carrier = {"baggage": "tenant_id=PRIVATE"}
    W3COnlyPropagator().inject(carrier)
    assert "baggage" not in carrier


def test_invalid_tracestate_never_reaches_parser_diagnostics(caplog):
    from app.observability.context import validated_headers

    parent = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    for state in ("PRIVATE-STATE-SENTINEL", "key=ok,key=PRIVATE-STATE-SENTINEL"):
        assert validated_headers({"traceparent": parent, "tracestate": state}) == {"traceparent": parent}
    assert "PRIVATE-STATE-SENTINEL" not in caplog.text


def test_real_httpx_transport_uses_private_client_span_and_standard_histogram():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import httpx
    from app.observability.instrumentation import activate, instrument_frameworks
    from tests.observability.test_otel_adapter import metrics

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    runtime = Observability(Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=reader)
    try:
        instrument_frameworks()
        with activate(runtime), httpx.Client(trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{server.server_port}/PRIVATE?secret=PRIVATE").status_code == 204
        runtime.force_flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1 and spans[0].kind.name == "CLIENT"
        assert "PRIVATE" not in spans[0].to_json()
        data = metrics(reader)
        assert data["http.client.request.duration"].data.data_points[0].count == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        runtime.shutdown()


def test_concurrent_apps_never_route_to_last_configured_owner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from tests.support.application import create_sqlite_test_app
    import app.main as main

    runtimes = []

    def configured(settings, **kwargs):
        exporter = InMemorySpanExporter()
        runtime = Observability(
            settings,
            span_exporter=exporter,
            metric_reader=InMemoryMetricReader(),
            route_templates=kwargs["route_templates"],
        )
        runtimes.append((runtime, exporter))
        return runtime

    monkeypatch.setattr(main, "configure_observability", configured)
    apps = [
        create_sqlite_test_app(Settings(telemetry_enabled=True, database_url=f"sqlite:///{tmp_path}/{i}.db"))
        for i in range(2)
    ]
    try:
        with TestClient(apps[0]) as first, TestClient(apps[1]) as second, ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(client.get, "/api/v1/health/live") for client in (first, second)]
            assert all(future.result().status_code == 200 for future in futures)
            for runtime, exporter in runtimes:
                runtime.force_flush()
                servers = [span for span in exporter.get_finished_spans() if span.kind.name == "SERVER"]
                assert len(servers) == 1
                assert servers[0].name == "GET /api/v1/health/live"
        assert runtimes[0][1].get_finished_spans()[0].resource != runtimes[1][1].get_finished_spans()[0].resource
    finally:
        for runtime, _ in runtimes:
            runtime.shutdown()
