"""RecruitMatch v1 request and response schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.enums import FeedbackAction, JobStatus, MatchStatus, ResumeStatus, Role


class BootstrapRequest(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    role: Role
    tenant_id: str
    tenant_name: str


class JobCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    jd_text: str = Field(min_length=1, max_length=50000)
    profile: Dict[str, Any] = Field(default_factory=dict)


class JobUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=200)
    jd_text: Optional[str] = Field(default=None, min_length=1, max_length=50000)
    profile: Optional[Dict[str, Any]] = None


class JobVersionResponse(BaseModel):
    id: str
    version: int
    jd_text: str
    profile: Dict[str, Any]
    created_by: Optional[str]
    search_index_status: str
    search_index_error: Optional[str]


class JobResponse(BaseModel):
    id: str
    title: str
    status: JobStatus
    current_version: int
    versions: List[JobVersionResponse]


class JobListResponse(BaseModel):
    items: List[JobResponse]
    total: int


class JobTemplateResponse(BaseModel):
    id: str
    slug: str
    title: str
    jd_text: str
    profile: Dict[str, Any]


class ResumeResponse(BaseModel):
    id: str
    original_filename: str
    media_type: str
    size_bytes: int
    status: ResumeStatus
    profile: Dict[str, Any]
    error_code: Optional[str]
    error_message: Optional[str]
    search_index_status: str
    search_index_error: Optional[str]


class ResumeListResponse(BaseModel):
    items: List[ResumeResponse]
    total: int


class MatchResultResponse(BaseModel):
    id: str
    rank: int
    job_id: str
    job_version_id: str
    job_title: str
    total_score: float
    dimension_scores: Dict[str, Any]
    matched_items: List[str]
    missing_items: List[str]
    uncertain_items: List[str]
    evidence: List[Dict[str, Any]]
    risk_flags: List[str]
    summary: str
    rule_score: Optional[float] = None
    semantic_score: Optional[float] = Field(default=None, ge=0, le=100, description="语义项目匹配分，范围 0–100")
    grounding_status: Optional[str] = None
    fallback_reason: Optional[str] = None
    citations: List[Any] = Field(default_factory=list)
    grounded_explanation: Dict[str, Any] = Field(default_factory=dict)
    interview_questions: List[Any] = Field(default_factory=list)


class MatchRunResponse(BaseModel):
    id: str
    resume_id: str
    status: MatchStatus
    algorithm_version: str
    prompt_version: str
    results: List[MatchResultResponse]


class FeedbackRequest(BaseModel):
    action: FeedbackAction
    corrected_job_version_id: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=500)


class FeedbackResponse(BaseModel):
    id: str
    match_result_id: str
    action: FeedbackAction
    corrected_job_version_id: Optional[str]
    reason: Optional[str]
