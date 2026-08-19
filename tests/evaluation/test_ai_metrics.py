from __future__ import annotations

from app.evaluation.ai_metrics import evaluate_ai_cases, evaluate_grounding


def test_grounding_metrics_are_exact():
    cases = [
        {"claims": [{"citation_ids": ["c1"]}], "authorized_ids": ["c1"]},
        {"claims": [{"citation_ids": ["bad"]}], "authorized_ids": ["c2"]},
    ]
    result = evaluate_grounding(cases)
    assert result.citation_coverage == 1.0
    assert result.citation_validity == 0.5
    assert result.unsupported_claim_rate == 0.5


def test_ai_metrics_include_extraction_ranking_latency_and_cost():
    metrics = evaluate_ai_cases(
        [
            {
                "expected_facts": ["Python", "RAG"],
                "extracted_facts": ["Python", "FastAPI"],
                "expected_job_families": ["ai_application"],
                "predictions": ["ai_application", "backend"],
                "claims": [{"citation_ids": ["r1", "j1"]}],
                "authorized_ids": ["r1", "j1"],
                "latency_ms": 20,
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost": 0.001,
            }
        ]
    )
    assert metrics.extraction_precision == 0.5
    assert metrics.extraction_recall == 0.5
    assert metrics.top1_accuracy == 1
    assert metrics.top3_recall == 1
    assert metrics.p50_latency_ms == 20
    assert metrics.total_tokens == 120
    assert metrics.estimated_cost == 0.001
