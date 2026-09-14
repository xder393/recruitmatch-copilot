"""Current public-flow acceptance must reject cumulative history without emission."""

import re
from types import SimpleNamespace

import pytest

from scripts import verify_telemetry as gate


WORKER_METRICS = (
    "recruitmatch_task_started_total",
    "recruitmatch_task_completed_total",
    "recruitmatch_task_duration_seconds_count",
    "recruitmatch_model_request_total",
    "recruitmatch_model_tokens_total",
    "recruitmatch_vector_indexed_chunks_total",
)
MATCH = "recruitmatch_match_completed_total"


@pytest.mark.parametrize("missing", [*WORKER_METRICS, MATCH])
def test_repeated_flow_rejects_any_counter_with_only_previous_contributions(monkeypatch, missing):
    install_flow(monkeypatch, missing=missing)
    with pytest.raises(AssertionError, match="actual_.*metric.*bounded deadline"):
        gate.verify_business()


@pytest.mark.parametrize("baseline", [0, 100])
def test_flow_reports_writer_deltas_from_absent_or_present_baselines(monkeypatch, baseline):
    install_flow(monkeypatch, baseline=baseline)
    result = gate.verify_business()
    for writer in ("worker-a", "worker-b"):
        for name in WORKER_METRICS:
            increment = 72 if "tokens" in name else 4
            assert result["metric_deltas"][writer + "/" + name] == {
                "baseline": baseline,
                "current": baseline + increment,
                "delta": increment,
                "minimum": increment,
            }
    assert result["metric_deltas"]["api/" + MATCH] == {
        "baseline": baseline,
        "current": baseline + 1,
        "delta": 1,
        "minimum": 1,
    }


def install_flow(monkeypatch, *, baseline=100, missing=None):
    uploaded = []
    matched = []

    class Flow:
        run_id = "synthetic-flow"
        forbidden = []
        client = SimpleNamespace(close=lambda: None)

        def setup(self):
            pass

        def upload(self, index):
            uploaded.append(index)
            return index, str(index), "parent"

        def completed(self, resume_id):
            return True

        def available(self):
            matched.append(True)
            return {"results": [1]}

    def backend(expression):
        # No new label identifies the flow. These are existing writer series.
        assert "synthetic-flow" not in expression
        if "http_server_request_duration" in expression:
            return [{"metric": {"deployment_environment_name": "development"}, "value": [0, "0"]}]
        name = expression.split("{")[0]
        if not name:  # Privacy inventory queries.
            return [{"metric": {"instance": "api"}, "value": [0, "1"]}]
        writers = ["api"] if name == MATCH else ["worker-a", "worker-b"]
        selected = re.search(r'instance="([^"]+)"', expression)
        if selected:
            writers = [selected[1]]
        active = bool(matched) if name == MATCH else bool(uploaded)
        increment = (1 if name == MATCH else 72 if "tokens" in name else 4) if active else 0
        if baseline == 0 and not active:
            return []
        return [
            {
                "metric": {"instance": writer},
                "value": [
                    0,
                    str(baseline if name == missing and writer in {"worker-a", "api"} else baseline + increment),
                ],
            }
            for writer in writers
        ]

    poll = gate.eventually
    monkeypatch.setenv("TELEMETRY_E2E", "1")
    monkeypatch.setattr(
        gate, "eventually", lambda check, **kwargs: poll(check, seconds=0, label=kwargs.get("label", ""))
    )
    monkeypatch.setattr(gate, "BusinessFlow", Flow)
    monkeypatch.setattr(gate.httpx, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(gate, "query", backend)
    monkeypatch.setattr(gate, "trace", lambda trace_id: {"spans": [trace_id]})
    monkeypatch.setattr(
        gate,
        "verify_parentage",
        lambda body, trace_id, parent: ("worker-a" if int(trace_id) % 2 else "worker-b", "api"),
    )
