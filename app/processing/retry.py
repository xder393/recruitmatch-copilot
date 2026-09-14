"""Bounded retry policy shared by workers and PostgreSQL recovery transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from app.processing.outcomes import ClaimedLease, ProcessDisposition
from app.observability.events import record

if TYPE_CHECKING:
    from app.repositories.unit_of_work import RecruitingUnitOfWork


TRANSIENT_PROCESSING_CODES = frozenset({"storage_unavailable", "embedding_failed", "processing_timeout"})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    short_seconds: int = 30
    long_seconds: int = 300

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value <= 0
                for value in (self.max_attempts, self.short_seconds, self.long_seconds)
            )
            or self.long_seconds <= self.short_seconds
        ):
            raise ValueError("processing_retry_policy_invalid")


def finish_failed_attempt(
    uow: RecruitingUnitOfWork, lease: ClaimedLease, code: str, policy: RetryPolicy
) -> ProcessDisposition:
    """Finish the caller's existing transaction, including any guarded index error.

    Never opens a UoW or separate transaction. A lost lease rolls back everything
    the caller staged; otherwise its failure/retry decision commits with it.
    """
    if code in TRANSIENT_PROCESSING_CODES:
        outcome = uow.leases.schedule_retry(
            lease,
            code,
            short_delay=timedelta(seconds=policy.short_seconds),
            long_delay=timedelta(seconds=policy.long_seconds),
            max_attempts=policy.max_attempts,
        )
    else:
        outcome = ProcessDisposition.COMPLETED if uow.leases.fail(lease, code) else ProcessDisposition.LEASE_LOST
    if outcome == ProcessDisposition.LEASE_LOST:
        uow.rollback()
        return outcome
    # Capture the decision while its guarded transaction still owns the row.
    # Retry is an overlapping subset of committed failed attempts, not delivery.
    retry_scheduled = code in TRANSIENT_PROCESSING_CODES and (
        outcome == ProcessDisposition.RETRY_SHORT or uow.leases.retry_scheduled
    )
    uow.commit()
    attributes = {"task.type": str(lease.source_type.value), "error.code": code, "outcome": "failure"}
    record("task.failed", attributes)
    if retry_scheduled:
        record("task.retry", {**attributes, "outcome": "retry"})
    return outcome
