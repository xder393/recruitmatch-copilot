"""Generate promtool cases from the provisioned dashboard expression itself."""

import json
import sys
from pathlib import Path

import yaml


def http_ratio_cases():
    dashboard = json.loads(Path("ops/grafana/dashboards/system-overview.json").read_text())
    panel = next(panel for panel in dashboard["panels"] if panel["title"] == "HTTP 5xx ratio")
    expression = panel["targets"][0]["expr"].replace("$__rate_interval", "1m")
    series = []
    for environment, status, values in (
        ("healthy", "200", "0+10x12"),
        ("errors", "200", "0+8x12"),
        ("errors", "500", "0+2x12"),
        ("idle", "200", "10+0x12"),
        ("idle-errors", "500", "10+0x12"),
    ):
        series.append(
            {
                "series": 'http_server_request_duration_seconds_count{deployment_environment_name="'
                + environment
                + '",http_response_status_code="'
                + status
                + '"}',
                "values": values,
            }
        )
    return {
        "evaluation_interval": "5s",
        "tests": [
            {
                "interval": "5s",
                "input_series": series,
                "promql_expr_test": [
                    {
                        "expr": expression,
                        "eval_time": "1m",
                        "exp_samples": [
                            {"labels": '{deployment_environment_name="healthy"}', "value": 0},
                            {"labels": '{deployment_environment_name="errors"}', "value": 0.2},
                        ],
                    }
                ],
            },
            {
                "interval": "5s",
                "input_series": [],
                "promql_expr_test": [{"expr": expression, "eval_time": "1m", "exp_samples": []}],
            },
        ],
    }


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(yaml.safe_dump(http_ratio_cases()))
