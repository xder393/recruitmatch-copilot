"""Generate the versioned RecruitMatch v1 synthetic benchmark."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_FAMILIES = _ROOT / "app" / "seeds" / "job_templates.json"


def generate_cases(seed: int = 20260819) -> List[Dict[str, Any]]:
    randomizer = random.Random(seed)
    families = json.loads(_FAMILIES.read_text(encoding="utf-8"))
    cases = []
    for family in families:
        required = list(family["required_skills"])
        preferred = list(family["preferred_skills"])
        for variant in range(15):
            if variant < 6:
                skills = required + preferred
                experience = [1, 3, 5][variant % 3]
            elif variant < 10:
                skills = required
                experience = [2, 4, 6, 8][variant - 6]
            elif variant < 13:
                skills = required[:2] + preferred[:1]
                experience = [1, 3, 5][variant - 10]
            elif variant == 13:
                skills = required[:1] + preferred[:1]
                experience = 3
            else:
                skills = required + preferred
                experience = None
            shuffled = list(dict.fromkeys(skills))
            randomizer.shuffle(shuffled)
            cases.append(
                {
                    "id": f"{family['job_family']}-{variant + 1:02d}",
                    "dataset_version": "recruitmatch-v1",
                    "label_source": "synthetic_heuristic",
                    "resume_text": "候选人技能：" + "、".join(shuffled),
                    "skills": shuffled,
                    "experience_years": experience,
                    "expected_job_families": [family["job_family"]],
                }
            )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evaluation/recruitmatch-v1.json")
    parser.add_argument("--seed", type=int, default=20260819)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(generate_cases(args.seed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated_cases=150 output={output}")


if __name__ == "__main__":
    main()
