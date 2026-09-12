"""Tenant-owned resume metadata and extracted artifacts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    Computed,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    func,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.database import Base
from app.domain.enums import ResumeStatus


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint("lifecycle_status IN ('active', 'deleted')", name="ck_resume_lifecycle"),
        UniqueConstraint("tenant_id", "sha256", name="uq_resume_tenant_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_owner_type", "id", "artifact_id"],
            ["artifacts.tenant_id", "artifacts.owner_type", "artifacts.owner_id", "artifacts.id"],
            name="fk_resumes_artifact_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    artifact_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    artifact_owner_type: Mapped[str] = mapped_column(String(18), Computed("'resume'", persisted=True), nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(10), default="active", server_default="active", nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ResumeStatus] = mapped_column(
        Enum(ResumeStatus, native_enum=False), default=ResumeStatus.QUEUED, index=True, nullable=False
    )
    profile: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    processing_lease_epoch: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    processing_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_lease_owner: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    search_index_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    search_index_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    search_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active_index_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_index_error = synonym("search_index_error_code")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
