"""Tenant-scoped job catalog persistence."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.jobs import Job, JobTemplate


class JobRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_templates(self) -> List[JobTemplate]:
        return list(self.session.scalars(select(JobTemplate).order_by(JobTemplate.title)))

    def get(self, tenant_id: str, job_id: str) -> Optional[Job]:
        statement = (
            select(Job)
            .options(selectinload(Job.versions))
            .where(Job.id == job_id, Job.tenant_id == tenant_id)
        )
        return self.session.scalar(statement)

    def list(self, tenant_id: str) -> List[Job]:
        statement = (
            select(Job)
            .options(selectinload(Job.versions))
            .where(Job.tenant_id == tenant_id)
            .order_by(Job.updated_at.desc())
        )
        return list(self.session.scalars(statement))

    def add(self, job: Job) -> None:
        self.session.add(job)
