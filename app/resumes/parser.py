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

        years = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*年", text)]
        education = next((level for level in _EDUCATION_LEVELS if level in text), None)
        return ResumeProfile(
            skills=[item[1] for item in found],
            experience_years=max(years) if years else None,
            education_level=education,
        )
