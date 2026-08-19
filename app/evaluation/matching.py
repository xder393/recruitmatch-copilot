"""Recommendation metric calculations over labeled cases."""
from __future__ import annotations

from typing import Any, Dict, List


def evaluate_predictions(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(cases)
    top1_hits = 0
    top3_hits = 0
    evidence_items = 0
    matched_items = 0
    for case in cases:
        expected = set(case["expected_job_families"])
        predictions = case.get("predictions", [])
        families = [item["job_family"] for item in predictions]
        if families and families[0] in expected:
            top1_hits += 1
        if any(family in expected for family in families[:3]):
            top3_hits += 1
        for prediction in predictions[:3]:
            matched_items += len(prediction.get("matched_items", []))
            evidence_items += min(
                len(prediction.get("evidence", [])),
                len(prediction.get("matched_items", [])),
            )
    return {
        "cases": count,
        "top1_accuracy": round(top1_hits / count, 4) if count else 0.0,
        "top3_recall": round(top3_hits / count, 4) if count else 0.0,
        "evidence_coverage": round(evidence_items / matched_items, 4) if matched_items else 0.0,
    }
