"""Explicit SQLite lease/publication fakes; no PostgreSQL ownership SQL."""

from contextlib import contextmanager
from datetime import datetime, timezone

from app.domain.artifacts import ArtifactOwnerType, ArtifactStatus
from app.domain.enums import ResumeStatus
from app.models.resumes import Resume
from app.models.knowledge import KnowledgeDocument
from app.models.artifacts import Artifact
from app.processing.outcomes import ClaimedLease, ClaimDisposition, ClaimResult, LeaseOwnershipLost
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class FakeLeaseRepository:
    def __init__(self, session):
        self.session = session

    def _source(self, tenant_id, source_type, source_id):
        row = self.session.get(Resume if source_type == "resume" else KnowledgeDocument, source_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    def _available(self, row, source_type):
        artifact = self.session.get(Artifact, row.artifact_id) if row.artifact_id else None
        return artifact is not None and (
            artifact.tenant_id,
            artifact.owner_type,
            artifact.owner_id,
            artifact.status,
        ) == (row.tenant_id, source_type, row.id, ArtifactStatus.AVAILABLE)

    @staticmethod
    def _running(row):
        return row.status == (ResumeStatus.RUNNING if isinstance(row, Resume) else "processing")

    def claim(self, tenant_id, source_type, source_id, owner, *, duration, max_attempts=5):
        row = self._source(tenant_id, source_type, source_id)
        if row is None or row.lifecycle_status != "active":
            return ClaimResult(ClaimDisposition.TERMINAL)
        if row.status != (ResumeStatus.QUEUED if isinstance(row, Resume) else "uploaded") and not self._running(row):
            return ClaimResult(ClaimDisposition.TERMINAL)
        now = datetime.now(timezone.utc)
        if self._running(row) and row.processing_lease_expires_at and aware(row.processing_lease_expires_at) > now:
            return ClaimResult(ClaimDisposition.DUPLICATE_ACTIVE)
        if not self._available(row, source_type) or (row.next_retry_at and aware(row.next_retry_at) > now):
            return ClaimResult(ClaimDisposition.DEFERRED)
        if row.processing_attempts >= max_attempts:
            row.status = ResumeStatus.FAILED if isinstance(row, Resume) else "failed"
            row.error_code = "processing_attempts_exhausted"
            row.processing_lease_owner = row.processing_lease_expires_at = row.next_retry_at = None
            self.session.flush()
            return ClaimResult(ClaimDisposition.TERMINAL)
        row.status = ResumeStatus.RUNNING if isinstance(row, Resume) else "processing"
        row.processing_attempts += 1
        row.processing_lease_epoch += 1
        row.processing_lease_owner = owner
        row.processing_lease_expires_at = now + duration
        row.next_retry_at = row.error_code = row.error_message = None
        self.session.flush()
        return ClaimResult(
            ClaimDisposition.CLAIMED,
            ClaimedLease(
                tenant_id,
                ArtifactOwnerType(source_type),
                source_id,
                row.artifact_id,
                owner,
                row.processing_lease_epoch,
                row.processing_lease_expires_at,
            ),
        )

    def _owned(self, lease):
        row = self._source(lease.tenant_id, lease.source_type, lease.source_id)
        if row is not None:
            self.session.refresh(row)
        if row is None or row.lifecycle_status != "active" or not self._running(row):
            return None
        if (row.processing_lease_epoch, row.processing_lease_owner, row.artifact_id) != (
            lease.epoch,
            lease.owner,
            lease.artifact_id,
        ):
            return None
        if not self._available(row, lease.source_type) or not row.processing_lease_expires_at:
            return None
        return row if aware(row.processing_lease_expires_at) > datetime.now(timezone.utc) else None

    def renew(self, lease, *, duration):
        row = self._owned(lease)
        if row is None:
            return False
        row.processing_lease_expires_at = datetime.now(timezone.utc) + duration
        self.session.flush()
        return True

    def _terminal(self, lease, code, retry=None):
        row = self._owned(lease)
        if row is None:
            return False
        row.status = (
            (ResumeStatus.FAILED if code else ResumeStatus.SUCCEEDED)
            if isinstance(row, Resume)
            else ("failed" if code else "ready")
        )
        row.error_code, row.error_message, row.next_retry_at = code, None, retry
        row.processing_lease_owner = row.processing_lease_expires_at = None
        self.session.flush()
        return True

    def finalize(self, lease):
        return self._terminal(lease, None)

    def fail(self, lease, error_code, *, next_retry_at=None):
        return self._terminal(lease, error_code, next_retry_at)

    def schedule_retry(self, lease, error_code, *, short_delay, long_delay, max_attempts):
        from app.processing.outcomes import ProcessDisposition

        row = self._owned(lease)
        if row is None:
            return ProcessDisposition.LEASE_LOST
        attempts = row.processing_attempts
        now = datetime.now(timezone.utc)
        retry = now + (short_delay if attempts <= 2 else long_delay) if attempts < max_attempts else None
        self._terminal(lease, error_code, retry)
        if retry is not None and attempts <= 2:
            row.status = ResumeStatus.QUEUED if isinstance(row, Resume) else "uploaded"
            row.queued_at = now
            self.session.flush()
            return ProcessDisposition.RETRY_SHORT
        return ProcessDisposition.COMPLETED

    @contextmanager
    def _guard(self, lease, terminal):
        if self._owned(lease) is None:
            raise LeaseOwnershipLost()
        with self.session.begin_nested():
            self.session.info["processing_lease_guard"] = lease
            try:
                yield
                self.session.flush()
                if not (self.finalize(lease) if terminal else self._owned(lease)):
                    raise LeaseOwnershipLost()
            finally:
                self.session.info.pop("processing_lease_guard", None)

    def owned(self, lease):
        return self._guard(lease, False)

    def finalize_owned(self, lease):
        return self._guard(lease, True)


class FakeProcessingUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(self, session, writer=None):
        super().__init__(session, owns_session=True)
        self.leases = FakeLeaseRepository(session)
        self._publications = []
        if writer is not None:
            self.generations = writer.bind(session, self._publications)

    def commit(self):
        super().commit()
        for publication in self._publications:
            publication()
        self._publications.clear()

    def rollback(self):
        self._publications.clear()
        super().rollback()


class FakeProcessingUnitOfWorkFactory:
    def __init__(self, session_factory, writer=None):
        self.session_factory = session_factory
        self.writer = writer

    def __call__(self):
        return FakeProcessingUnitOfWork(self.session_factory(), self.writer)
