from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_ai_dataset_has_150_labeled_synthetic_cases():
    cases = json.loads(Path("evaluation/recruitmatch-ai-v1.json").read_text(encoding="utf-8"))
    assert len(cases) >= 150
    assert all(case["label_source"] == "synthetic_ai" for case in cases)
    assert all(case["evidence_spans"] and case["policy_citations"] for case in cases)


def test_fake_model_evaluation_is_deterministic(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    command = [
        sys.executable,
        "scripts/evaluate_ai_pipeline.py",
        "--dataset",
        "evaluation/recruitmatch-ai-v1.json",
        "--mode",
        "hybrid-v1",
        "--fake-model",
    ]
    subprocess.run([*command, "--output", str(first)], check=True)
    subprocess.run([*command, "--output", str(second)], check=True)
    assert first.read_bytes() == second.read_bytes()
    result = json.loads(first.read_text(encoding="utf-8"))
    assert result["algorithm_version"] == "hybrid-v1"
    assert result["label_source"] == "synthetic_ai"
    assert result["pipeline_calls"]["semantic_project_match"] > 0
    assert result["pipeline_calls"]["match_explanation"] > 0
    assert result["pipeline_outcomes"]["parser"]["rules_fallback"] > 0
    assert result["pipeline_outcomes"]["grounding"]["rejected_unsupported_claims"] > 0
    assert result["pipeline_outcomes"]["semantic_fallback_results"] > 0
    assert result["pipeline_outcomes"]["semantic_discriminated_cases"] > 0
    assert result["pipeline_outcomes"]["semantic_ranking_changed_cases"] > 0
    assert len(result["cases"]) == 150
    assert "resume_text" not in json.dumps(result["cases"], ensure_ascii=False)


def test_modes_execute_different_real_pipeline_components():
    from scripts.evaluate_ai_pipeline import evaluate

    cases = json.loads(Path("evaluation/recruitmatch-ai-v1.json").read_text(encoding="utf-8"))
    rules = evaluate(cases, "rules-v1")
    llm_rules = evaluate(cases, "llm-rules-v1")
    hybrid = evaluate(cases, "hybrid-v1")

    assert rules["pipeline_calls"] == {}
    assert llm_rules["pipeline_calls"]["resume_extract"] > 0
    assert "semantic_project_match" not in llm_rules["pipeline_calls"]
    assert hybrid["pipeline_calls"]["semantic_project_match"] > 0
    assert rules["metrics"]["extraction_recall"] < llm_rules["metrics"]["extraction_recall"]
