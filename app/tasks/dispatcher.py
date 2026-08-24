"""Replaceable resume task dispatchers."""

from __future__ import annotations

from typing import Protocol


class TaskDispatcher(Protocol):
    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None: ...


class InlineTaskDispatcher:
    """Deterministic local/test dispatcher."""

    def __init__(self, processor):
        self.processor = processor

    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None:
        self.processor.process(tenant_id, resume_id)


class CeleryTaskDispatcher:
    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None:
        from app.tasks.celery_app import process_resume_task

        process_resume_task.delay(tenant_id, resume_id)
