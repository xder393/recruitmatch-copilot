"""Celery worker entry point for resume processing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from celery import Celery  # type: ignore[import-untyped]

from app.config import Settings
from app.database import create_engine_and_session
from app.resumes.artifacts import LocalArtifactStore
from app.resumes.parser import HeuristicResumeParser
from app.services.resume_processing import ResumeProcessingService
from app.ai.gateway import OpenAICompatibleGateway
from app.ai.resume_parser import LLMResumeParser
from app.knowledge.artifacts import KnowledgeArtifactStore
from app.knowledge.embeddings import BGEEmbedder
from app.services.knowledge_processing import KnowledgeProcessingService
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.retrieval.generations import GenerationWriter
from app.retrieval.indexing import EmbeddingAdapter, SourceIndexer
from app.retrieval.pgvector_index import PgVectorRecruitingIndex


@dataclass(frozen=True)
class WorkerDependencies:
    resume_processor: ResumeProcessingService
    knowledge_processor: KnowledgeProcessingService
    retrieval_index: PgVectorRecruitingIndex
    source_indexer: SourceIndexer


def build_worker_dependencies(
    settings: Settings,
    embedder: EmbeddingAdapter | None = None,
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
    return WorkerDependencies(
        resume_processor=ResumeProcessingService(
            uow_factory,
            LocalArtifactStore(Path(settings.artifact_dir)),
            parser,
            source_indexer=source_indexer,
        ),
        knowledge_processor=KnowledgeProcessingService(
            uow_factory,
            KnowledgeArtifactStore(Path(settings.knowledge_artifact_dir)),
            source_indexer,
        ),
        retrieval_index=retrieval_index,
        source_indexer=source_indexer,
    )


celery_app = Celery("recruitmatch", broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(bind=True, name="recruitmatch.process_resume", max_retries=10)
def process_resume_task(self, tenant_id: str, resume_id: str) -> None:
    settings = Settings.load()
    dependencies = build_worker_dependencies(settings)
    if not dependencies.resume_processor.process(tenant_id, resume_id):
        raise self.retry(countdown=60)


@celery_app.task(bind=True, name="recruitmatch.process_knowledge", max_retries=10)
def process_knowledge_task(self, tenant_id: str, document_id: str) -> None:
    settings = Settings.load()
    dependencies = build_worker_dependencies(settings)
    if not dependencies.knowledge_processor.process(tenant_id, document_id):
        raise self.retry(countdown=60)
