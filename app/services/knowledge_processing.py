"""Generation-safe recruiting knowledge processing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.core.exceptions import AppError
from app.knowledge.chunking import chunk_document
from app.knowledge.schemas import ChunkInput
from app.repositories.unit_of_work import UnitOfWorkFactory
from app.resumes.extractors import extract_text


class KnowledgeIndexer(Protocol):
    def embed(self, chunks: list[ChunkInput]) -> list[ChunkInput]: ...


class KnowledgeProcessingService:
    lease_seconds = 300

    def __init__(self, uow_factory: UnitOfWorkFactory, artifact_store, indexer: KnowledgeIndexer):
        self.uow_factory = uow_factory
        self.artifact_store = artifact_store
        self.indexer = indexer

    def process(self, tenant_id: str, document_id: str) -> bool:
        with self.uow_factory() as uow:
            document = uow.knowledge.get_document(tenant_id, document_id, for_update=True)
            if document is None or document.status in {"ready", "inactive", "failed"}:
                return True
            if document.status == "processing" and not self._lease_expired(document.updated_at):
                return False
            if document.status not in {"uploaded", "processing"}:
                return True
            document.status = "processing"
            document.updated_at = datetime.now(timezone.utc)
            document.error_code = None
            document.error_message = None
            filename = document.original_filename
            artifact_key = document.artifact_key
            document_type = document.document_type
            next_generation = document.active_generation + 1
            uow.commit()

        try:
            content = self.artifact_store.read(artifact_key)
            text = extract_text(filename, content)
            chunks = chunk_document(text, document_type)
            embedded = self.indexer.embed(chunks)
            if len(embedded) != len(chunks) or any(not item.vector for item in embedded):
                raise ValueError("embedding output incomplete")
        except AppError as exc:
            self._mark_failed(tenant_id, document_id, exc.code, "知识文档处理失败")
            return True
        except Exception:
            self._mark_failed(tenant_id, document_id, "knowledge_processing_failed", "知识文档处理失败")
            return True

        with self.uow_factory() as uow:
            repository = uow.knowledge
            document = repository.get_document(tenant_id, document_id)
            if document is None or document.status == "inactive":
                return True
            repository.replace_generation(document, next_generation, embedded)
            uow.commit()
        return True

    def _lease_expired(self, updated_at: datetime) -> bool:
        timestamp = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=timezone.utc)
        return timestamp <= datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)

    def _mark_failed(self, tenant_id: str, document_id: str, code: str, message: str) -> None:
        with self.uow_factory() as uow:
            document = uow.knowledge.get_document(tenant_id, document_id)
            if document is None or document.status == "inactive":
                return
            document.status = "failed"
            document.error_code = code[:100]
            document.error_message = message[:500]
            uow.commit()
