"""PostgreSQL Source leases; caller UoW owns all transaction commits."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
import re
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.artifacts import ArtifactOwnerType, ArtifactStatus
from app.domain.enums import ResumeStatus
from app.models.artifacts import Artifact
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.processing.outcomes import ClaimedLease, ClaimDisposition, ClaimResult, LeaseOwnershipLost, ProcessDisposition
from app.processing.retry import TRANSIENT_PROCESSING_CODES

Source = Resume | KnowledgeDocument


class LeaseRepository:
    def __init__(self, session: Session):
        self._session = session

    @staticmethod
    def _identity(tenant_id: str, source_type: ArtifactOwnerType | str, source_id: str) -> ArtifactOwnerType:
        for value in (tenant_id, source_id):
            if not isinstance(value, str) or not 1 <= len(value) <= 36 or value.strip() != value:
                raise ValueError("processing_source_identity_invalid")
        return ArtifactOwnerType(source_type)

    @staticmethod
    def _owner(owner: str) -> None:
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,100}", owner):
            raise ValueError("processing_lease_owner_invalid")

    @staticmethod
    def _duration(duration: timedelta) -> None:
        if not isinstance(duration, timedelta) or not timedelta(0) < duration <= timedelta(days=1):
            raise ValueError("processing_lease_duration_invalid")

    def _validate_lease(self, lease: ClaimedLease) -> None:
        if not isinstance(lease, ClaimedLease):
            raise ValueError("processing_lease_invalid")
        self._identity(lease.tenant_id, lease.source_type, lease.source_id)
        self._identity(lease.tenant_id, lease.source_type, lease.artifact_id)
        self._owner(lease.owner)
        if type(lease.epoch) is not int or not 0 < lease.epoch <= 9223372036854775807:
            raise ValueError("processing_lease_epoch_invalid")
        if not isinstance(lease.expires_at, datetime) or lease.expires_at.utcoffset() is None:
            raise ValueError("processing_lease_clock_invalid")

    def _locked(self, tenant_id: str, source_type: ArtifactOwnerType | str, source_id: str) -> Source | None:
        model = Resume if source_type == ArtifactOwnerType.RESUME else KnowledgeDocument
        return cast(
            Source | None,
            self._session.scalar(
                select(model)
                .where(model.tenant_id == tenant_id, model.id == source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    def _available(self, row: Source, source_type: ArtifactOwnerType | str) -> bool:
        artifact = self._session.scalar(
            select(Artifact)
            .where(
                Artifact.tenant_id == row.tenant_id,
                Artifact.owner_type == source_type,
                Artifact.owner_id == row.id,
                Artifact.id == row.artifact_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return artifact is not None and artifact.status == ArtifactStatus.AVAILABLE

    def _now(self) -> datetime:
        # PostgreSQL now()/CURRENT_TIMESTAMP is transaction-start time and unsafe
        # after waiting for a row lock. Observe wall clock after ALL lock waits.
        return cast(datetime, self._session.scalar(select(func.clock_timestamp())))

    @staticmethod
    def _running(row: Source) -> bool:
        return row.status == (ResumeStatus.RUNNING if isinstance(row, Resume) else "processing")

    def claim(
        self,
        tenant_id: str,
        source_type: ArtifactOwnerType | str,
        source_id: str,
        owner: str,
        *,
        duration: timedelta,
        max_attempts: int = 5,
    ) -> ClaimResult:
        source_type = self._identity(tenant_id, source_type, source_id)
        self._owner(owner)
        self._duration(duration)
        self._budget(max_attempts)
        with self._session.no_autoflush:
            row = self._locked(tenant_id, source_type, source_id)
            if row is None or row.lifecycle_status != "active":
                return ClaimResult(ClaimDisposition.TERMINAL)
            queued = row.status == (ResumeStatus.QUEUED if isinstance(row, Resume) else "uploaded")
            if not queued and not self._running(row):
                return ClaimResult(ClaimDisposition.TERMINAL)
            # A live duplicate is a pure no-op, even when its Artifact has changed.
            if (
                self._running(row)
                and row.processing_lease_expires_at is not None
                and row.processing_lease_expires_at > self._now()
            ):
                return ClaimResult(ClaimDisposition.DUPLICATE_ACTIVE)
            available = self._available(row, source_type)
            now = self._now()
            if not available or (row.next_retry_at is not None and row.next_retry_at > now):
                return ClaimResult(ClaimDisposition.DEFERRED)
            if row.processing_attempts >= max_attempts:
                self._failed_row(row, "processing_attempts_exhausted")
                self._session.flush([row])
                return ClaimResult(ClaimDisposition.TERMINAL)
            row.status = ResumeStatus.RUNNING if isinstance(row, Resume) else "processing"
            row.processing_attempts += 1
            row.processing_lease_epoch += 1
            row.processing_lease_owner = owner
            row.processing_lease_expires_at = now + duration
            row.next_retry_at = None
            row.error_code = None
            row.error_message = None
            assert row.artifact_id is not None  # Validated exact AVAILABLE Artifact above.
            lease = ClaimedLease(
                tenant_id,
                source_type,
                source_id,
                row.artifact_id,
                owner,
                row.processing_lease_epoch,
                row.processing_lease_expires_at,
            )
        self._session.flush([row])
        return ClaimResult(ClaimDisposition.CLAIMED, lease)

    @staticmethod
    def _budget(max_attempts: int) -> None:
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("processing_attempt_budget_invalid")

    @staticmethod
    def _failed_row(row: Source, code: str) -> None:
        row.status = ResumeStatus.FAILED if isinstance(row, Resume) else "failed"
        row.error_code, row.error_message, row.next_retry_at = code, None, None
        row.processing_lease_owner = row.processing_lease_expires_at = None

    def schedule_retry(
        self,
        lease: ClaimedLease,
        error_code: str,
        *,
        short_delay: timedelta,
        long_delay: timedelta,
        max_attempts: int,
    ) -> ProcessDisposition:
        """Persist failure and its next eligibility under the current lease; caller commits."""
        self._validate_lease(lease)
        self._budget(max_attempts)
        if (
            not isinstance(short_delay, timedelta)
            or not isinstance(long_delay, timedelta)
            or not timedelta(0) < short_delay < long_delay
        ):
            raise ValueError("processing_retry_delay_invalid")
        if error_code not in TRANSIENT_PROCESSING_CODES:
            raise ValueError("processing_retry_error_invalid")
        with self._session.no_autoflush:
            owned = self._owned(lease)
            if owned is None:
                return ProcessDisposition.LEASE_LOST
            row, now = owned
            self._failed_row(row, error_code)
            outcome = ProcessDisposition.COMPLETED
            if row.processing_attempts < max_attempts:
                short = row.processing_attempts <= 2
                row.next_retry_at = now + (short_delay if short else long_delay)
                if short:
                    row.status = ResumeStatus.QUEUED if isinstance(row, Resume) else "uploaded"
                    row.queued_at = now
                    outcome = ProcessDisposition.RETRY_SHORT
        self._session.flush([row])
        return outcome

    def requeue_due(
        self,
        tenant_id: str,
        source_type: ArtifactOwnerType | str,
        source_id: str,
        *,
        expected_epoch: int,
        expected_artifact_id: str,
        max_attempts: int,
    ) -> bool:
        """Consume a due FAILED scan snapshot once. Commit before ordinary dispatch."""
        source_type = self._identity(tenant_id, source_type, source_id)
        self._identity(tenant_id, source_type, expected_artifact_id)
        self._budget(max_attempts)
        if type(expected_epoch) is not int or not 0 < expected_epoch <= 9223372036854775807:
            raise ValueError("processing_lease_epoch_invalid")
        with self._session.no_autoflush:
            row = self._locked(tenant_id, source_type, source_id)
            if (
                row is None
                or row.lifecycle_status != "active"
                or row.status != (ResumeStatus.FAILED if isinstance(row, Resume) else "failed")
                or row.processing_lease_epoch != expected_epoch
                or row.artifact_id != expected_artifact_id
                or row.error_code not in TRANSIENT_PROCESSING_CODES
                or row.processing_attempts >= max_attempts
                or row.next_retry_at is None
            ):
                return False
            if not self._available(row, source_type):
                return False
            now = self._now()
            if row.next_retry_at > now:
                return False
            row.status = ResumeStatus.QUEUED if isinstance(row, Resume) else "uploaded"
            row.queued_at = now
            row.next_retry_at = None
            row.processing_lease_owner = row.processing_lease_expires_at = None
        self._session.flush([row])
        return True

    def _owned(self, lease: ClaimedLease) -> tuple[Source, datetime] | None:
        row = self._locked(lease.tenant_id, lease.source_type, lease.source_id)
        if (
            row is None
            or row.lifecycle_status != "active"
            or not self._running(row)
            or row.processing_lease_epoch != lease.epoch
            or row.processing_lease_owner != lease.owner
            or row.artifact_id != lease.artifact_id
        ):
            return None
        if not self._available(row, lease.source_type):
            return None
        now = self._now()
        if row.processing_lease_expires_at is None or row.processing_lease_expires_at <= now:
            return None
        return row, now

    def renew(self, lease: ClaimedLease, *, duration: timedelta) -> bool:
        self._validate_lease(lease)
        self._duration(duration)
        with self._session.no_autoflush:
            owned = self._owned(lease)
            if owned is None:
                return False
            row, now = owned
            row.processing_lease_expires_at = now + duration
        self._session.flush([row])
        return True

    def _terminal(self, lease: ClaimedLease, error_code: str | None, next_retry_at: datetime | None) -> bool:
        self._validate_lease(lease)
        with self._session.no_autoflush:
            owned = self._owned(lease)
            if owned is None:
                return False
            row, _ = owned
            if isinstance(row, Resume):
                row.status = ResumeStatus.SUCCEEDED if error_code is None else ResumeStatus.FAILED
            else:
                row.status = "ready" if error_code is None else "failed"
            row.error_code = error_code
            row.error_message = None
            row.next_retry_at = next_retry_at
            row.processing_lease_owner = None
            row.processing_lease_expires_at = None
        self._session.flush([row])
        return True

    def finalize(self, lease: ClaimedLease) -> bool:
        return self._terminal(lease, None, None)

    def fail(self, lease: ClaimedLease, error_code: str, *, next_retry_at: datetime | None = None) -> bool:
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error_code):
            raise ValueError("processing_error_code_invalid")
        if next_retry_at is not None and (not isinstance(next_retry_at, datetime) or next_retry_at.utcoffset() is None):
            raise ValueError("processing_retry_clock_invalid")
        return self._terminal(lease, error_code, next_retry_at)

    @contextmanager
    def owned(self, lease: ClaimedLease) -> Iterator[None]:
        """Guard nonterminal generation writes; caller commits after context exit."""
        self._validate_lease(lease)
        if self._session.new or self._session.dirty or self._session.deleted:
            raise ValueError("processing_publication_requires_clean_session")
        with self._session.no_autoflush:
            if self._owned(lease) is None:
                raise LeaseOwnershipLost("processing_lease_lost")
        with self._session.begin_nested():
            self._session.info["processing_lease_guard"] = lease
            try:
                yield
                self._session.flush()
                if self._owned(lease) is None:
                    raise LeaseOwnershipLost("processing_lease_lost")
            finally:
                self._session.info.pop("processing_lease_guard", None)

    @contextmanager
    def finalize_owned(self, lease: ClaimedLease) -> Iterator[None]:
        """Write derived data inside; commit the caller UoW only after exit.

        The savepoint rolls back this publication on failure. Source and Artifact
        locks remain held until the outer transaction ends. Never commit inside.
        """
        self._validate_lease(lease)
        # begin_nested() flushes even under no_autoflush. Enforce guard-first
        # publication so stale/pending writes cannot escape its savepoint.
        if self._session.new or self._session.dirty or self._session.deleted:
            raise ValueError("processing_publication_requires_clean_session")
        with self._session.no_autoflush:
            if self._owned(lease) is None:
                raise LeaseOwnershipLost("processing_lease_lost")
        with self._session.begin_nested():
            self._session.info["processing_lease_guard"] = lease
            try:
                yield
                self._session.flush()
                if not self.finalize(lease):
                    raise LeaseOwnershipLost("processing_lease_lost")
            finally:
                self._session.info.pop("processing_lease_guard", None)
