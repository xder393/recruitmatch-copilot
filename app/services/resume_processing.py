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
from app.repositories.model_traces import ModelTraceWriter


class ArtifactReader(Protocol):
    def read(self, key: str) -> bytes: ...


class ResumeParser(Protocol):
    def parse(self, text: str) -> ResumeProfile: ...


class ResumeProcessingService:
    def __init__(self, session_factory, artifact_store: ArtifactReader, parser: ResumeParser, source_index=None):
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.parser = parser
        self.source_index = source_index

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
            source_version = resume.sha256
            session.commit()

        try:
            content = self.artifact_store.read(storage_key)
        except Exception:
            self._mark_failed(tenant_id, resume_id, "artifact_read_failed", "无法读取简历文件")
            return

        try:
            text = extract_text(filename, content)
            parse_result = (
                self.parser.parse_with_metadata(text) if hasattr(self.parser, "parse_with_metadata") else None
            )
            profile = parse_result.profile if parse_result is not None else self.parser.parse(text)
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
            if parse_result is not None:
                writer = ModelTraceWriter(session)
                if parse_result.response is not None:
                    writer.succeeded(
                        tenant_id,
                        "resume_extract",
                        resume_id,
                        [resume_id],
                        parse_result.request,
                        parse_result.response,
                    )
                elif parse_result.error is not None:
                    writer.failed(
                        tenant_id,
                        "resume_extract",
                        resume_id,
                        [resume_id],
                        parse_result.request,
                        parse_result.error,
                        parse_result.latency_ms,
                    )
            session.commit()

        if self.source_index is not None:
            try:
                from app.knowledge.chunking import chunk_document

                self.source_index.index_source(
                    tenant_id,
                    "resume",
                    resume_id,
                    source_version,
                    chunk_document(text, "resume"),
                )
            except Exception:
                # Search enrichment must never roll back an otherwise valid
                # resume; re-indexing can repair this side effect later.
                pass

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
