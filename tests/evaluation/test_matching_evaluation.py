from __future__ import annotations


def test_metrics_use_literal_expected_rank_and_evidence_counts():
    """Catches inflated metrics from computing expectations with prediction code."""
    from app.evaluation.matching import evaluate_predictions

    metrics = evaluate_predictions(
        [
            {
                "expected_job_families": ["ai_application"],
                "predictions": [
                    {"job_family": "ai_application", "matched_items": ["Python", "RAG"], "evidence": [{}, {}]}
                ],
            },
            {
                "expected_job_families": ["backend"],
                "predictions": [
                    {"job_family": "data_engineering", "matched_items": ["SQL"], "evidence": [{}]},
                    {"job_family": "backend", "matched_items": ["Python"], "evidence": [{}]},
                ],
            },
        ]
    )

    assert metrics == {
        "cases": 2,
        "top1_accuracy": 0.5,
        "top3_recall": 1.0,
        "evidence_coverage": 1.0,
    }


def test_generator_creates_150_versioned_synthetic_cases():
    """Catches accidental shrinkage or nondeterminism in the portfolio benchmark."""
    from scripts.generate_recruitment_eval import generate_cases

    first = generate_cases(seed=20260819)
    second = generate_cases(seed=20260819)

    assert first == second
    assert len(first) == 150
    assert len({case["id"] for case in first}) == 150
    assert {case["label_source"] for case in first} == {"synthetic_heuristic"}
