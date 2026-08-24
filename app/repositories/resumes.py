"""Tenant-scoped resume persistence."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import ResumeStatus
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeChunk
from app.models.matching import MatchResult, MatchRun


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
        statement = (
            select(Resume)
            .options(selectinload(Resume.artifact))
            .where(Resume.id == resume_id, Resume.tenant_id == tenant_id)
        )
        if not include_deleted:
            statement = statement.where(Resume.status != ResumeStatus.DELETED)
        if for_update:
            statement = statement.with_for_update()
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

    def list_succeeded(self, tenant_id: str) -> List[Resume]:
        return list(
            self.session.scalars(
                select(Resume)
                .options(selectinload(Resume.artifact))
                .where(Resume.tenant_id == tenant_id, Resume.status == ResumeStatus.SUCCEEDED)
            )
        )

    def reload(self, tenant_id: str, resume_id: str) -> Optional[Resume]:
        self.session.expire_all()
        return self.get(tenant_id, resume_id)

    def scrub_private_data(self, tenant_id: str, resume: Resume, deleted_at: datetime) -> None:
        resume.sha256 = hashlib.sha256(f"deleted:{resume.id}".encode()).hexdigest()
        resume.original_filename = "deleted"
        resume.size_bytes = 0
        resume.uploaded_by = None
        resume.profile = {}
        resume.error_code = None
        resume.error_message = None
        resume.search_index_status = "deleted"
        resume.search_index_error = None
        resume.search_indexed_at = None
        if resume.artifact is not None:
            resume.artifact.extracted_text = None
        self.session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.source_type == "resume",
                KnowledgeChunk.source_id == resume.id,
            )
        )
        run_ids = select(MatchRun.id).where(MatchRun.tenant_id == tenant_id, MatchRun.resume_id == resume.id)
        self.session.execute(
            update(MatchResult)
            .where(MatchResult.run_id.in_(run_ids))
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
            )
        )
        resume.status = ResumeStatus.DELETED
        resume.deleted_at = deleted_at
