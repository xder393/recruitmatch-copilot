"""Explainable recommendation and recruiter feedback endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.api.v1.schemas import FeedbackRequest, FeedbackResponse, MatchResultResponse, MatchRunResponse
from app.models.matching import MatchResult, MatchRun
from app.security.tokens import Principal
from app.services.feedback import FeedbackService
from app.services.matching import MatchingService

router = APIRouter(tags=["RecruitMatch Recommendations"])


def _result_response(result: MatchResult) -> MatchResultResponse:
    return MatchResultResponse(
        id=result.id,
        rank=result.rank,
        job_id=result.job_version.job_id,
        job_version_id=result.job_version_id,
        job_title=result.job_version.job.title,
        total_score=result.total_score,
        dimension_scores=result.dimension_scores,
        matched_items=result.matched_items,
        missing_items=result.missing_items,
        uncertain_items=result.uncertain_items,
        evidence=result.evidence,
        risk_flags=result.risk_flags,
        summary=result.summary or "",
        rule_score=result.rule_score,
        semantic_score=result.semantic_score,
        grounding_status=result.grounding_status,
        fallback_reason=result.fallback_reason,
        citations=result.citations or [],
        grounded_explanation=result.grounded_explanation or {},
        interview_questions=result.interview_questions or [],
    )


def _run_response(run: MatchRun) -> MatchRunResponse:
    return MatchRunResponse(
        id=run.id,
        resume_id=run.resume_id,
        status=run.status,
        algorithm_version=run.algorithm_version,
        prompt_version=run.prompt_version,
        results=[_result_response(item) for item in run.results],
    )


@router.post("/resumes/{resume_id}/matches", response_model=MatchRunResponse, status_code=status.HTTP_201_CREATED)
def run_matches(
    resume_id: str,
    request: Request,
    mode: str = "rules-v1",
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _run_response(
        MatchingService(
            session,
            hybrid_engine=request.app.state.hybrid_matching_engine,
            explanation_service=request.app.state.grounded_explanation_service,
            source_index=request.app.state.knowledge_index,
            retrieval_top_k=request.app.state.settings.retrieval_top_k,
            retrieval_min_score=request.app.state.settings.retrieval_min_score,
        ).run(
            principal, resume_id, mode=mode
        )
    )


@router.get("/resumes/{resume_id}/matches", response_model=MatchRunResponse)
def latest_matches(
    resume_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _run_response(MatchingService(session).latest_for_resume(principal, resume_id))


@router.get("/match-runs/{run_id}", response_model=MatchRunResponse)
def get_match_run(
    run_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _run_response(MatchingService(session).get_run(principal, run_id))


@router.post(
    "/match-results/{result_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_feedback(
    result_id: str,
    payload: FeedbackRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    feedback = FeedbackService(session).submit(
        principal,
        result_id,
        payload.action,
        payload.reason,
        payload.corrected_job_version_id,
    )
    return FeedbackResponse(
        id=feedback.id,
        match_result_id=feedback.match_result_id,
        action=feedback.action,
        corrected_job_version_id=feedback.corrected_job_version_id,
        reason=feedback.reason,
    )
