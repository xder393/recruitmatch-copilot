"""Replaceable knowledge processing dispatchers."""
from __future__ import annotations


class InlineKnowledgeDispatcher:
    def __init__(self, processor):
        self.processor = processor

    def dispatch_knowledge(self, tenant_id: str, document_id: str) -> None:
        self.processor.process(tenant_id, document_id)


class CeleryKnowledgeDispatcher:
    def dispatch_knowledge(self, tenant_id: str, document_id: str) -> None:
        from app.tasks.celery_app import process_knowledge_task

        process_knowledge_task.delay(tenant_id, document_id)
