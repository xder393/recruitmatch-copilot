"""Tenant-scoped recommendation feedback persistence."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.jobs import Job, JobVersion
from app.models.matching import MatchResult, MatchRun


class FeedbackRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_result(self, tenant_id: str, result_id: str) -> Optional[MatchResult]:
        return self.session.scalar(
            select(MatchResult)
            .join(MatchRun, MatchResult.run_id == MatchRun.id)
            .options(selectinload(MatchResult.job_version).selectinload(JobVersion.job))
            .where(MatchResult.id == result_id, MatchRun.tenant_id == tenant_id)
        )

    def get_job_version(self, tenant_id: str, version_id: str) -> Optional[JobVersion]:
        return self.session.scalar(
            select(JobVersion)
            .join(Job, JobVersion.job_id == Job.id)
            .where(JobVersion.id == version_id, Job.tenant_id == tenant_id)
        )
