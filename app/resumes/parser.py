"""Deterministic, evidence-preserving resume profile baseline."""
from __future__ import annotations

import re
from typing import Dict

from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence

_SKILLS: Dict[str, str] = {
    "python": "Python",
    "fastapi": "FastAPI",
    "rag": "RAG",
    "langchain": "LangChain",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "react": "React",
    "vue": "Vue",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "redis": "Redis",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "pytorch": "PyTorch",
    "transformer": "Transformer",
    "spark": "Spark",
    "flink": "Flink",
    "sql": "SQL",
}

_EDUCATION_LEVELS = ("博士", "硕士", "本科", "大专")


class HeuristicResumeParser:
    """Fallback parser that never invents facts absent from source text."""

    def parse(self, text: str) -> ResumeProfile:
        found = []
        for token, canonical in _SKILLS.items():
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", re.IGNORECASE)
            match = pattern.search(text)
            if match:
                found.append(
                    (
                        match.start(),
                        SkillEvidence(
                            name=canonical,
                            evidence=Evidence(start=match.start(), end=match.end(), text=match.group(0)),
                        ),
                    )
                )
        found.sort(key=lambda item: item[0])

        year_matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*年", text))
        year_match = max(year_matches, key=lambda item: float(item.group(1))) if year_matches else None
        education = next((level for level in _EDUCATION_LEVELS if level in text), None)
        education_start = text.find(education) if education else -1
        return ResumeProfile(
            skills=[item[1] for item in found],
            experience_years=float(year_match.group(1)) if year_match else None,
            experience_evidence=(
                Evidence(
                    start=year_match.start(),
                    end=year_match.end(),
                    text=year_match.group(0),
                )
                if year_match
                else None
            ),
            education_level=education,
            education_evidence=(
                Evidence(start=education_start, end=education_start + len(education), text=education)
                if education
                else None
            ),
        )
