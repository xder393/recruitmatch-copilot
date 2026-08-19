"""RecruitMatch v1 request and response schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.enums import JobStatus, Role


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
