"""Bounded internal-network telemetry acceptance; never prints recruiting payloads."""

import argparse
import base64
import json
import hashlib
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path

import httpx

from tests.support.telemetry_runtime import KEY_SENTINEL, MODEL_SENTINEL

API = "http://api:8000"
PROMETHEUS = "http://prometheus:9090"
TEMPO = "http://tempo:3200"


def source_digest():
    """Bind host evidence to the actual test-image application/config/helper bytes."""
    root = Path(__file__).resolve().parents[1]
    paths = {root / "docker-compose.yml", root / "Dockerfile"}
    for directory in ("app", "scripts", "tests/support", "tests/observability", "ops"):
        paths.update(
            path
            for path in (root / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def eventually(check, *, seconds=75, interval=1, label="telemetry_condition"):
    deadline = time.monotonic() + seconds
    while True:
        try:
            value = check()
            if value:
                return value
        except (AssertionError, httpx.HTTPError, KeyError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError(f"{label}: bounded deadline exceeded") from None
        time.sleep(interval)


def query(expression, base=PROMETHEUS):
    response = httpx.get(base + "/api/v1/query", params={"query": expression}, timeout=5)
    assert response.status_code == 200, "prometheus_query_failed"
    body = response.json()
    assert body["status"] == "success", "prometheus_query_rejected"
    return body["data"]["result"]


def trace(trace_id):
    response = httpx.get(TEMPO + f"/api/traces/{trace_id}", headers={"Accept": "application/json"}, timeout=5)
    assert response.status_code == 200, "tempo_trace_not_ready"
    return response.json()


def spans(body):
    result = []
    for batch in body.get("batches", body.get("resourceSpans", [])):
        resource = {item["key"]: item["value"].get("stringValue") for item in batch["resource"]["attributes"]}
        for scope in batch["scopeSpans"]:
            for span in scope["spans"]:
                result.append({**span, "resource": resource})
    return result


def hex_id(value):
    return base64.b64decode(value).hex()


def verify_parentage(body, trace_id, parent_id):
    output = spans(body)
    expected = {
        "resume.upload",
        "artifact.put",
        "resume.process",
        "artifact.get",
        "resume.parse",
        "model.generate",
        "embedding.generate",
        "vector.index",
        "lease.claim",
        "lease.finalize",
    }
    assert expected <= {s["name"] for s in output}, "missing_business_spans"
    assert all(hex_id(s["traceId"]) == trace_id for s in output), "trace_identity_mismatch"
    by_id = {s["spanId"]: s for s in output}

    def ancestors(span):
        result, seen = [], set()
        while span.get("parentSpanId") in by_id:
            span = by_id[span["parentSpanId"]]
            assert span["spanId"] not in seen, "cyclic_trace"
            seen.add(span["spanId"])
            result.append(span)
        return result

    server = next(
        (
            s
            for s in output
            if s.get("kind") == "SPAN_KIND_SERVER" and s["resource"]["service.name"] == "recruitmatch-api"
        ),
        None,
    )
    assert server is not None, "missing_http_server_span"
    assert hex_id(server["parentSpanId"]) == parent_id, "incoming_w3c_parent_lost"
    process = next(s for s in output if s["name"] == "resume.process")
    chain = ancestors(process)
    assert server in chain, "worker_not_descended_from_http_server"
    assert any(s.get("kind") == "SPAN_KIND_PRODUCER" for s in chain), "missing_broker_producer"
    assert any(s.get("kind") == "SPAN_KIND_CONSUMER" for s in chain), "missing_prefork_consumer"
    worker = process["resource"]["service.instance.id"]
    assert worker != server["resource"]["service.instance.id"], "writer_collision"
    assert any(s["name"] == "model.generate" and process in ancestors(s) for s in output)
    return worker, server["resource"]["service.instance.id"]


def assert_private(payload, forbidden):
    serialized = json.dumps(payload, ensure_ascii=False)
    assert serialized and all(item not in serialized for item in forbidden if item), "synthetic_privacy_leak"


class BusinessFlow:
    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.sentinel = "CP5_PRIVATE_BODY_" + self.run_id
        self.email = self.run_id + "@cp5.example.invalid"
        self.password = "CP5_PRIVATE_PASSWORD_" + self.run_id
        self.client = httpx.Client(base_url=API, timeout=15)
        self.forbidden = [self.sentinel, self.email, self.password, KEY_SENTINEL, MODEL_SENTINEL]
        self.resumes = []

    def request(self, method, path, expected, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise AssertionError("business_transport_unavailable") from None
        assert response.status_code in expected, f"business_http_status_{response.status_code}"
        return response.json()

    def setup(self):
        self.ready = self.request("GET", "/api/v1/health/ready", {200})
        assert self.ready["status"] == "ready"
        created = self.request(
            "POST",
            "/api/v1/auth/bootstrap",
            {201},
            json={
                "tenant_name": self.sentinel,
                "email": self.email,
                "password": self.password,
            },
        )
        self.forbidden.extend(value for key, value in created.items() if key.endswith("id") and isinstance(value, str))
        login = self.request("POST", "/api/v1/auth/login", {200}, json={"email": self.email, "password": self.password})
        self.forbidden.append(login["access_token"])
        self.client.headers["Authorization"] = "Bearer " + login["access_token"]
        job = self.request(
            "POST",
            "/api/v1/jobs",
            {201},
            json={
                "title": self.sentinel,
                "jd_text": "Python " + self.sentinel,
                "profile": {
                    "job_family": "ai_application",
                    "level": "mid",
                    "required_skills": ["Python"],
                    "preferred_skills": [],
                    "weights": {"skills": 1},
                },
            },
        )
        self.job_id = job["id"]
        self.forbidden.append(self.job_id)
        self.request("POST", f"/api/v1/jobs/{self.job_id}/activate", {200})

    def upload(self, index):
        trace_id, parent_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
        filename = f"CP5_PRIVATE_FILENAME_{self.run_id}_{index}.txt"
        self.forbidden.append(filename)
        content = f"Python {self.sentinel} CandidateSynthetic {self.email} +86-19900009999 {index}"
        self.forbidden.append(hashlib.sha256(content.encode()).hexdigest())
        data = self.request(
            "POST",
            "/api/v1/resumes",
            {202},
            files={
                "file": (
                    filename,
                    content,
                    "text/plain",
                ),
            },
            headers={"traceparent": f"00-{trace_id}-{parent_id}-01", "baggage": "tenant_id=" + self.sentinel},
        )
        self.forbidden.append(data["id"])
        self.resumes.append(data["id"])
        return data["id"], trace_id, parent_id

    def completed(self, resume_id):
        data = self.request("GET", f"/api/v1/resumes/{resume_id}", {200})
        assert data["status"] == "succeeded" and data["search_index_status"] == "ready", "source_not_published"
        assert data["profile"]["skills"], "missing_real_profile"
        return True

    def available(self):
        assert self.request("GET", "/api/v1/health/ready", {200}) == self.ready, "readiness_changed"
        jobs = self.request("GET", "/api/v1/jobs", {200})
        assert self.job_id in json.dumps(jobs), "authorized_job_missing"
        matched = self.request("POST", f"/api/v1/resumes/{self.resumes[0]}/matches?mode=rules-v1", {201})
        assert matched["algorithm_version"] == "rules-v1" and matched["results"], "empty_rule_match"
        assert matched["results"][0]["total_score"] > 0, "nonmeaningful_rule_match"
        return matched

    def live_processes(self):
        system = self.request("GET", "/api/v1/health/system", {200})
        assert system["worker"] == "fresh" and system["beat"] == "fresh", "runtime_heartbeat_stale"
        assert system["beat_heartbeat_age_seconds"] <= 10, "beat_not_ticking"
        assert system["worker_oldest_heartbeat_age_seconds"] <= 10, "worker_not_pulsing"
        return True


def verify_business():
    assert os.environ.get("TELEMETRY_E2E") == "1", "use_explicit_test_telemetry_runner"
    for url in ("http://otel-collector:13133/", PROMETHEUS + "/-/ready", TEMPO + "/ready"):
        eventually(lambda url=url: httpx.get(url, timeout=5).status_code == 200, label="backend_prerequisite")
    flow = BusinessFlow()
    try:
        names = [
            "recruitmatch_task_started_total",
            "recruitmatch_task_completed_total",
            "recruitmatch_task_duration_seconds_count",
            "recruitmatch_model_request_total",
            "recruitmatch_model_tokens_total",
            "recruitmatch_vector_indexed_chunks_total",
        ]
        match_name = "recruitmatch_match_completed_total"
        # Snapshot all existing writers before this flow submits any work. New
        # series start at zero; stable writers cannot reuse earlier contributions.
        baselines = {}
        for name in [*names, match_name]:
            service = "api" if name == match_name else "worker"
            totals = Counter()
            for row in query(f'{name}{{job="recruitmatch-{service}"}}'):
                totals[row["metric"]["instance"]] += float(row["value"][1])
            baselines[name] = totals
        flow.setup()
        with ThreadPoolExecutor(max_workers=4) as executor:
            uploads = list(executor.map(flow.upload, range(8)))
        worker_writers, api_writers = set(), set()
        deliveries = Counter()
        for resume_id, trace_id, parent_id in uploads:
            eventually(lambda resume_id=resume_id: flow.completed(resume_id), label="persisted_processing")

            def chain(trace_id=trace_id, parent_id=parent_id):
                body = trace(trace_id)
                writers = verify_parentage(body, trace_id, parent_id)
                assert_private(body, flow.forbidden + ["CandidateSynthetic", "+86-19900009999"])
                return writers

            worker, api = eventually(chain, label="real_broker_parentage")
            worker_writers.add(worker)
            deliveries[worker] += 1
            api_writers.add(api)
        assert len(worker_writers) >= 2, "prefork_children_not_exercised"
        matched = flow.available()
        assert matched["results"]
        metric_evidence = {}

        def contribution(name, writer, minimum, label):
            baseline = baselines[name][writer]

            def increased():
                rows = query(f'{name}{{instance="{writer}"}}')
                current = sum(float(row["value"][1]) for row in rows)
                delta = current - baseline
                assert rows and delta >= minimum, "current_flow_contribution_missing"
                return {"baseline": baseline, "current": current, "delta": delta, "minimum": minimum}

            metric_evidence[writer + "/" + name] = eventually(increased, label=label)

        for writer in sorted(worker_writers):
            for name in names:
                minimum = deliveries[writer] * (18 if name == "recruitmatch_model_tokens_total" else 1)
                contribution(name, writer, minimum, "actual_worker_metric_" + name)
            assert_private(query(f'{{instance="{writer}"}}'), flow.forbidden)
        for writer in api_writers:
            contribution(match_name, writer, 1, "actual_api_match_metric")
            assert_private(query(f'{{instance="{writer}"}}'), flow.forbidden)
        dashboard = json.loads(Path("ops/grafana/dashboards/system-overview.json").read_text())
        panel = next(panel for panel in dashboard["panels"] if panel["title"] == "HTTP 5xx ratio")

        def http_ratio():
            rows = query(panel["targets"][0]["expr"].replace("$__rate_interval", "1m"))
            ratios = {row["metric"]["deployment_environment_name"]: float(row["value"][1]) for row in rows}
            assert ratios.get("development") == 0, "successful_flow_http_ratio_not_zero"
            return ratios

        ratios = eventually(http_ratio, label="actual_dashboard_http_ratio")
        evidence = {
            "run_id": flow.run_id,
            "worker_writers": sorted(worker_writers),
            "api_writers": sorted(api_writers),
            "processed": len(uploads),
            "parentage_verified": True,
            "metric_deltas": metric_evidence,
            "http_5xx_ratios": ratios,
            "trace_ids": [item[1] for item in uploads],
            "timestamp": time.time(),
        }
        print(json.dumps(evidence, sort_keys=True))
        return evidence
    finally:
        flow.client.close()


def verify_host_evidence():
    path = Path(os.environ.get("TELEMETRY_HOST_EVIDENCE", "/evidence/host.json"))
    assert path.is_file(), "missing_host_evidence_run_scripts/verify_telemetry_host.sh"
    evidence = json.loads(path.read_text())
    assert evidence.get("source_digest") == source_digest(), "host_evidence_source_identity_mismatch"
    assert 0 <= time.time() - evidence["timestamp"] < 3600, "stale_host_evidence"
    for field in ("collector_stopped", "collector_restored", "business_available", "logs_private", "restart_verified"):
        assert evidence.get(field) is True, "incomplete_host_evidence_" + field


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["business", "outage-prepare", "outage-check", "logs", "finalize"])
    parser.add_argument("--directory", default="/evidence")
    parser.add_argument("--output", choices=["before", "after"])
    args = parser.parse_args()
    directory = Path(args.directory)
    if args.phase == "business":
        evidence = verify_business()
        if args.output:
            (directory / (args.output + ".json")).write_text(json.dumps(evidence))
    elif args.phase == "outage-prepare":
        flow = BusinessFlow()
        flow.setup()
        resume_id, _, _ = flow.upload("before")
        eventually(lambda: flow.completed(resume_id), label="outage_setup")
        flow.available()
        eventually(flow.live_processes, label="outage_prerequisite_heartbeats")
        state = {
            "run_id": flow.run_id,
            "sentinel": flow.sentinel,
            "email": flow.email,
            "password": flow.password,
            "forbidden": flow.forbidden,
            "resumes": flow.resumes,
            "ready": flow.ready,
            "job_id": flow.job_id,
            "authorization": flow.client.headers["Authorization"],
        }
        (directory / "state.json").write_text(json.dumps(state))
    elif args.phase == "outage-check":
        state = json.loads((directory / "state.json").read_text())
        flow = BusinessFlow()
        for name, value in state.items():
            if name != "authorization":
                setattr(flow, name, value)
        flow.client.headers["Authorization"] = state["authorization"]
        flow.available()
        # Wait past a full 10s heartbeat interval while Collector remains stopped.
        time.sleep(12)
        eventually(flow.live_processes, seconds=35, label="collector_down_beat_and_worker_continue")
        resume_id, _, _ = flow.upload("during")
        eventually(lambda: flow.completed(resume_id), label="collector_down_persisted_processing")
        flow.available()
        (directory / "outage.json").write_text(
            json.dumps({"business_available": True, "processed_during_outage": True, "beat_and_worker_continued": True})
        )
        state["forbidden"] = flow.forbidden
        (directory / "state.json").write_text(json.dumps(state))
    elif args.phase == "logs":
        state = json.loads((directory / "state.json").read_text())
        for service in ("api", "worker", "beat"):
            lines = (directory / (service + ".log")).read_text().splitlines()
            assert lines, "empty_runtime_logs_" + service
            assert_private(lines, state["forbidden"] + ["CandidateSynthetic", "+86-19900009999"])
            records = [json.loads(line) for line in lines]
            assert all({"event", "trace_id", "span_id", "request_id"} <= row.keys() for row in records)
            if service != "beat":
                assert any(row["trace_id"] for row in records), "no_correlated_runtime_logs_" + service
        print("runtime_logs_nonempty_private_and_correlated")
        (directory / "logs.json").write_text(json.dumps({"logs_private": True}))
    else:
        before = json.loads((directory / "before.json").read_text())
        after = json.loads((directory / "after.json").read_text())
        assert set(before["worker_writers"]).isdisjoint(after["worker_writers"]), "restart_writer_reused"
        assert before["api_writers"] == after["api_writers"], "api_writer_not_stable"
        writers = before["worker_writers"] + after["worker_writers"]
        restart_metrics = {}
        for metric in ("recruitmatch_task_completed_total", "recruitmatch_task_duration_seconds_count"):
            rows = query(metric + '{instance=~"' + "|".join(writers) + '"}')
            assert {row["metric"]["instance"] for row in rows} == set(writers), "restart_series_collided"
            assert all(float(row["value"][1]) > 0 for row in rows), "restart_contribution_missing"
            restart_metrics[metric] = sum(float(row["value"][1]) for row in rows)
        evidence = {
            **json.loads((directory / "outage.json").read_text()),
            **json.loads((directory / "logs.json").read_text()),
            "timestamp": time.time(),
            "source_digest": source_digest(),
            "collector_stopped": True,
            "collector_restored": True,
            "restart_verified": True,
            "before": before,
            "after": after,
            "restart_metric_totals": restart_metrics,
        }
        (directory / "host.json").write_text(json.dumps(evidence))
        print("host_outage_restoration_and_restart_verified")


if __name__ == "__main__":
    main()
