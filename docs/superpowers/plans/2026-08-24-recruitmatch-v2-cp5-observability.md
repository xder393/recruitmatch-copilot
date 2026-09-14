# RecruitMatch v2 Checkpoint 5 Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one privacy-safe OTLP metrics/traces pipeline with provisioned Prometheus, Tempo and Grafana evidence.

**Architecture:** Domain/application services emit bounded events; the Observability Adapter and auto-instrumentation send OTLP asynchronously to Collector. Collector redacts and exports traces to Tempo and metrics to Prometheus's OTLP receiver. Grafana reads only Prometheus and Tempo.

**Tech Stack:** OpenTelemetry Python, OTel Collector Contrib, Prometheus, Tempo, Grafana, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-24-recruitmatch-productionization-design.md`

## Global Constraints

- No `prometheus_client` in business code and no Grafana PostgreSQL datasource.
- W3C `traceparent`/`tracestate` propagate through Celery; business Baggage does not.
- Automatic sensitive attributes are denied by default.
- Telemetry queues are bounded and non-blocking; backend failure may drop telemetry, not business work.
- Compose trace sampling is 1.0; load-test sampling is 0.1; metrics are unsampled.
- User approved the telemetry-only `service.instance.id` Resource exception on 2026-09-14 (spec9.4). Task2 must generate isolated writer identities, including after prefork; Task3 preserves them through OTLP/Prometheus, and Task4 verifies multi-writer/restart behavior. All business-ID and Baggage restrictions remain unchanged.

---

### Task 1: Implement the OTel adapter and privacy policy

**Files:**
- Create: `app/observability/otel.py`
- Create: `app/observability/policy.py`
- Modify: `app/observability/events.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/observability/test_otel_adapter.py`
- Test: `tests/observability/test_telemetry_privacy.py`

**Interfaces:**
- Produces: `OtelDomainEventRecorder`, `configure_observability(settings)`, `shutdown_observability()`.
- Consumes: `DomainEvent` and bounded attribute allowlist.

- [ ] **Step 1: Write in-memory exporter and PII-negative tests**

```python
def test_domain_event_becomes_bounded_metric(recorder, metric_reader):
    recorder.record(DomainEvent("model.fallback", {"error.code": "model_timeout"}))
    assert metric_reader.counter_value("recruitmatch.model.fallback") == 1


@pytest.mark.parametrize("key", ["tenant_id", "url.full", "db.statement", "exception.message"])
def test_forbidden_attributes_are_dropped(recorder, span_exporter, key):
    recorder.record(DomainEvent("matching.completed", {key: "secret", "outcome": "success"}))
    assert key not in span_exporter.last_attributes()
```

- [ ] **Step 2: Run and confirm OTel adapter is missing**

Run: `docker compose run --rm test-unit pytest tests/observability/test_otel_adapter.py tests/observability/test_telemetry_privacy.py -q`

Expected: FAIL importing `OtelDomainEventRecorder`.

- [ ] **Step 3: Implement bounded async SDK configuration**

Add compatible locked runtime dependencies for `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-redis`, `opentelemetry-instrumentation-celery` and `opentelemetry-instrumentation-httpx`. Regenerate `uv.lock` with the pinned uv container from Checkpoint 1, rebuild `test-unit`, and reject a lock containing incompatible OTel API/SDK/instrumentation lines. Use `ParentBased(TraceIdRatioBased(settings.trace_sample_ratio))`, `BatchSpanProcessor`, `PeriodicExportingMetricReader`, finite queue/batch sizes and short exporter timeout. Allow only spec section 9.4 attributes. Configure `OTEL_SEMCONV_STABILITY_OPT_IN=http` and suppress query/body capture.

- [ ] **Step 4: Run adapter and privacy tests**

Run: `docker compose run --rm test-unit pytest tests/observability/test_otel_adapter.py tests/observability/test_telemetry_privacy.py -q`

Expected: bounded metrics/spans appear and forbidden attributes never do.

- [ ] **Step 5: Commit OTel adapter**

```bash
git add app/observability app/config.py app/main.py pyproject.toml uv.lock tests/observability
git commit -m "feat: emit privacy-safe OpenTelemetry signals"
```

### Task 2: Instrument HTTP, database, Celery and recruiting operations

**Files:**
- Create: `app/observability/instrumentation.py`
- Modify: `app/tasks/celery_app.py`
- Modify: `app/tasks/beat.py`
- Modify: `app/services/resume_processing.py`
- Modify: `app/services/knowledge_processing.py`
- Modify: `app/services/matching.py`
- Modify: `app/ai/gateway.py`
- Test: `tests/observability/test_trace_propagation.py`
- Test: `tests/observability/test_domain_metrics.py`

**Interfaces:**
- Produces: API→Worker trace continuity and the exact `recruitmatch.*` instruments from spec section 9.2.
- Consumes: OTel recorder and W3C propagator.

- [ ] **Step 1: Write API→Worker context and low-cardinality tests**

```python
def test_worker_span_continues_api_trace(trace_harness):
    api_span, worker_span = trace_harness.upload_and_process()
    assert worker_span.context.trace_id == api_span.context.trace_id


def test_worker_metric_has_no_worker_id(metric_reader):
    attrs = metric_reader.attributes("recruitmatch.worker.live")
    assert all("worker_id" not in item for item in attrs)
