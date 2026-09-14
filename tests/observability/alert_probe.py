"""Bounded synthetic inputs to real, unchanged Prometheus alert rules (R14).

This does not measure physical Collector overload or production API failures.
"""

import json
import math
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from scripts.verify_telemetry import eventually, source_digest

ALERTS = {
    "Http5xx",
    "ApiP95",
    "QueueOldestAge",
    "WorkerZero",
    "BeatStale",
    "ArtifactCleanup",
    "AiFallback",
    "CollectorExportFailure",
    "CollectorQueuePressure",
    "LeaseTakeover",
}
started = None
writer = str(uuid.uuid4())


def metrics():
    elapsed = 0 if started is None else time.monotonic() - started
    active = started is not None and elapsed < 420
    count = math.floor(elapsed) if active else 0
    lines = []

    def sample(name, value, service="recruitmatch-beat", extra=""):
        labels = f'job="{service}",instance="{writer}",deployment_environment_name="test"'
        lines.append(f"{name}{{{labels}{extra}}} {value}")

    for name, baseline, fault in (
        ("recruitmatch_queue_depth", 0, 1),
        ("recruitmatch_queue_oldest_age_seconds", 0, 200),
        ("recruitmatch_worker_live", 1, 0),
        ("recruitmatch_worker_oldest_heartbeat_age_seconds", 0, 60),
        ("recruitmatch_beat_tick_age_seconds", 0, 60),
        ("recruitmatch_artifact_cleanup_pending", 0, 1),
    ):
        sample(name, fault if active else baseline)
    sample("recruitmatch_model_fallback_total", 10 + count, extra=',operation="match_explanation"')
    sample("recruitmatch_lease_takeover_total", 10 + count, extra=',source_type="resume"')
    sample("http_server_request_duration_seconds_count", 10, "recruitmatch-api", ',http_response_status_code="200"')
    sample("http_server_request_duration_seconds_count", count, "recruitmatch-api", ',http_response_status_code="500"')
    for boundary, value in (("0.1", 10), ("1", 10), ("5", 10 + count), ("+Inf", 10 + count)):
        sample("http_server_request_duration_seconds_bucket", value, "recruitmatch-api", f',le="{boundary}"')
    sample("http_server_request_duration_seconds_sum", 1 + count * 2, "recruitmatch-api")
    for name, value in (
        ("otelcol_exporter_send_failed_spans", count),
        ("otelcol_exporter_send_failed_metric_points", 0),
        ("otelcol_exporter_enqueue_failed_spans", 0),
        ("otelcol_exporter_enqueue_failed_metric_points", count),
        ("otelcol_exporter_queue_size", 9 if active else 0),
        ("otelcol_exporter_queue_capacity", 10),
    ):
        lines.append(f'{name}{{exporter="synthetic"}} {value}')
    lines.append(f'otelcol_exporter_enqueue_failed_spans{{exporter="single-family"}} {count}')
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(metrics().encode())

    def do_POST(self):
        global started
        if self.path != "/stimulus" or started is not None:
            self.send_error(409)
            return
        started = time.monotonic()
        self.send_response(204)
        self.end_headers()


def states():
    response = httpx.get("http://prometheus:9090/api/v1/rules", params={"type": "alert"}, timeout=5)
    assert response.status_code == 200, "alert_rules_unavailable"
    groups = response.json()["data"]["groups"]
    return {rule["name"]: rule["state"] for group in groups for rule in group["rules"]}


def verify():
    def healthy():
        current = states()
        assert set(current) == ALERTS
        assert all(state == "inactive" for state in current.values()), "initial_alerts_not_inactive"
        # A successful scrape plus real latest gauge excludes the initial empty state.
        from scripts.verify_telemetry import query

        rows = query('recruitmatch:worker_live:latest{deployment_environment_name="test"}')
        assert rows and rows[0]["value"][1] == "1"
        return True

    eventually(healthy, seconds=45, label="known_nonfiring_prerequisite")
    time.sleep(10)
    assert healthy()
    response = httpx.post("http://stimulus:8080/stimulus", timeout=5)
    assert response.status_code == 204
    activated = time.monotonic()
    observed = {}

    def all_firing():
        current = states()
        for name, state in current.items():
            if state == "firing" and name not in observed:
                observed[name] = round(time.monotonic() - activated, 1)
                print("synthetic_rule_firing " + name, flush=True)
        return set(observed) == ALERTS

    eventually(all_firing, seconds=380, interval=5, label="all_ten_actual_firing")
    # Read the actual provisioned numeric query; OR without a distinct transient
    # family label would silently undercount identical-label metric families.
    from scripts.verify_telemetry import query

    dashboard = json.loads(Path("ops/grafana/dashboards/system-overview.json").read_text())
    panel = next(p for p in dashboard["panels"] if p["title"] == "Collector failed send/enqueue per second")
    expression = panel["targets"][0]["expr"].replace("$__rate_interval", "1m")
    rates = {row["metric"]["exporter"]: float(row["value"][1]) for row in query(expression)}
    assert 1.8 <= rates["synthetic"] <= 2.2, "simultaneous_failure_families_not_summed"
    assert 0.9 <= rates["single-family"] <= 1.1, "missing_failure_families_masked_present_rate"
    result = {
        "initial_nonfiring": True,
        "firing": observed,
        "timestamp": time.time(),
        "source_digest": source_digest(),
        "source": "isolated_synthetic_rule_inputs_not_physical_collector_faults",
        "dashboard_failure_rates": rates,
    }
    Path("/evidence/alerts.json").write_text(json.dumps(result))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    else:
        verify()
