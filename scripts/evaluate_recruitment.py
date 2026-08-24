"""Run rules-v1 against the committed synthetic benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.evaluation.matching import evaluate_predictions  # noqa: E402
from app.matching.engine import MatchingEngine  # noqa: E402
from app.matching.schemas import CandidateJob  # noqa: E402
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence  # noqa: E402


def _jobs():
    families = json.loads((_ROOT / "app" / "seeds" / "job_templates.json").read_text(encoding="utf-8"))
    return [
        CandidateJob(
            job_id=family["job_family"],
            job_version_id=f"{family['job_family']}-mid-v1",
            title=family["title"],
            jd_text=family["responsibilities"],
            profile={
                "required_skills": family["required_skills"],
                "preferred_skills": family["preferred_skills"],
                "min_experience_years": 3,
                "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
            },
        )
        for family in families
    ]


def _profile(case):
    text = case["resume_text"]
    skills = []
    cursor = 0
    for name in case["skills"]:
        start = text.find(name, cursor)
        skills.append(SkillEvidence(name=name, evidence=Evidence(start=start, end=start + len(name), text=name)))
        cursor = start + len(name)
    return ResumeProfile(skills=skills, experience_years=case["experience_years"])


def evaluate(dataset):
    engine = MatchingEngine()
    jobs = _jobs()
    evaluated = []
    for case in dataset:
        predictions = engine.rank(_profile(case), jobs, top_k=3)
        evaluated.append(
            {
                **case,
                "predictions": [
                    {
                        "job_family": item.job_id,
                        "score": item.total_score,
                        "matched_items": item.matched_items,
                        "evidence": [evidence.model_dump(mode="json") for evidence in item.evidence],
                    }
                    for item in predictions
                ],
            }
        )
    return evaluate_predictions(evaluated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/recruitmatch-v1.json")
    parser.add_argument("--output", default="evaluation/recruitmatch-v1-results.json")
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    result = {
        "dataset": "recruitmatch-v1",
        "algorithm_version": "rules-v1",
        "label_source": "synthetic_heuristic",
        "metrics": evaluate(dataset),
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
