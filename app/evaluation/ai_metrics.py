"""Deterministic extraction, ranking, grounding, latency, and cost metrics."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel


class GroundingMetrics(BaseModel):
    citation_coverage: float
    citation_validity: float
    unsupported_claim_rate: float


class AIMetrics(GroundingMetrics):
    cases: int
    extraction_precision: float
    extraction_recall: float
    top1_accuracy: float
    top3_recall: float
    p50_latency_ms: float
    p95_latency_ms: float
    total_tokens: int
    estimated_cost: float


def _rounded(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: List[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 4)


def evaluate_grounding(cases: List[Dict[str, Any]]) -> GroundingMetrics:
    claims = [claim for case in cases for claim in case.get("claims", [])]
    covered = valid_ids = citation_count = unsupported = 0
    for case in cases:
        authorized = set(case.get("authorized_ids", []))
        for claim in case.get("claims", []):
            ids = claim.get("citation_ids", [])
            if ids:
                covered += 1
            citation_count += len(ids)
            valid_ids += sum(1 for item in ids if item in authorized)
            if not ids or any(item not in authorized for item in ids):
                unsupported += 1
    return GroundingMetrics(
        citation_coverage=_rounded(covered, len(claims)),
        citation_validity=_rounded(valid_ids, citation_count),
        unsupported_claim_rate=_rounded(unsupported, len(claims)),
    )


def evaluate_ai_cases(cases: List[Dict[str, Any]]) -> AIMetrics:
    true_positive = predicted_count = expected_count = top1 = top3 = 0
    for case in cases:
        expected_facts = set(case.get("expected_facts", []))
        extracted = set(case.get("extracted_facts", []))
        true_positive += len(expected_facts & extracted)
        predicted_count += len(extracted)
        expected_count += len(expected_facts)
        expected_families = set(case.get("expected_job_families", []))
        predictions = case.get("predictions", [])
        top1 += int(bool(predictions) and predictions[0] in expected_families)
        top3 += int(any(item in expected_families for item in predictions[:3]))
    grounding = evaluate_grounding(cases)
    latencies = [float(case.get("latency_ms", 0)) for case in cases]
    return AIMetrics(
        cases=len(cases),
        extraction_precision=_rounded(true_positive, predicted_count),
        extraction_recall=_rounded(true_positive, expected_count),
        top1_accuracy=_rounded(top1, len(cases)),
        top3_recall=_rounded(top3, len(cases)),
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_tokens=sum(int(case.get("input_tokens", 0)) + int(case.get("output_tokens", 0)) for case in cases),
        estimated_cost=round(sum(float(case.get("estimated_cost", 0)) for case in cases), 6),
        **grounding.model_dump(),
    )
