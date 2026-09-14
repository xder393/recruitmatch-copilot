"""Deployment boundaries; live probes are requested separately from unit tests."""

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text())


def test_only_api_and_authenticated_grafana_publish_loopback_ports():
    services = read_yaml("docker-compose.yml")["services"]
    assert {name: service["ports"] for name, service in services.items() if service.get("ports")} == {
        "api": ["127.0.0.1:8000:8000"],
        "grafana": ["127.0.0.1:3000:3000"],
    }
    for name in ("api", "worker", "beat"):
        assert not {"otel-collector", "prometheus", "tempo", "grafana"} & services[name]["depends_on"].keys()
        assert services[name]["environment"]["OTEL_SERVICE_NAME"] == f"recruitmatch-{name}"
    for name in ("test-unit", "test-integration"):
        assert services[name]["environment"]["TELEMETRY_ENABLED"] == "false"
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert "docker.sock" not in str(services)


def test_collector_routes_bounded_nonblocking_exports_through_privacy_processors():
    config = read_yaml("ops/otel/collector.yaml")
    pipelines = config["service"]["pipelines"]
    assert set(pipelines) == {"traces", "metrics"}
    for signal, exporter in (("traces", "otlp_grpc/tempo"), ("metrics", "otlp_http/prometheus")):
        assert pipelines[signal]["receivers"] == ["otlp"]
        assert pipelines[signal]["processors"] == ["memory_limiter", "filter/redaction", "transform/privacy", "batch"]
        assert pipelines[signal]["exporters"] == [exporter]
        queue = config["exporters"][exporter]["sending_queue"]
        assert 0 < queue["queue_size"] <= 1024
        assert queue["block_on_overflow"] is False
        assert queue["wait_for_result"] is False
        assert config["exporters"][exporter]["retry_on_failure"]["max_elapsed_time"] == "30s"
    assert config["exporters"]["otlp_http/prometheus"]["metrics_endpoint"] == (
        "http://prometheus:9090/api/v1/otlp/v1/metrics"
    )
    assert config["processors"]["filter/redaction"]["error_mode"] == "propagate"
    assert config["processors"]["transform/privacy"]["error_mode"] == "propagate"
    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"


def test_prometheus_has_only_collector_scrape_and_no_extra_telemetry_paths():
    config = read_yaml("ops/prometheus/prometheus.yml")
    assert config["scrape_configs"] == [
        {"job_name": "otel-collector", "static_configs": [{"targets": ["otel-collector:8888"]}]}
    ]
    assert "alerting" not in config
    assert "remote_write" not in config
    tempo = read_yaml("ops/tempo/tempo.yaml")
    assert tempo["target"] == "all"
    assert "metrics_generator" not in tempo
    assert tempo["usage_report"]["reporting_enabled"] is False


def test_provisioning_resolves_four_dashboards_to_only_metrics_and_traces():
    datasources = read_yaml("ops/grafana/provisioning/datasources/datasources.yaml")["datasources"]
    assert {(item["uid"], item["type"], item["url"]) for item in datasources} == {
        ("prometheus", "prometheus", "http://prometheus:9090"),
        ("tempo", "tempo", "http://tempo:3200"),
    }
    dashboards = [json.loads(path.read_text()) for path in (ROOT / "ops/grafana/dashboards").glob("*.json")]
    assert {dashboard["title"] for dashboard in dashboards} == {
        "System Overview",
        "Async Reliability",
        "AI & RAG Pipeline",
        "Recruiting Business",
    }
    for dashboard in dashboards:
        assert dashboard["panels"]
        for panel in dashboard["panels"]:
            if "datasource" in panel:
                assert panel["datasource"]["uid"] in {"prometheus", "tempo"}
            if panel["type"] == "timeseries":
                assert panel["fieldConfig"]["defaults"]["noValue"] == "Unknown / no data"


def test_dashboard_legends_identify_retained_query_dimensions():
    raw_dimensions = {
        "recruitmatch:queue_depth:latest": {"source_type", "deployment_environment_name"},
        "recruitmatch:queue_oldest_age_seconds:latest": {"source_type", "deployment_environment_name"},
        'up{job="otel-collector"}': {"job", "instance"},
        "otelcol_exporter_queue_size / otelcol_exporter_queue_capacity": {"exporter", "job", "instance"},
        'ALERTS{alertstate="firing"}': {
            "alertname",
            "deployment_environment_name",
            "source_type",
            "operation",
            "exporter",
            "job",
            "instance",
        },
    }
    for path in (ROOT / "ops/grafana/dashboards").glob("*.json"):
        for panel in json.loads(path.read_text())["panels"]:
            for target in panel.get("targets", []):
                if "expr" not in target:
                    continue
                expr = target["expr"]
                groups = re.findall(r"sum by\s*\(([^)]+)\)", expr)
                if groups:
                    dimensions = set(groups[0].replace(" ", "").split(",")) - {"le"}
                elif expr in raw_dimensions:
                    dimensions = raw_dimensions[expr]
                elif expr.startswith("recruitmatch:"):
                    dimensions = {"source_type", "deployment_environment_name"}
                else:
                    dimensions = set()
                legend = target["legendFormat"]
                assert set(re.findall(r"{{\s*(\w+)\s*}}", legend)) == dimensions, panel["title"]
                if not dimensions:
                    assert legend == "Mean retrieval results (all environments)"
