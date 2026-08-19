"""Structured resume profile and evidence contracts."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class SkillEvidence(BaseModel):
    name: str
    evidence: Evidence


class ProjectEvidence(BaseModel):
    name: str
    description: str = ""
    evidence: Evidence


class ResumeProfile(BaseModel):
    schema_version: str = "1.0"
    skills: List[SkillEvidence] = Field(default_factory=list)
    experience_years: Optional[float] = None
    experience_evidence: Optional[Evidence] = None
    education_level: Optional[str] = None
    education_evidence: Optional[Evidence] = None
    projects: List[ProjectEvidence] = Field(default_factory=list)
