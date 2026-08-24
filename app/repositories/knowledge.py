"""Tenant-scoped recruiting knowledge persistence."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.knowledge.schemas import ChunkInput
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_document(
        self,
        tenant_id: str,
        document_id: str,
        *,
        for_update: bool = False,
    ) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def by_checksum(self, tenant_id: str, checksum: str, document_type: str) -> KnowledgeDocument | None:
        return self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.document_type == document_type,
            )
        )

    def list_documents(self, tenant_id: str) -> list[KnowledgeDocument]:
        return list(
            self.session.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.tenant_id == tenant_id)
                .order_by(KnowledgeDocument.created_at.desc())
            )
        )

    def active_chunks(self, tenant_id: str, document_id: str | None = None) -> list[KnowledgeChunk]:
        statement = select(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.is_active.is_(True),
        )
        if document_id is not None:
            statement = statement.where(KnowledgeChunk.document_id == document_id)
        return list(self.session.scalars(statement.order_by(KnowledgeChunk.start)))

    def replace_generation(
        self,
        document: KnowledgeDocument,
        generation: int,
        chunks: list[ChunkInput],
    ) -> None:
        self.session.execute(
            update(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == document.tenant_id,
                KnowledgeChunk.document_id == document.id,
                KnowledgeChunk.is_active.is_(True),
            )
            .values(is_active=False)
        )
        for item in chunks:
            self.session.add(
                KnowledgeChunk(
                    tenant_id=document.tenant_id,
                    document_id=document.id,
                    source_type=item.source_type,
                    source_id=document.id,
                    source_version=str(generation),
                    generation=generation,
                    start=item.start,
                    end=item.end,
                    page=item.page,
                    content=item.content,
                    vector=item.vector,
                    is_active=True,
                )
            )
        document.active_generation = generation
        document.status = "ready"
        document.error_code = None
        document.error_message = None
        self.session.flush()

    def deactivate(self, document: KnowledgeDocument) -> None:
        document.status = "inactive"
        self.session.execute(
            update(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == document.tenant_id,
                KnowledgeChunk.document_id == document.id,
            )
            .values(is_active=False)
        )

    def add(self, document: KnowledgeDocument) -> None:
        self.session.add(document)

    def reload_document(self, tenant_id: str, document_id: str) -> KnowledgeDocument | None:
        self.session.expire_all()
        return self.get_document(tenant_id, document_id)
