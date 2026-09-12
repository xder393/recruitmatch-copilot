"""SQLAlchemy adapter composition for recruiting repository ports."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable

from sqlalchemy.exc import IntegrityError
from app.processing.leases import LeaseRepository
from app.retrieval.generations import TransactionGenerationWriter

from app.repositories.feedback import FeedbackRepository
from app.repositories.artifacts import ArtifactRepository
from app.repositories.identity import IdentityRepository
from app.repositories.jobs import JobRepository
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.matching import MatchingRepository
from app.repositories.model_traces import ModelTraceWriter
from app.repositories.ports import RepositoryConflictError
from app.repositories.resumes import ResumeRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork


class SqlAlchemyUnitOfWork(AbstractContextManager["SqlAlchemyUnitOfWork"], RecruitingUnitOfWork):
    """Owns SQLAlchemy repository adapters for one transaction."""

    def __init__(self, session, *, owns_session: bool = False):
        self._session = session
        self._owns_session = owns_session
        self.identities = IdentityRepository(session)
        self.artifacts = ArtifactRepository(session)
        self.feedback = FeedbackRepository(session)
        self.jobs = JobRepository(session)
        self.knowledge = KnowledgeRepository(session)
        self.leases = LeaseRepository(session)
        self.generations = TransactionGenerationWriter(session)
        self.matching = MatchingRepository(session)
        self.model_traces = ModelTraceWriter(session)
        self.resumes = ResumeRepository(session)

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is not None:
            self.rollback()
        if self._owns_session:
            self._session.close()

    def commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            raise RepositoryConflictError from exc

    def rollback(self) -> None:
        self._session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory

    def __call__(self) -> AbstractContextManager[RecruitingUnitOfWork]:
        return SqlAlchemyUnitOfWork(self._session_factory(), owns_session=True)
