"""Infrastructure-independent recruiting task dispatchers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskDispatcher(Protocol):
    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None:
        raise NotImplementedError

    def dispatch_knowledge(self, tenant_id: str, document_id: str) -> None:
        raise NotImplementedError


class InlineTaskDispatcher:
    """Deterministic local/test dispatcher."""

    def __init__(self, resume_processor, knowledge_processor=None):
        self.resume_processor = resume_processor
        self.knowledge_processor = knowledge_processor

    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None:
        self.resume_processor.process(tenant_id, resume_id)

    def dispatch_knowledge(self, tenant_id: str, document_id: str) -> None:
        if self.knowledge_processor is None:
            raise RuntimeError("knowledge processor is not configured")
        self.knowledge_processor.process(tenant_id, document_id)


class CeleryTaskDispatcher:
    def dispatch_resume(self, tenant_id: str, resume_id: str) -> None:
        from app.tasks.celery_app import process_resume_task

        self._publish(process_resume_task, (tenant_id, resume_id))

    def dispatch_knowledge(self, tenant_id: str, document_id: str) -> None:
        from app.tasks.celery_app import process_knowledge_task

        self._publish(process_knowledge_task, (tenant_id, document_id))

    @staticmethod
    def _publish(task, args):
        from kombu.exceptions import OperationalError  # type: ignore[import-untyped]
        from redis.exceptions import RedisError
        from app.processing.recovery import RecoveryDispatchUnavailable

        try:
            task.apply_async(args=args, argsrepr="[redacted]", kwargsrepr="[redacted]")
        except (OperationalError, RedisError, OSError):
            raise RecoveryDispatchUnavailable() from None


def build_task_dispatcher(task_mode: str, resume_processor, knowledge_processor) -> TaskDispatcher:
    if task_mode == "celery":
        return CeleryTaskDispatcher()
    return InlineTaskDispatcher(resume_processor, knowledge_processor)


def configure_task_dispatcher(app, task_mode: str, resume_processor, knowledge_processor) -> TaskDispatcher:
    dispatcher = build_task_dispatcher(task_mode, resume_processor, knowledge_processor)
    app.state.task_dispatcher = dispatcher
    app.state.knowledge_dispatcher = dispatcher
    return dispatcher
