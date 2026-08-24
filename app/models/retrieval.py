"""PostgreSQL-backed recruiting evidence chunks."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecruitingChunk(Base):
    """One immutable, tenant-owned unit of authorized recruiting evidence."""

    __tablename__ = "recruiting_chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "citation_id", name="uq_recruiting_chunk_tenant_citation"),
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_id",
            "source_version",
            "generation",
            "start_offset",
            "end_offset",
            name="uq_recruiting_chunk_source_generation_offsets",
        ),
        CheckConstraint(
            "source_type IN ('resume', 'job_version', 'knowledge_document')",
            name="ck_recruiting_chunk_source_type",
        ),
        CheckConstraint("generation > 0", name="ck_recruiting_chunk_generation_positive"),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="ck_recruiting_chunk_offsets"),
        Index("ix_recruiting_chunk_tenant_type_active", "tenant_id", "source_type", "is_active"),
        Index(
            "ix_recruiting_chunk_source_active",
            "tenant_id",
            "source_type",
            "source_id",
            "source_version",
            "is_active",
        ),
        Index(
            "ix_recruiting_chunk_document_generation_active",
            "tenant_id",
            "document_id",
            "generation",
            "is_active",
            postgresql_where=text("document_id IS NOT NULL"),
        ),
        Index(
            "ix_recruiting_chunk_embedding_hnsw_active",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    citation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
