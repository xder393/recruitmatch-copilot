"""Infrastructure-independent recruiting transaction contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable, Protocol

from app.repositories.ports import (
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
    identities: IdentityRepository
    feedback: FeedbackRepository
    jobs: JobRepository
    knowledge: KnowledgeRepository
    matching: MatchingRepository
    model_traces: ModelTraceRepository
    resumes: ResumeRepository


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[RecruitingUnitOfWork]: ...


def unit_of_work(value) -> RecruitingUnitOfWork:
    """Adapt legacy direct-session callers while composition roots migrate."""
    if all(hasattr(value, name) for name in ("commit", "rollback")) and not hasattr(value, "execute"):
        return value
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    return SqlAlchemyUnitOfWork(value)


def unit_of_work_factory(value) -> UnitOfWorkFactory:
    """Adapt an existing session factory at worker composition boundaries."""
    return _CompatibleUnitOfWorkFactory(value)


class _CompatibleUnitOfWorkFactory:
    """Accept a port-native fake factory or a legacy SQLAlchemy session factory."""

    def __init__(self, factory: Callable):
        self._factory = factory

    def __call__(self):
        candidate = self._factory()
        if all(hasattr(candidate, name) for name in ("commit", "rollback")) and not hasattr(candidate, "execute"):
            return candidate
        from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

        return SqlAlchemyUnitOfWork(candidate, owns_session=True)
