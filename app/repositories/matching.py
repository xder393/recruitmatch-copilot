"""Tenant-scoped recommendation persistence queries."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import JobStatus, ResumeStatus
from app.models.jobs import Job, JobVersion
from app.models.matching import MatchResult, MatchRun
from app.models.resumes import Resume


class MatchingRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_succeeded_resume(self, tenant_id: str, resume_id: str) -> Optional[Resume]:
        return self.session.scalar(
            select(Resume)
            .options(selectinload(Resume.artifact))
            .where(
                Resume.id == resume_id,
                Resume.tenant_id == tenant_id,
                Resume.status == ResumeStatus.SUCCEEDED,
            )
        )

    def active_jobs(self, tenant_id: str) -> List[Job]:
        return list(
            self.session.scalars(
                select(Job)
                .options(selectinload(Job.versions))
                .where(Job.tenant_id == tenant_id, Job.status == JobStatus.ACTIVE)
                .order_by(Job.title)
            )
        )

    def add_run(self, run: MatchRun) -> None:
        self.session.add(run)

    def get_run(self, tenant_id: str, run_id: str) -> Optional[MatchRun]:
        result_loader = selectinload(MatchRun.results).selectinload(MatchResult.job_version)
        return self.session.scalar(
            select(MatchRun)
            .options(result_loader.selectinload(JobVersion.job))
            .where(MatchRun.id == run_id, MatchRun.tenant_id == tenant_id)
        )

    def latest_for_resume(self, tenant_id: str, resume_id: str) -> Optional[MatchRun]:
        return self.session.scalar(
            select(MatchRun)
            .options(
                selectinload(MatchRun.results)
                .selectinload(MatchResult.job_version)
                .selectinload(JobVersion.job)
            )
            .where(MatchRun.tenant_id == tenant_id, MatchRun.resume_id == resume_id)
            .order_by(MatchRun.created_at.desc())
            .limit(1)
        )
