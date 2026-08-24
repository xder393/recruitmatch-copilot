"""Tenant-scoped resume persistence."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import ResumeStatus
from app.models.resumes import Resume


class ResumeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, tenant_id: str, resume_id: str, include_deleted: bool = False) -> Optional[Resume]:
        statement = (
            select(Resume)
            .options(selectinload(Resume.artifact))
            .where(Resume.id == resume_id, Resume.tenant_id == tenant_id)
        )
        if not include_deleted:
            statement = statement.where(Resume.status != ResumeStatus.DELETED)
        return self.session.scalar(statement)

    def find_by_hash(self, tenant_id: str, sha256: str) -> Optional[Resume]:
        return self.session.scalar(
            select(Resume)
            .options(selectinload(Resume.artifact))
            .where(
                Resume.tenant_id == tenant_id,
                Resume.sha256 == sha256,
                Resume.status != ResumeStatus.DELETED,
            )
        )

    def list(self, tenant_id: str) -> List[Resume]:
        return list(
            self.session.scalars(
                select(Resume)
                .options(selectinload(Resume.artifact))
                .where(Resume.tenant_id == tenant_id, Resume.status != ResumeStatus.DELETED)
                .order_by(Resume.created_at.desc())
            )
        )

    def add(self, resume: Resume) -> None:
        self.session.add(resume)
