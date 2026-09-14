"""Explicit live acceptance: pytest this file only with the internal stack up.

Uses synthetic OTLP only. Missing/unhealthy prerequisites fail, never skip.
"""

import json
import os
import time
import uuid
from copy import deepcopy

import httpx


def eventually(check, seconds=40):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return check()
        except (AssertionError, httpx.HTTPError, KeyError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def attrs(**values):
    return [{"key": key, "value": {"stringValue": value}} for key, value in values.items()]


def query(expression):
    response = httpx.get("http://prometheus:9090/api/v1/query", params={"query": expression}, timeout=5)
    response.raise_for_status()
    body = response.json()
    assert body["status"] == "success", body
    return body["data"]["result"]


def test_running_backends_and_authenticated_grafana_provisioning():
    for url in ("http://otel-collector:13133/", "http://prometheus:9090/-/ready", "http://tempo:3200/ready"):
        response = httpx.get(url, timeout=5)
        assert response.status_code == 200, (url, response.text)
    auth = (os.environ["GRAFANA_ADMIN_USER"], os.environ["GRAFANA_ADMIN_PASSWORD"])
    with httpx.Client(base_url="http://grafana:3000", auth=auth, timeout=5) as client:
        sources = client.get("/api/datasources")
        assert sources.status_code == 200
        assert {(d["uid"], d["type"], d["url"]) for d in sources.json()} == {
            ("prometheus", "prometheus", "http://prometheus:9090"),
            ("tempo", "tempo", "http://tempo:3200"),
        }
        dashboards = client.get("/api/search", params={"type": "dash-db"}).json()
        assert {d["title"] for d in dashboards} == {
            "System Overview",
            "Async Reliability",
            "AI & RAG Pipeline",
            "Recruiting Business",
        }
        for dashboard in dashboards:
            provisioned = client.get(f"/api/dashboards/uid/{dashboard['uid']}").json()
            assert provisioned["meta"]["provisioned"] is True
            for panel in provisioned["dashboard"]["panels"]:
                for target in panel.get("targets", []):
                    if target.get("expr"):
                        query(target["expr"].replace("$__rate_interval", "5m"))
    for path in ("/api/datasources", "/api/search", "/api/dashboards/uid/system-overview"):
        assert httpx.get("http://grafana:3000" + path, timeout=5).status_code == 401
    assert query('up{job="otel-collector"}')[0]["value"][1] == "1"


def test_collector_preserves_positive_metric_and_trace_but_removes_optional_payloads():
    writer = str(uuid.uuid4())
    trace_id, span_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    secret = "synthetic-private-payload@example.invalid"
    now = time.time_ns()
    resource = {
        "attributes": attrs(
            **{
                "service.name": "recruitmatch-api",
                "service.instance.id": writer,
                "deployment.environment.name": "test",
                "tenant_id": secret,
            }
        )
    }
    scope = {"name": secret, "version": secret, "attributes": attrs(secret=secret)}
    labels = attrs(**{"outcome": "success", "source.type": "resume", "model.name": secret, "tenant_id": secret})
    metric = {
        "name": "recruitmatch.task.completed",
        "unit": secret,
        "description": secret,
        "sum": {
            "aggregationTemporality": 2,
            "isMonotonic": True,
            "dataPoints": [
                {
                    "startTimeUnixNano": str(now - 1_000_000_000),
                    "timeUnixNano": str(now),
                    "asInt": "7",
                    "attributes": labels,
                    "exemplars": [{"timeUnixNano": str(now), "asInt": "7", "filteredAttributes": attrs(secret=secret)}],
                }
            ],
        },
    }
    unknown = {**metric, "name": secret}
    response = httpx.post(
        "http://otel-collector:4318/v1/metrics",
        json={
            "resourceMetrics": [
                {
                    "resource": resource,
                    "schemaUrl": secret,
                    "scopeMetrics": [{"scope": scope, "schemaUrl": secret, "metrics": [metric, unknown]}],
                }
            ]
        },
        timeout=5,
    )
    response.raise_for_status()

    def check_metric():
        result = query(f'recruitmatch_task_completed_total{{instance="{writer}"}}')
        assert len(result) == 1, result
        assert result[0]["value"][1] == "7"
        assert result[0]["metric"]["outcome"] == "success"
        assert result[0]["metric"]["source_type"] == "resume"
        assert "model_name" not in result[0]["metric"]
        assert secret not in json.dumps(result)
        all_series = query(f'{{instance="{writer}"}}')
        assert {row["metric"]["__name__"] for row in all_series} == {"recruitmatch_task_completed_total", "target_info"}
        assert secret not in json.dumps(all_series)

    eventually(check_metric)
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": "0123456789abcdef",
        "name": "resume.process",
        "kind": 1,
        "startTimeUnixNano": str(now),
        "endTimeUnixNano": str(now + 100_000),
        "attributes": labels,
        "status": {"code": 2, "message": secret},
        "traceState": "secret=" + secret,
        "events": [{"name": secret, "timeUnixNano": str(now), "attributes": labels}],
        "links": [{"traceId": uuid.uuid4().hex, "spanId": span_id, "traceState": secret, "attributes": labels}],
    }
    unsafe_span = {
        **span,
        "spanId": uuid.uuid4().hex[:16],
        "name": secret,
        "attributes": attrs(**{"outcome": secret, "model.name": "deepseek-chat"}),
    }
    response = httpx.post(
        "http://otel-collector:4318/v1/traces",
        json={
            "resourceSpans": [
                {
                    "resource": resource,
                    "schemaUrl": secret,
                    "scopeSpans": [{"scope": scope, "schemaUrl": secret, "spans": [span, unsafe_span]}],
                }
            ]
        },
        timeout=5,
    )
    response.raise_for_status()

    def check_trace():
        response = httpx.get(
            f"http://tempo:3200/api/traces/{trace_id}", headers={"Accept": "application/json"}, timeout=5
        )
        response.raise_for_status()
        body = response.json()
        assert secret not in json.dumps(body)
        batches = body.get("batches", body.get("resourceSpans", []))
        output = [s for b in batches for scope in b["scopeSpans"] for s in scope["spans"]]
        assert len(output) == 2, body
        retained = next(s for s in output if s["name"] == "resume.process")
        sanitized = next(s for s in output if s["name"] == "internal.operation")
        assert retained["parentSpanId"] == "ASNFZ4mrze8="
        assert retained["status"]["code"] == "STATUS_CODE_ERROR"
        assert all(not s.get("events") and not s.get("links") for s in output)
        assert any(a == {"key": "outcome", "value": {"stringValue": "success"}} for a in retained["attributes"])
        assert sanitized["attributes"] == [{"key": "model.name", "value": {"stringValue": "deepseek-chat"}}]

    eventually(check_trace)


