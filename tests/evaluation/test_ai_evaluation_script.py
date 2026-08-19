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
