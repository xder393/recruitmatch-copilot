"""Pure matching input and output contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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


class HybridMatchRecommendation(MatchRecommendation):
    rule_score: float = Field(ge=0, le=1)
    semantic_score: Optional[float] = Field(default=None, ge=0, le=1)
    grounding_status: str
    fallback_reason: Optional[str] = None
    citations: List[Any] = Field(default_factory=list)
    grounded_explanation: Dict[str, Any] = Field(default_factory=dict)
    interview_questions: List[Any] = Field(default_factory=list)
