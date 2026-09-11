"""Tenant-scoped resume persistence."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.enums import ResumeStatus
from app.models.resumes import Resume
from app.models.retrieval import RecruitingChunk
from app.repositories.matching import MatchingRepository


class ResumeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(
        self,
        tenant_id: str,
        resume_id: str,
        include_deleted: bool = False,
        *,
        for_update: bool = False,
    ) -> Optional[Resume]:
        statement = select(Resume).where(Resume.id == resume_id, Resume.tenant_id == tenant_id)
        if not include_deleted:
            statement = statement.where(Resume.lifecycle_status == "active")
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def find_by_hash(self, tenant_id: str, sha256: str) -> Optional[Resume]:
        return self.session.scalar(
            select(Resume).where(
                Resume.tenant_id == tenant_id,
                Resume.sha256 == sha256,
                Resume.lifecycle_status == "active",
            )
        )

    def list(self, tenant_id: str) -> List[Resume]:
        return list(
            self.session.scalars(
                select(Resume)
                .where(Resume.tenant_id == tenant_id, Resume.lifecycle_status == "active")
                .order_by(Resume.created_at.desc())
            )
        )

    def add(self, resume: Resume) -> None:
        self.session.add(resume)

    def list_succeeded(self, tenant_id: str) -> List[Resume]:
        return list(
            self.session.scalars(
                select(Resume).where(
                    Resume.tenant_id == tenant_id,
                    Resume.status == ResumeStatus.SUCCEEDED,
                    Resume.lifecycle_status == "active",
                )
            )
        )

    def reload(self, tenant_id: str, resume_id: str) -> Optional[Resume]:
        self.session.expire_all()
        return self.get(tenant_id, resume_id)

    def scrub_private_data(self, tenant_id: str, resume: Resume, deleted_at: datetime) -> None:
        resume.sha256 = None
        resume.original_filename = None
        resume.size_bytes = 0
        resume.uploaded_by = None
        resume.profile = {}
        resume.error_code = None
        resume.error_message = None
        resume.search_index_status = "deleted"
        resume.search_index_error = None
        resume.search_indexed_at = None
        resume.extracted_text = None
        self.session.execute(
            delete(RecruitingChunk).where(
                RecruitingChunk.tenant_id == tenant_id,
                RecruitingChunk.source_type == "resume",
                RecruitingChunk.source_id == resume.id,
            )
        )
        MatchingRepository(self.session).scrub_private_results(tenant_id, resume_id=resume.id)
        resume.lifecycle_status = "deleted"
        resume.deleted_at = deleted_at
