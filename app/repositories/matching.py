"""Tenant-scoped recommendation persistence queries."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import JobStatus, ResumeStatus
from app.models.jobs import Job, JobVersion
from app.models.matching import Feedback, MatchResult, MatchRun
from app.models.resumes import Resume


class MatchingRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_succeeded_resume(self, tenant_id: str, resume_id: str) -> Optional[Resume]:
        return self.session.scalar(
            select(Resume)
            .where(
                Resume.id == resume_id,
                Resume.tenant_id == tenant_id,
                Resume.status == ResumeStatus.SUCCEEDED,
                Resume.lifecycle_status == "active",
            )
            .with_for_update(of=Resume)
            .execution_options(populate_existing=True)
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

    def scrub_private_results(self, tenant_id: str, *, resume_id: str | None = None) -> None:
        runs = select(MatchRun.id).where(MatchRun.tenant_id == tenant_id)
        if resume_id is not None:
            runs = runs.where(MatchRun.resume_id == resume_id)
        results = select(MatchResult.id).where(MatchResult.run_id.in_(runs))
        self.session.execute(
            update(Feedback)
            .where(Feedback.tenant_id == tenant_id, Feedback.match_result_id.in_(results))
            .values(reason=None)
        )
        self.session.execute(
            update(MatchResult)
            .where(MatchResult.run_id.in_(runs))
            .values(
                dimension_scores={},
                matched_items=[],
                missing_items=[],
                uncertain_items=[],
                evidence=[],
                risk_flags=[],
                summary=None,
                citations=[],
                grounded_explanation={},
                interview_questions=[],
                grounding_status="privacy_redacted",
                fallback_reason=None,
            )
        )

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
            .options(selectinload(MatchRun.results).selectinload(MatchResult.job_version).selectinload(JobVersion.job))
            .where(MatchRun.tenant_id == tenant_id, MatchRun.resume_id == resume_id)
            .order_by(MatchRun.created_at.desc())
            .limit(1)
        )
