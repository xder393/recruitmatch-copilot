"""Immutable, infrastructure-independent processing outcomes."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.domain.artifacts import ArtifactOwnerType


class ClaimDisposition(str, Enum):
    CLAIMED = "claimed"
    DUPLICATE_ACTIVE = "duplicate_active"
    TERMINAL = "terminal"
    DEFERRED = "deferred"


class ProcessDisposition(str, Enum):
    COMPLETED = "completed"
    DUPLICATE_ACTIVE = "duplicate_active"
    TERMINAL = "terminal"
    RETRY_SHORT = "retry_short"
    DEFERRED = "deferred"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class ClaimedLease:
    tenant_id: str
    source_type: ArtifactOwnerType
    source_id: str
    artifact_id: str
    owner: str
    epoch: int
    expires_at: datetime


@dataclass(frozen=True)
class ClaimResult:
    disposition: ClaimDisposition
    lease: ClaimedLease | None = None


class LeaseOwnershipLost(Exception):
    """The publication transaction no longer owns a live Source lease."""
