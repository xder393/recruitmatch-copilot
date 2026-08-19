"""Resume extraction and profiling lifecycle orchestration."""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError
from app.domain.enums import ResumeStatus
from app.models.resumes import Resume
from app.resumes.extractors import extract_text
from app.resumes.schemas import ResumeProfile


class ArtifactReader(Protocol):
    def read(self, key: str) -> bytes: ...


class ResumeParser(Protocol):
    def parse(self, text: str) -> ResumeProfile: ...


class ResumeProcessingService:
    def __init__(self, session_factory, artifact_store: ArtifactReader, parser: ResumeParser):
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.parser = parser

    def process(self, tenant_id: str, resume_id: str) -> None:
        with self.session_factory() as session:
            resume = self._get(session, tenant_id, resume_id)
            if resume is None or resume.status is ResumeStatus.DELETED:
                return
            if resume.artifact is None:
                self._mark_failed(tenant_id, resume_id, "artifact_missing", "简历文件记录不存在")
                return
            resume.status = ResumeStatus.RUNNING
            resume.error_code = None
            resume.error_message = None
            filename = resume.original_filename
            storage_key = resume.artifact.storage_key
            session.commit()

        try:
            content = self.artifact_store.read(storage_key)
        except Exception:
            self._mark_failed(tenant_id, resume_id, "artifact_read_failed", "无法读取简历文件")
            return

        try:
            text = extract_text(filename, content)
            profile = self.parser.parse(text)
        except AppError as exc:
            self._mark_failed(tenant_id, resume_id, exc.code, exc.message)
            return
        except Exception:
            self._mark_failed(tenant_id, resume_id, "resume_processing_failed", "简历处理失败")
            return

        with self.session_factory() as session:
            resume = self._get(session, tenant_id, resume_id)
            if resume is None or resume.status is ResumeStatus.DELETED:
                return
            resume.artifact.extracted_text = text
            resume.profile = profile.model_dump(mode="json")
            resume.status = ResumeStatus.SUCCEEDED
            resume.error_code = None
            resume.error_message = None
            session.commit()

    def _mark_failed(self, tenant_id: str, resume_id: str, code: str, message: str) -> None:
        with self.session_factory() as session:
            resume = self._get(session, tenant_id, resume_id)
            if resume is None or resume.status is ResumeStatus.DELETED:
                return
            resume.status = ResumeStatus.FAILED
            resume.error_code = code
            resume.error_message = message[:500]
            session.commit()

    @staticmethod
    def _get(session, tenant_id: str, resume_id: str):
        return session.scalar(
            select(Resume)
            .options(selectinload(Resume.artifact))
            .where(Resume.id == resume_id, Resume.tenant_id == tenant_id)
        )