REGISTRY = [
    ("recruitmatch.task.started", "counter", "{task}"),
    ("recruitmatch.task.completed", "counter", "{task}"),
    ("recruitmatch.task.failed", "counter", "{task}"),
    ("recruitmatch.task.retry", "counter", "{task}"),
    ("recruitmatch.task.duration", "histogram", "s"),
    ("recruitmatch.queue.depth", "gauge", "{source}"),
    ("recruitmatch.queue.oldest_age", "gauge", "s"),
    ("recruitmatch.lease.takeover", "counter", "{takeover}"),
    ("recruitmatch.lease.renew_failure", "counter", "{failure}"),
    ("recruitmatch.worker.live", "gauge", "{worker}"),
    ("recruitmatch.worker.oldest_heartbeat_age", "gauge", "s"),
    ("recruitmatch.beat.tick_age", "gauge", "s"),
    ("recruitmatch.artifact.operation", "counter", "{operation}"),
    ("recruitmatch.artifact.operation.duration", "histogram", "s"),
    ("recruitmatch.artifact.cleanup_pending", "gauge", "{artifact}"),
    ("recruitmatch.vector.search.duration", "histogram", "s"),
    ("recruitmatch.vector.search.results", "histogram", "{result}"),
    ("recruitmatch.vector.indexed_chunks", "counter", "{chunk}"),
    ("recruitmatch.model.request", "counter", "{request}"),
    ("recruitmatch.model.duration", "histogram", "s"),
    ("recruitmatch.model.tokens", "counter", "{token}"),
    ("recruitmatch.model.fallback", "counter", "{fallback}"),
    ("recruitmatch.model.schema_failure", "counter", "{failure}"),
    ("recruitmatch.citation.rejection", "counter", "{rejection}"),
    ("recruitmatch.match.completed", "counter", "{match}"),
    ("recruitmatch.match.duration", "histogram", "s"),
    ("recruitmatch.match.score", "histogram", "1"),
    ("http.server.request.duration", "histogram", "s"),
    ("http.client.request.duration", "histogram", "s"),
    ("http.server.active_requests", "up_down_counter", "{request}"),
]


