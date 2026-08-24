"""Seed 30 idempotent technical job templates from ten job families."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import create_engine_and_session
from app.models import JobTemplate

_SOURCE = Path(__file__).resolve().parents[1] / "app" / "seeds" / "job_templates.json"
_LEVELS = {
    "junior": ("初级", 0),
    "mid": ("中级", 3),
    "senior": ("高级", 5),
}


def seed_templates(session: Session, source_path: Optional[Path] = None) -> int:
    source = source_path or _SOURCE
    families = json.loads(source.read_text(encoding="utf-8"))
    existing = set(session.scalars(select(JobTemplate.slug)))
    created = 0
    for family in families:
        for level, (level_name, min_years) in _LEVELS.items():
            slug = f"{family['slug']}-{level}"
            if slug in existing:
                continue
            profile = {
                "job_family": family["job_family"],
                "level": level,
                "required_skills": family["required_skills"],
                "preferred_skills": family["preferred_skills"],
                "min_experience_years": min_years,
                "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
            }
            session.add(
                JobTemplate(
                    slug=slug,
                    title=f"{level_name}{family['title']}",
                    jd_text=(
                        f"岗位职责：{family['responsibilities']}\n"
                        f"必备技能：{'、'.join(family['required_skills'])}\n"
                        f"加分技能：{'、'.join(family['preferred_skills'])}\n"
                        f"经验要求：{min_years} 年以上"
                    ),
                    profile=profile,
                )
            )
            existing.add(slug)
            created += 1
    session.commit()
    return created


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/recruitmatch.db")
    _, session_factory = create_engine_and_session(database_url)
    with session_factory() as session:
        created = seed_templates(session)
    print(f"seeded_job_templates={created}")


if __name__ == "__main__":
    main()
