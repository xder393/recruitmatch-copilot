"""Celery worker entry point for resume processing."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from celery import Celery  # type: ignore[import-untyped]
from celery.exceptions import Reject  # type: ignore[import-untyped]
from billiard.exceptions import SoftTimeLimitExceeded  # type: ignore[import-untyped]

from app.config import Settings
from app.database import create_engine_and_session
from app.artifacts.ports import ArtifactStore
from app.artifacts.s3 import S3ArtifactStore, S3Settings
from app.resumes.parser import HeuristicResumeParser
from app.services.resume_processing import ResumeProcessingService
from app.ai.gateway import OpenAICompatibleGateway
from app.ai.resume_parser import LLMResumeParser
from app.knowledge.embeddings import BGEEmbedder
from app.services.knowledge_processing import KnowledgeProcessingService
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.retrieval.generations import GenerationWriter
from app.retrieval.indexing import EmbeddingAdapter, SourceIndexer
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from app.processing.outcomes import ProcessDisposition
from app.processing.retry import RetryPolicy
from app.repositories.ports import PersistenceUnavailable
from app.tasks.beat import WorkerHeartbeatStep

logger = logging.getLogger(__name__)


class ProcessingTaskFailed(Exception):
    """A task fault safe for the Celery failure log; business recovery stays in PostgreSQL."""


@dataclass(frozen=True)
class WorkerDependencies:
    resume_processor: ResumeProcessingService
    knowledge_processor: KnowledgeProcessingService
    retrieval_index: PgVectorRecruitingIndex
    source_indexer: SourceIndexer


def build_worker_dependencies(
    settings: Settings,
    embedder: EmbeddingAdapter | None = None,
    artifact_store: ArtifactStore | None = None,
) -> WorkerDependencies:
    _, session_factory = create_engine_and_session(settings.database_url)
    embedding_adapter = embedder or BGEEmbedder(settings.embedding_model)
    retrieval_index = PgVectorRecruitingIndex(session_factory)
    source_indexer = SourceIndexer(GenerationWriter(session_factory), embedding_adapter)
    fallback_parser = HeuristicResumeParser()
    parser = (
        LLMResumeParser(
            OpenAICompatibleGateway(settings),
            fallback_parser,
            prompt_version=settings.resume_prompt_version,
            max_evidence_characters=settings.max_evidence_characters,
        )
        if settings.ai_enabled
        else fallback_parser
    )
    uow_factory = SqlAlchemyUnitOfWorkFactory(session_factory)
    retry_policy = RetryPolicy(
        settings.processing_max_attempts, settings.retry_short_seconds, settings.retry_long_seconds
    )
    artifact_store = artifact_store if artifact_store is not None else S3ArtifactStore(S3Settings.from_env().client())
    return WorkerDependencies(
        resume_processor=ResumeProcessingService(
            uow_factory,
            artifact_store,
            parser,
            source_indexer=source_indexer,
            lease_seconds=settings.processing_lease_seconds,
            retry_policy=retry_policy,
            timeout_errors=(TimeoutError, SoftTimeLimitExceeded),
        ),
        knowledge_processor=KnowledgeProcessingService(
            uow_factory,
            artifact_store,
            source_indexer,
            lease_seconds=settings.processing_lease_seconds,
            retry_policy=retry_policy,
            timeout_errors=(TimeoutError, SoftTimeLimitExceeded),
        ),
        retrieval_index=retrieval_index,
        source_indexer=source_indexer,
    )


settings = Settings.load()
celery_app = Celery("recruitmatch", broker=settings.celery_broker_url, backend=None)
celery_app.steps["worker"].add(WorkerHeartbeatStep)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    result_backend=None,
    task_soft_time_limit=settings.task_soft_time_limit,
    task_time_limit=settings.task_hard_time_limit,
    broker_transport_options={
        "visibility_timeout": settings.redis_visibility_timeout,
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "retry_on_timeout": False,
        "max_retries": 0,
    },
    broker_connection_timeout=1,
    broker_pool_limit=0,
    beat_scheduler="app.tasks.beat:RecoveryScheduler",
    visibility_timeout=settings.redis_visibility_timeout,
    task_publish_retry=False,
)


def _deliver(task, tenant_id: str, source_id: str, kind: str) -> None:
    task.request.argsrepr = "[redacted]"
    task.request.kwargsrepr = "[redacted]"
    try:
        settings = Settings.load()
        dependencies = build_worker_dependencies(settings)
        processor = dependencies.resume_processor if kind == "resume" else dependencies.knowledge_processor
        outcome = processor.process(tenant_id, source_id)
    except PersistenceUnavailable:
        logger.warning("processing_database_unavailable")
        return
    except Exception:
        # Preserve a genuine Celery FAILURE without serializing exception text or arguments.
        raise ProcessingTaskFailed("processing_task_failed") from None
    if outcome == ProcessDisposition.RETRY_SHORT:
        try:
            raise task.retry(countdown=settings.retry_short_seconds)
        except Reject:
            # Celery wraps a failed retry publish in Reject. The committed QUEUED
            # row remains eligible for bounded Beat recovery after its due time.
            logger.warning("processing_dispatch_unavailable")


@celery_app.task(bind=True, name="recruitmatch.process_resume", max_retries=None, throws=(ProcessingTaskFailed,))
def process_resume_task(self, tenant_id: str, resume_id: str) -> None:
    _deliver(self, tenant_id, resume_id, "resume")


@celery_app.task(bind=True, name="recruitmatch.process_knowledge", max_retries=None, throws=(ProcessingTaskFailed,))
def process_knowledge_task(self, tenant_id: str, document_id: str) -> None:
    _deliver(self, tenant_id, document_id, "knowledge_document")


@celery_app.task(name="recruitmatch.reconcile_artifacts", throws=(ProcessingTaskFailed,))
def reconcile_artifacts_task() -> None:
    from app.tasks.maintenance import run_artifact_maintenance

    try:
        run_artifact_maintenance()
    except PersistenceUnavailable:
        logger.warning("maintenance_database_unavailable")
    except Exception:
        raise ProcessingTaskFailed("maintenance_task_failed") from None
