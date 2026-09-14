"""PostgreSQL-only scan and guarded dispatch reservations; never claims a lease."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select

from app.domain.artifacts import ArtifactStatus
from app.domain.enums import ResumeStatus
from app.models.artifacts import Artifact
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from app.processing.retry import TRANSIENT_PROCESSING_CODES


@dataclass(frozen=True)
class RecoveryCandidate:
    tenant_id: str
    source_type: str
    source_id: str
    artifact_id: str
    epoch: int
    status: str
    queued_at: datetime
    token: str | None


class RecoveryRepository:
    def __init__(
        self,
        session_factory,
        *,
        batch_size: int = 50,
        stale_seconds: int = 60,
        pacing_seconds: int = 60,
        max_attempts: int = 5,
    ):
        if (
            any(
                type(value) is not int or value <= 0
                for value in (batch_size, stale_seconds, pacing_seconds, max_attempts)
            )
            or batch_size > 1000
        ):
            raise ValueError("recovery_policy_invalid")
        self.session_factory = session_factory
        self.batch_size = batch_size
        self.stale = timedelta(seconds=stale_seconds)
        self.pacing = timedelta(seconds=pacing_seconds)
        self.max_attempts = max_attempts

    @staticmethod
    def _states(model):
        return (
            (ResumeStatus.QUEUED, ResumeStatus.RUNNING, ResumeStatus.FAILED)
            if model is Resume
            else ("uploaded", "processing", "failed")
        )

    @staticmethod
    def _snapshot(row, kind):
        return RecoveryCandidate(
            row.tenant_id,
            kind,
            row.id,
            row.artifact_id,
            row.processing_lease_epoch,
            row.status,
            row.queued_at,
            row.recovery_dispatch_token,
        )

    def candidates(self) -> list[RecoveryCandidate]:
        candidates: list[RecoveryCandidate] = []
        with self.session_factory() as session, SqlAlchemyUnitOfWork(session):
            now = session.scalar(select(func.clock_timestamp()))
            for model, kind in ((Resume, "resume"), (KnowledgeDocument, "knowledge_document")):
                queued, running, failed = self._states(model)
                rows = session.scalars(
                    select(model)
                    .join(
                        Artifact,
                        and_(
                            Artifact.id == model.artifact_id,
                            Artifact.tenant_id == model.tenant_id,
                            Artifact.owner_id == model.id,
                            Artifact.owner_type == kind,
                        ),
                    )
                    .where(
                        model.lifecycle_status == "active",
                        Artifact.status == ArtifactStatus.AVAILABLE,
                        or_(model.next_retry_at.is_(None), model.next_retry_at <= now),
                        or_(model.recovery_dispatch_at.is_(None), model.recovery_dispatch_at <= now - self.pacing),
                        or_(
                            and_(model.status == queued, model.queued_at <= now - self.stale),
                            and_(
                                model.status == running,
                                or_(
                                    model.processing_lease_expires_at.is_(None),
                                    model.processing_lease_expires_at <= now,
                                ),
                            ),
                            and_(
                                model.status == failed,
                                model.next_retry_at.is_not(None),
                                model.error_code.in_(TRANSIENT_PROCESSING_CODES),
                                model.processing_attempts < self.max_attempts,
                            ),
                        ),
                    )
                    .order_by(func.coalesce(model.recovery_dispatch_at, model.queued_at), model.tenant_id, model.id)
                    .limit(self.batch_size)
                )
                candidates.extend(self._snapshot(row, kind) for row in rows)
        return candidates

    @staticmethod
    def _locked(session, candidate):
        # Contention skips this candidate (including late failure recording),
        # without hiding connection/schema faults or blocking the bounded cycle.
        model = Resume if candidate.source_type == "resume" else KnowledgeDocument
        row = session.scalar(
            select(model)
            .where(model.tenant_id == candidate.tenant_id, model.id == candidate.source_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.lifecycle_status != "active"
            or row.artifact_id != candidate.artifact_id
            or row.processing_lease_epoch != candidate.epoch
            or row.status != candidate.status
            or row.queued_at != candidate.queued_at
            or row.recovery_dispatch_token != candidate.token
        ):
            return None
        artifact = session.scalar(
            select(Artifact)
            .where(
                Artifact.tenant_id == candidate.tenant_id,
                Artifact.owner_type == candidate.source_type,
                Artifact.owner_id == candidate.source_id,
                Artifact.id == candidate.artifact_id,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return row if artifact is not None and artifact.status == ArtifactStatus.AVAILABLE else None

    def reserve(self, candidate: RecoveryCandidate) -> RecoveryCandidate | None:
        with self.session_factory() as session, SqlAlchemyUnitOfWork(session) as uow:
            row = self._locked(session, candidate)
            if row is None:
                return None
            now = session.scalar(select(func.clock_timestamp()))
            queued, running, failed = self._states(type(row))
            if (row.next_retry_at is not None and row.next_retry_at > now) or (
                row.recovery_dispatch_at is not None and row.recovery_dispatch_at > now - self.pacing
            ):
                return None
            if row.status == failed:
                if not uow.leases.requeue_due(
                    candidate.tenant_id,
                    candidate.source_type,
                    candidate.source_id,
                    expected_epoch=candidate.epoch,
                    expected_artifact_id=candidate.artifact_id,
                    max_attempts=self.max_attempts,
                ):
                    return None
            elif row.status == running:
                if row.processing_lease_expires_at is not None and row.processing_lease_expires_at > now:
                    return None
            elif row.status != queued or row.queued_at > now - self.stale:
                return None
            if row.processing_attempts >= self.max_attempts:
                row.status = failed
                row.error_code, row.error_message = "processing_attempts_exhausted", None
                row.next_retry_at = row.processing_lease_owner = row.processing_lease_expires_at = None
                uow.commit()
                return None
            if row.status == running:
                row.status, row.queued_at = queued, now
                row.processing_lease_owner = row.processing_lease_expires_at = None
            row.recovery_dispatch_at, row.recovery_dispatch_token = now, str(uuid4())
            row.recovery_dispatch_error_code = None
            reservation = self._snapshot(row, candidate.source_type)
            uow.commit()
            return reservation

    def dispatch_failed(self, reservation: RecoveryCandidate) -> None:
        with self.session_factory() as session, SqlAlchemyUnitOfWork(session) as uow:
            row = self._locked(session, reservation)
            if row is not None and row.status == self._states(type(row))[0]:
                row.recovery_dispatch_error_code = "processing_dispatch_unavailable"
                uow.commit()
