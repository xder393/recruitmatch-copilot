"""Recruiting knowledge documents and generation-versioned chunks."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    func,
    Integer,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_recovery_dispatch", "recovery_dispatch_at", "tenant_id", "id"),
        CheckConstraint("lifecycle_status IN ('active', 'deleted')", name="ck_knowledge_lifecycle"),
        UniqueConstraint("tenant_id", "checksum", "document_type", name="uq_knowledge_tenant_checksum_type"),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_owner_type", "id", "artifact_id"],
            ["artifacts.tenant_id", "artifacts.owner_type", "artifacts.owner_id", "artifacts.id"],
            name="fk_knowledge_documents_artifact_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(10), default="active", server_default="active", nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    artifact_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    artifact_owner_type: Mapped[str] = mapped_column(
        String(18), Computed("'knowledge_document'", persisted=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    active_index_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_index_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    search_index_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    search_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active_generation = synonym("active_index_generation")
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    processing_lease_epoch: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    processing_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_lease_owner: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_dispatch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_dispatch_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    recovery_dispatch_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
