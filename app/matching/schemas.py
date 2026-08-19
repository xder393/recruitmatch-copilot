"""Pure matching input and output contracts."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.resumes.schemas import Evidence


class CandidateJob(BaseModel):
    job_id: str
    job_version_id: str
    title: str
    jd_text: str
    profile: Dict[str, Any]


class DimensionScores(BaseModel):
    skills: float = Field(ge=0, le=1)
    experience: float = Field(ge=0, le=1)
    projects: float = Field(ge=0, le=1)


class MatchRecommendation(BaseModel):
    job_id: str
    job_version_id: str
    title: str
    total_score: float = Field(ge=0, le=1)
    dimension_scores: DimensionScores
    matched_items: List[str]
    missing_items: List[str]
    uncertain_items: List[str]
    evidence: List[Evidence]
    risk_flags: List[str]
    summary: str
