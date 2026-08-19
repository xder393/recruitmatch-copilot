"""Generation-safe recruiting knowledge processing."""
from __future__ import annotations

from typing import Protocol

from app.core.exceptions import AppError
from app.knowledge.chunking import chunk_document
from app.knowledge.schemas import ChunkInput
from app.repositories.knowledge import KnowledgeRepository
from app.resumes.extractors import extract_text


class KnowledgeIndexer(Protocol):
    def embed(self, chunks: list[ChunkInput]) -> list[ChunkInput]: ...


class KnowledgeProcessingService:
    def __init__(self, session_factory, artifact_store, indexer: KnowledgeIndexer):
        self.session_factory = session_factory
        self.artifact_store = artifact_store
        self.indexer = indexer

    def process(self, tenant_id: str, document_id: str) -> None:
        with self.session_factory() as session:
            repository = KnowledgeRepository(session)
            document = repository.get_document(tenant_id, document_id)
            if document is None or document.status != "uploaded":
                return
            document.status = "processing"
            document.error_code = None
            document.error_message = None
            filename = document.original_filename
            artifact_key = document.artifact_key
            document_type = document.document_type
            next_generation = document.active_generation + 1
            session.commit()

        try:
            content = self.artifact_store.read(artifact_key)
            text = extract_text(filename, content)
            chunks = chunk_document(text, document_type)
            embedded = self.indexer.embed(chunks)
            if len(embedded) != len(chunks) or any(not item.vector for item in embedded):
                raise ValueError("embedding output incomplete")
        except AppError as exc:
            self._mark_failed(tenant_id, document_id, exc.code, "知识文档处理失败")
            return
        except Exception:
            self._mark_failed(tenant_id, document_id, "knowledge_processing_failed", "知识文档处理失败")
            return

        with self.session_factory() as session:
            repository = KnowledgeRepository(session)
            document = repository.get_document(tenant_id, document_id)
            if document is None or document.status == "inactive":
                return
            repository.replace_generation(document, next_generation, embedded)
            session.commit()

    def _mark_failed(self, tenant_id: str, document_id: str, code: str, message: str) -> None:
        with self.session_factory() as session:
            document = KnowledgeRepository(session).get_document(tenant_id, document_id)
            if document is None or document.status == "inactive":
                return
            document.status = "failed"
            document.error_code = code[:100]
            document.error_message = message[:500]
            session.commit()