```

- [ ] **Step 2: Run and confirm context is not propagated**

Run: `docker compose run --rm test-unit pytest tests/observability/test_trace_propagation.py tests/observability/test_domain_metrics.py -q`

Expected: FAIL because Celery headers/metrics are absent.

- [ ] **Step 3: Add auto and explicit instrumentation**

Instrument FastAPI, SQLAlchemy, Redis, HTTP Client and Celery. Inject only `traceparent` and `tracestate` into task headers. Add explicit spans for upload, Artifact, processing, Embedding batch, Vector, Matching, Model, Citation, Lease and Beat. Queue depth/age query PostgreSQL; Worker metrics aggregate live count/oldest age; Beat uses bounded recovery reasons.

- [ ] **Step 4: Run instrumentation tests**

Run: `docker compose run --rm test-unit pytest tests/observability/test_trace_propagation.py tests/observability/test_domain_metrics.py -q`

Expected: trace IDs match, no Baggage/PII/high-cardinality labels exist, and instruments have correct Counter/Histogram/Gauge types.

- [ ] **Step 5: Commit instrumentation**

```bash
git add app/observability/instrumentation.py app/tasks app/services app/ai/gateway.py tests/observability
git commit -m "feat: trace recruiting workflows across Celery"
```

### Task 3: Add Collector, Prometheus, Tempo and provisioned Grafana

**Files:**
- Create: `ops/otel/collector.yaml`
- Create: `ops/prometheus/prometheus.yml`
- Create: `ops/prometheus/rules.yml`
- Create: `ops/tempo/tempo.yaml`
- Create: `ops/grafana/provisioning/datasources/datasources.yaml`
- Create: `ops/grafana/provisioning/dashboards/dashboards.yaml`
- Create: `ops/grafana/dashboards/system-overview.json`
- Create: `ops/grafana/dashboards/async-reliability.json`
- Create: `ops/grafana/dashboards/ai-rag-pipeline.json`
- Create: `ops/grafana/dashboards/recruiting-business.json`
- Modify: `docker-compose.yml`
- Test: `tests/observability/test_observability_configs.py`

**Interfaces:**
- Produces: Collector OTLP receiver, Tempo exporter, Prometheus OTLP ingestion, Collector self-scrape and four dashboards.
- Consumes: application OTLP on internal Docker network.

- [ ] **Step 1: Write config/provisioning tests**

```python
def test_grafana_has_no_postgres_datasource(compose_config):
    data = Path("ops/grafana/provisioning/datasources/datasources.yaml").read_text()
    assert "prometheus" in data and "tempo" in data
    assert "postgres" not in data.lower()


def test_only_api_and_grafana_publish_host_ports(compose_config):
    assert compose_config.host_ports() == {"127.0.0.1:8000", "127.0.0.1:3000"}
```

- [ ] **Step 2: Run and confirm configs are absent**

Run: `docker compose run --rm test-unit pytest tests/observability/test_observability_configs.py -q`

Expected: FAIL because `ops/otel`, Tempo and Grafana provisioning are absent.

- [ ] **Step 3: Create the exact Collector pipeline and dashboards**

Collector processors are `memory_limiter -> filter/redaction -> attributes/transform -> batch`; exporters use bounded queue, finite retry and non-blocking overflow. Prometheus enables OTLP receiver and scrapes Collector `:8888`. Rules cover HTTP 5xx/P95, Queue, Worker, Beat, Cleanup, AI Fallback, Collector failures and Lease takeover. Do not add Alertmanager or Docker Socket.

- [ ] **Step 4: Validate configs and service health**

Run:

```bash
docker compose config --quiet
docker compose up -d otel-collector prometheus tempo grafana
docker compose ps
docker compose run --rm test-unit pytest tests/observability/test_observability_configs.py -q
```

Expected: configs validate, services become healthy, only API/Grafana have localhost bindings.

- [ ] **Step 5: Commit observability stack**

```bash
git add ops/otel ops/prometheus ops/tempo ops/grafana docker-compose.yml tests/observability/test_observability_configs.py
git commit -m "ops: provision RecruitMatch metrics traces and dashboards"
```

### Task 4: Prove real backend queries and Collector failure isolation

**Files:**
- Create: `tests/observability/test_telemetry_e2e.py`
- Create: `scripts/verify_telemetry.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `test-telemetry` Compose runner and deterministic backend query verification.
- Consumes: full API/Worker/Collector/Prometheus/Tempo stack.

- [ ] **Step 1: Write backend E2E assertions**

```python
def test_upload_trace_and_metric_are_queryable(stack):
    trace_id = stack.upload_and_wait()
    assert stack.tempo_trace(trace_id)["traceID"] == trace_id
    assert stack.prometheus_query("recruitmatch_task_completed_total") > 0


def test_collector_down_does_not_fail_business(stack):
    stack.stop("otel-collector")
    assert stack.list_jobs().status_code == 200
    assert stack.rule_match().status_code in {200, 201}
```

- [ ] **Step 2: Run and confirm end-to-end evidence is not yet queryable**

Run: `docker compose run --rm test-telemetry pytest tests/observability/test_telemetry_e2e.py -q`

Expected: FAIL until telemetry runner and backend query helper exist.

- [ ] **Step 3: Implement bounded retry queries and outage orchestration**

Poll Prometheus/Tempo at a fixed interval with a fixed timeout. Never query recruiting PostgreSQL from Grafana. Ensure stopping Collector does not alter `/ready` or rule-path response status.

- [ ] **Step 4: Run complete telemetry E2E**

Run: `docker compose run --rm test-telemetry pytest tests/observability -q`

Expected: real metric and trace queries pass; privacy and outage tests pass.

- [ ] **Step 5: Commit E2E evidence**

```bash
git add tests/observability/test_telemetry_e2e.py scripts/verify_telemetry.py docker-compose.yml
git commit -m "test: verify telemetry backends and outage isolation"
```

## Checkpoint 5 Gate

```bash
docker compose up --build -d
docker compose run --rm test-telemetry pytest tests/observability -q
```

Expected: Prometheus and Tempo return actual data, the API→Worker trace is continuous, PII tests pass, and Collector outage does not break deterministic business paths.
