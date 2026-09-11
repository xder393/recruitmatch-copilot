"""Infrastructure-independent recruiting transaction contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.repositories.ports import (
    ArtifactRepository,
    FeedbackRepository,
    IdentityRepository,
    JobRepository,
    KnowledgeRepository,
    MatchingRepository,
    ModelTraceRepository,
    ResumeRepository,
)


class UnitOfWork(Protocol):
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class RecruitingUnitOfWork(UnitOfWork, Protocol):
    artifacts: ArtifactRepository
    identities: IdentityRepository
    feedback: FeedbackRepository
    jobs: JobRepository
    knowledge: KnowledgeRepository
    matching: MatchingRepository
    model_traces: ModelTraceRepository
    resumes: ResumeRepository


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[RecruitingUnitOfWork]: ...
