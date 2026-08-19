"""Celery worker entry point for resume processing."""
from __future__ import annotations

import os
from pathlib import Path

from celery import Celery

from app.config import Settings
from app.database import create_engine_and_session
from app.resumes.artifacts import LocalArtifactStore
from app.resumes.parser import HeuristicResumeParser
from app.services.resume_processing import ResumeProcessingService
from app.ai.gateway import OpenAICompatibleGateway
from app.ai.resume_parser import LLMResumeParser

celery_app = Celery("recruitmatch", broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(name="recruitmatch.process_resume")
def process_resume_task(tenant_id: str, resume_id: str) -> None:
    settings = Settings.load()
    _, session_factory = create_engine_and_session(settings.database_url)
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
    processor = ResumeProcessingService(
        session_factory,
        LocalArtifactStore(Path(settings.artifact_dir)),
        parser,
    )
    processor.process(tenant_id, resume_id)
