"""Tenant-scoped privacy-safe evaluation summaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.repositories.evaluation import AIEvaluationRepository
from app.security.tokens import Principal

router = APIRouter(prefix="/evaluations", tags=["RecruitMatch Evaluation"])


class EvaluationSummary(BaseModel):
    id: str
    dataset_version: str
    algorithm_version: str
    model_version: str
    prompt_version: str
    embedding_version: str
    case_count: int
    metrics: dict[str, Any]
    failure_categories: dict[str, int]


@router.get("/summary", response_model=list[EvaluationSummary])
def evaluation_summary(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    summaries = []
    for run in AIEvaluationRepository(session).list_for_tenant(principal.tenant_id):
        categories: dict[str, int] = {}
        for case in run.cases:
            for category in case.failure_categories:
                categories[category] = categories.get(category, 0) + 1
        summaries.append(
            EvaluationSummary(
                id=run.id,
                dataset_version=run.dataset_version,
                algorithm_version=run.algorithm_version,
                model_version=run.model_version,
                prompt_version=run.prompt_version,
                embedding_version=run.embedding_version,
                case_count=run.case_count,
                metrics=run.metrics,
                failure_categories=categories,
            )
        )
    return summaries
