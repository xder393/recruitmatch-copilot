"""Explicit live gate; ordinary unit collection excludes this file."""


def test_real_http_broker_prefork_and_backend_privacy():
    from scripts.verify_telemetry import verify_business

    evidence = verify_business()
    assert len(evidence["worker_writers"]) >= 2
    assert evidence["processed"] >= 4
    assert evidence["parentage_verified"] is True


def test_collector_outage_and_runtime_logs_have_host_evidence():
    from scripts.verify_telemetry import verify_host_evidence

    verify_host_evidence()


def test_operational_gauge_replacement_and_failed_dependency_are_unknown():
    from tests.observability.gauge_probe import verify_gauges

    verify_gauges()


def test_all_alerts_reached_firing_with_isolated_synthetic_stimulus():
    import json
    import os
    import time
    from pathlib import Path

    path = Path(os.environ.get("TELEMETRY_ALERT_EVIDENCE", "/evidence/alerts.json"))
    assert path.is_file(), "missing_alert_evidence_run_scripts/verify_telemetry_alerts.sh"
    body = json.loads(path.read_text())
    from scripts.verify_telemetry import source_digest

    assert body.get("source_digest") == source_digest(), "alert_evidence_source_identity_mismatch"
    assert 0 <= time.time() - body["timestamp"] < 3600, "stale_alert_evidence"
    assert body["initial_nonfiring"] is True
    assert set(body["firing"]) == {
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
