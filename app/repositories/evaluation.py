"""Persistence helpers for safe AI benchmark aggregates and failure labels."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.evaluation import AIEvaluationRun


class AIEvaluationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, run: AIEvaluationRun) -> AIEvaluationRun:
        if not run.tenant_id:
            raise ValueError("tenant_id is required for persisted evaluation runs")
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def list_for_tenant(self, tenant_id: str) -> list[AIEvaluationRun]:
        return list(
            self.session.scalars(
                select(AIEvaluationRun)
                .options(selectinload(AIEvaluationRun.cases))
                .where(AIEvaluationRun.tenant_id == tenant_id)
                .order_by(AIEvaluationRun.created_at.desc())
            )
        )
