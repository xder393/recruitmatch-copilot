"""Persistence helpers for safe AI benchmark aggregates and failure labels."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.evaluation import AIEvaluationRun


class AIEvaluationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, run: AIEvaluationRun) -> AIEvaluationRun:
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run
