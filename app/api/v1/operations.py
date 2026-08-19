"""Model-independent health and tenant-scoped operations summaries."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.domain.enums import ResumeStatus
from app.models.jobs import Job
from app.models.matching import Feedback, MatchRun
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
def ai_status(request: Request):
    settings = request.app.state.settings
    return {
        "enabled": settings.ai_enabled,
        "provider": settings.model_provider,
        "model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "core_available": True,
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
