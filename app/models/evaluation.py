"""Privacy-safe metadata for reproducible AI evaluation runs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AIEvaluationRun(Base):
    __tablename__ = "ai_evaluation_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dataset_version",
            "algorithm_version",
            "model_version",
            "prompt_version",
            "embedding_version",
            name="uq_ai_evaluation_version_set",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(200), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    cases: Mapped[List["AIEvaluationCase"]] = relationship(cascade="all, delete-orphan", back_populates="run")


class AIEvaluationCase(Base):
    __tablename__ = "ai_evaluation_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("ai_evaluation_runs.id", ondelete="CASCADE"), index=True)
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    failure_categories: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    run: Mapped[AIEvaluationRun] = relationship(back_populates="cases")
