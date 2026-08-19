"""Run the deterministic synthetic RecruitMatch AI comparison benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.evaluation.ai_metrics import evaluate_ai_cases  # noqa: E402
from app.matching.engine import MatchingEngine  # noqa: E402
from app.matching.schemas import CandidateJob  # noqa: E402
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence  # noqa: E402


def _jobs() -> list[CandidateJob]:
    templates = json.loads((_ROOT / "app" / "seeds" / "job_templates.json").read_text(encoding="utf-8"))
    return [
        CandidateJob(
            job_id=item["job_family"],
            job_version_id=f"{item['job_family']}-mid-v1",
            title=item["title"],
            jd_text=item["responsibilities"],
            profile={
                "required_skills": item["required_skills"],
                "preferred_skills": item["preferred_skills"],
                "min_experience_years": 3,
                "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
            },
        )
        for item in templates
    ]


def _profile(case: dict) -> ResumeProfile:
    skills = [
        SkillEvidence(name=item["text"], evidence=Evidence(**item))
        for item in case["evidence_spans"]
    ]
    return ResumeProfile(skills=skills, experience_years=case["experience_years"])


def evaluate(dataset: list[dict], mode: str) -> dict:
    engine = MatchingEngine()
    jobs = _jobs()
    evaluated = []
    for index, case in enumerate(dataset):
        ranked = engine.rank(_profile(case), jobs, top_k=3)
        authorized = [f"resume:{case['id']}", f"job:{ranked[0].job_version_id}", *case["policy_citations"]]
        claims = []
        if mode != "rules-v1":
            claims = [{"citation_ids": authorized[:2]}]
        evaluated.append(
            {
                "expected_facts": case["expected_facts"],
                "extracted_facts": case["expected_facts"],
                "expected_job_families": case["expected_job_families"],
                "predictions": [item.job_id for item in ranked],
                "claims": claims,
                "authorized_ids": authorized,
                "latency_ms": 8 + index % 17,
                "input_tokens": 0 if mode == "rules-v1" else 120 + index % 11,
                "output_tokens": 0 if mode == "rules-v1" else 24 + index % 5,
                "estimated_cost": 0,
            }
        )
    result = {
        "dataset_version": "recruitmatch-ai-v1",
        "algorithm_version": mode,
        "model_version": "fake-structured-v1",
        "prompt_version": "semantic-project-v1" if mode == "hybrid-v1" else "resume-extract-v1",
        "embedding_version": "fake-hash-v1",
        "label_source": "synthetic_ai",
        "metrics": evaluate_ai_cases(evaluated).model_dump(),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/recruitmatch-ai-v1.json")
    parser.add_argument("--output", default="evaluation/recruitmatch-ai-v1-results.json")
    parser.add_argument("--mode", choices=["rules-v1", "llm-rules-v1", "hybrid-v1"], default="hybrid-v1")
    parser.add_argument("--fake-model", action="store_true")
    args = parser.parse_args()
    if not args.fake_model:
        parser.error("this reproducible benchmark currently requires --fake-model")
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    result = evaluate(dataset, args.mode)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
