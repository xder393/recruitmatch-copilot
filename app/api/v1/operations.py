"""Model-independent health and tenant-scoped operations summaries."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.domain.enums import JobStatus, ResumeStatus
from app.models.jobs import Job, JobVersion
from app.models.matching import Feedback, MatchRun
from app.models.operations import ModelTrace
from app.models.resumes import Resume
from app.security.tokens import Principal

router = APIRouter(tags=["RecruitMatch Operations"])


@router.get("/health/live")
def liveness():
    return {"status": "alive"}


@router.get("/health/ready")
def readiness(session: Session = Depends(get_db)):
    session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}


@router.get("/ai/status")
def ai_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    settings = request.app.state.settings
    tenant_id = principal.tenant_id
    latest = session.scalar(
        select(ModelTrace)
        .where(ModelTrace.tenant_id == tenant_id)
        .order_by(ModelTrace.created_at.desc())
        .limit(1)
    )
    failed_indexes = (
        session.scalar(
            select(func.count())
            .select_from(Resume)
            .where(
                Resume.tenant_id == tenant_id,
                Resume.status != ResumeStatus.DELETED,
                Resume.search_index_status == "failed",
            )
        )
        or 0
    ) + (
        session.scalar(
            select(func.count())
            .select_from(JobVersion)
            .join(Job, Job.id == JobVersion.job_id)
            .where(
                Job.tenant_id == tenant_id,
                Job.status == JobStatus.ACTIVE,
                JobVersion.version == Job.current_version,
                JobVersion.search_index_status == "failed",
            )
        )
        or 0
    )
    latest_failed = latest is not None and latest.status == "failed"
    degraded = not settings.ai_enabled or latest is None or latest_failed or failed_indexes > 0
    return {
        "configured": bool(settings.api_key),
        "enabled": settings.ai_enabled,
        "available": False if not settings.ai_enabled else (None if latest is None else not latest_failed),
        "degraded": degraded,
        "fallback_mode": "rules-v1" if degraded else None,
        "provider": settings.model_provider,
        "model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "core_available": True,
        "latest_status": latest.status if latest else None,
        "latest_error": latest.error_code if latest else None,
        "latest_latency_ms": latest.latency_ms if latest else None,
        "failed_source_indexes": failed_indexes,
    }


@router.get("/analytics/summary")
def analytics_summary(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    tenant_id = principal.tenant_id
    jobs = session.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == tenant_id)) or 0
    resumes = (
        session.scalar(
            select(func.count())
            .select_from(Resume)
            .where(Resume.tenant_id == tenant_id, Resume.status != ResumeStatus.DELETED)
        )
        or 0
    )
    match_runs = (
        session.scalar(select(func.count()).select_from(MatchRun).where(MatchRun.tenant_id == tenant_id)) or 0
    )
    feedback = (
        session.scalar(select(func.count()).select_from(Feedback).where(Feedback.tenant_id == tenant_id)) or 0
    )
    return {"jobs": jobs, "resumes": resumes, "match_runs": match_runs, "feedback": feedback}