def test_all_approved_metric_names_reach_prometheus_and_writers_remain_distinct():
    now = time.time_ns()
    writers = [str(uuid.uuid4()), str(uuid.uuid4())]
    for writer in writers:
        metrics = []
        for name, kind, unit in REGISTRY:
            point = {
                "startTimeUnixNano": str(now - 20_000_000_000),
                "timeUnixNano": str(now),
                "attributes": attrs(**{"source.type": "resume", "recovery.reason": "retry_due"}),
            }
            if kind == "histogram":
                point.update({"count": "2", "sum": 1.0, "bucketCounts": ["1", "1", "0"], "explicitBounds": [0.5, 1.0]})
                data = {"histogram": {"aggregationTemporality": 2, "dataPoints": [point]}}
            else:
                point["asInt"] = "3"
                if kind == "gauge":
                    data = {"gauge": {"dataPoints": [point]}}
                else:
                    data = {
                        "sum": {"aggregationTemporality": 2, "isMonotonic": kind == "counter", "dataPoints": [point]}
                    }
            if kind != "gauge":
                previous = deepcopy(point)
                previous["timeUnixNano"] = str(now - 10_000_000_000)
                if kind == "histogram":
                    previous.update({"count": "1", "sum": 0.25, "bucketCounts": ["1", "0", "0"]})
                else:
                    previous["asInt"] = "1"
                next(iter(data.values()))["dataPoints"].insert(0, previous)
            metrics.append({"name": name, "unit": unit, **data})
        response = httpx.post(
            "http://otel-collector:4318/v1/metrics",
            json={
                "resourceMetrics": [
                    {
                        "resource": {
                            "attributes": attrs(
                                **{
                                    "service.name": "recruitmatch-beat",
                                    "service.instance.id": writer,
                                    "deployment.environment.name": "test",
                                }
                            )
                        },
                        "scopeMetrics": [{"scope": {"name": "recruitmatch"}, "metrics": metrics}],
                    }
                ]
            },
            timeout=5,
        )
        response.raise_for_status()

    def check():
        rows = query('recruitmatch_task_started_total{deployment_environment_name="test"}')
        assert {r["metric"]["instance"] for r in rows} >= set(writers)
        assert all(r["metric"]["recovery_reason"] == "retry_due" for r in rows if r["metric"]["instance"] in writers)
        series = query(f'{{instance="{writers[0]}"}}')
        names = {r["metric"]["__name__"] for r in series}
        assert "recruitmatch_match_score_count" in names, names
        assert "http_server_request_duration_seconds_count" in names, names
        for name, kind, unit in REGISTRY:
            translated = name.replace(".", "_")
            if unit == "s":
                translated += "_seconds"
            if kind == "counter":
                translated += "_total"
            elif kind == "histogram":
                translated += "_count"
            assert translated in names, (translated, names)
        latest = query('recruitmatch:queue_depth:latest{deployment_environment_name="test"}')
        assert latest and latest[0]["value"][1] == "3"
        writer_filter = f'instance=~"{writers[0]}|{writers[1]}"'
        rates = query(f"rate(recruitmatch_task_started_total{{{writer_filter}}}[1m])")
        assert len(rates) == 2 and all(float(r["value"][1]) > 0 for r in rates)
        p95 = query(
            "histogram_quantile(0.95, sum by(le) "
            f"(rate(http_server_request_duration_seconds_bucket{{{writer_filter}}}[1m])))"
        )
        assert p95 and 0.5 < float(p95[0]["value"][1]) <= 1.0

    eventually(check)
