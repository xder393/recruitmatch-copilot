"""RecruitMatch v1 request and response schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import Role


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
