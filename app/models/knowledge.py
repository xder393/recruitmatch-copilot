"""Recruiting knowledge documents and generation-versioned chunks."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "checksum", "document_type", name="uq_knowledge_tenant_checksum_type"),
        ForeignKeyConstraint(
            ["tenant_id", "id", "artifact_id"],
            ["artifacts.tenant_id", "artifacts.owner_id", "artifacts.id"],
            name="fk_knowledge_documents_artifact_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_key: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    artifact_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    active_index_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_index_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    search_index_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    search_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active_generation = synonym("active_index_generation")
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
