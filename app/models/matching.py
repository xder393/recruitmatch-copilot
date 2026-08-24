"""Versioned recommendation runs, results, and recruiter feedback."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.enums import FeedbackAction, MatchStatus


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MatchRun(Base):
    __tablename__ = "match_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False), default=MatchStatus.QUEUED, index=True, nullable=False
    )
    algorithm_version: Mapped[str] = mapped_column(String(100), default="rules-v1", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), default="none", nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    resume: Mapped["Resume"] = relationship()
    results: Mapped[List["MatchResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="MatchResult.rank"
    )


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("run_id", "rank", name="uq_match_run_rank"),
        Index("ix_match_results_run_grounding", "run_id", "grounding_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("match_runs.id", ondelete="CASCADE"), index=True, nullable=False)
    job_version_id: Mapped[str] = mapped_column(ForeignKey("job_versions.id"), index=True, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    rule_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    semantic_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grounding_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    fallback_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    citations: Mapped[Optional[List[Any]]] = mapped_column(JSON, default=list, nullable=True)
    grounded_explanation: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    interview_questions: Mapped[Optional[List[Any]]] = mapped_column(JSON, default=list, nullable=True)
    dimension_scores: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    matched_items: Mapped[List[Any]] = mapped_column(JSON, default=list, nullable=False)
    missing_items: Mapped[List[Any]] = mapped_column(JSON, default=list, nullable=False)
    uncertain_items: Mapped[List[Any]] = mapped_column(JSON, default=list, nullable=False)
    evidence: Mapped[List[Any]] = mapped_column(JSON, default=list, nullable=False)
    risk_flags: Mapped[List[Any]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    run: Mapped[MatchRun] = relationship(back_populates="results")
    job_version: Mapped["JobVersion"] = relationship()
    feedback_entries: Mapped[List["Feedback"]] = relationship(
        back_populates="result", cascade="all, delete-orphan", order_by="Feedback.created_at"
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    match_result_id: Mapped[str] = mapped_column(
        ForeignKey("match_results.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    action: Mapped[FeedbackAction] = mapped_column(Enum(FeedbackAction, native_enum=False), nullable=False)
    corrected_job_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_versions.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    result: Mapped[MatchResult] = relationship(back_populates="feedback_entries")
    user: Mapped["User"] = relationship()
    corrected_job_version: Mapped[Optional["JobVersion"]] = relationship(foreign_keys=[corrected_job_version_id])


from app.models.identity import User  # noqa: E402,F401
from app.models.jobs import JobVersion  # noqa: E402,F401
from app.models.resumes import Resume  # noqa: E402,F401
