"""Focused persistence ports consumed by recruiting use cases.

The protocols deliberately describe business-specific queries.  They do not
offer a generic CRUD surface and do not expose an ORM session.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, List, Protocol

from app.ai.contracts import ModelRequest, ModelResponse
from app.artifacts.ports import ArtifactLocation
from app.domain.artifacts import ArtifactErrorCode, ArtifactOwnerType
from app.processing.outcomes import ClaimedLease, ClaimResult, ProcessDisposition


class LeaseRepository(Protocol):
    def claim(
        self,
        tenant_id: str,
        source_type: ArtifactOwnerType | str,
        source_id: str,
        owner: str,
        *,
        duration: timedelta,
        max_attempts: int = 5,
    ) -> ClaimResult: ...
    def schedule_retry(
        self,
        lease: ClaimedLease,
        error_code: str,
        *,
        short_delay: timedelta,
        long_delay: timedelta,
        max_attempts: int,
    ) -> ProcessDisposition: ...
    def requeue_due(
        self,
        tenant_id: str,
        source_type: ArtifactOwnerType | str,
        source_id: str,
        *,
        expected_epoch: int,
        expected_artifact_id: str,
        max_attempts: int,
    ) -> bool: ...
    def renew(self, lease: ClaimedLease, *, duration: timedelta) -> bool: ...
    def finalize(self, lease: ClaimedLease) -> bool: ...
    def fail(self, lease: ClaimedLease, error_code: str, *, next_retry_at: datetime | None = None) -> bool: ...
    def owned(self, lease: ClaimedLease) -> AbstractContextManager[None]: ...
    def finalize_owned(self, lease: ClaimedLease) -> AbstractContextManager[None]:
        """Guard derived writes and success; commit UoW only AFTER successful exit.

        Raises LeaseOwnershipLost on an entry/exit fence failure, rolling back
        writes made inside the context. Never commits the caller's transaction.
        """
        ...


class RepositoryConflictError(Exception):
    """A persistence uniqueness conflict safe for application handling."""


class PersistenceUnavailable(Exception):
    """A recoverable database connection/transaction failure; original cause retained."""


@dataclass(frozen=True)
class ArtifactTombstone:
    location: ArtifactLocation
    error_code: ArtifactErrorCode | None


@dataclass(frozen=True)
class PendingArtifact:
    location: ArtifactLocation
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactReconciliationBatch:
    pending: tuple[PendingArtifact, ...] = ()
    cleanup: tuple[ArtifactLocation, ...] = ()
    deleted: tuple[ArtifactTombstone, ...] = ()


class ArtifactRepository(Protocol):
    def reserve_reconciliation_batch(self, stale_before: datetime, *, limit: int) -> ArtifactReconciliationBatch: ...
    def count_associated_locations(self, locations: tuple[ArtifactLocation, ...]) -> int: ...
    def get(
        self,
        tenant_id: str,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        artifact_id: str,
        *,
        for_update: bool = False,
    ) -> Any | None: ...
    def list_deleted_tombstones(
        self, tenant_id: str, *, after_id: str | None = None, limit: int = 100
    ) -> list[ArtifactTombstone]: ...
    def record_deleted_cleanup_result(
        self,
        tenant_id: str,
        artifact_id: str,
        error_code: ArtifactErrorCode | None,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Any: ...
    def claim_upload(
        self,
        tenant_id: str,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        sha256: str,
        media_type: str,
        size_bytes: int,
    ) -> Any: ...
    def mark_available(
        self, tenant_id: str, artifact_id: str, *, owner_type: ArtifactOwnerType | str, owner_id: str
    ) -> Any: ...
    def mark_failed(
        self,
        tenant_id: str,
        artifact_id: str,
        error_code: ArtifactErrorCode,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Any: ...
    def mark_cleanup_pending(
        self, tenant_id: str, artifact_id: str, *, owner_type: ArtifactOwnerType | str, owner_id: str
    ) -> Any: ...
    def mark_cleanup_failed(
        self,
        tenant_id: str,
        artifact_id: str,
        error_code: ArtifactErrorCode,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Any: ...
    def mark_deleted(
        self, tenant_id: str, artifact_id: str, *, owner_type: ArtifactOwnerType | str, owner_id: str
    ) -> Any: ...
    def resolve_location(
        self,
        tenant_id: str,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        artifact_id: str,
    ) -> ArtifactLocation: ...


class IdentityRepository(Protocol):
    def lock_privacy_guard(self, tenant_id: str) -> None: ...
    def find_user_by_email(self, email: str) -> Any | None: ...
    def get_user(self, user_id: str, tenant_id: str) -> Any | None: ...
    def get_tenant(self, tenant_id: str) -> Any | None: ...
    def add_tenant_admin(self, tenant_name: str, email: str, password_hash: str) -> Any: ...


class FeedbackRepository(Protocol):
    def get_result(self, tenant_id: str, result_id: str) -> Any | None: ...
    def get_job_version(self, tenant_id: str, version_id: str) -> Any | None: ...
    def add(self, feedback: Any) -> None: ...


class JobRepository(Protocol):
    def list_templates(self) -> list[Any]: ...
    def get(self, tenant_id: str, job_id: str) -> Any | None: ...
    def list(self, tenant_id: str) -> list[Any]: ...
    def add(self, job: Any) -> None: ...
    def list_versions_for_tenant(self, tenant_id: str) -> List[Any]: ...
    def reload(self, tenant_id: str, job_id: str) -> Any | None: ...


class KnowledgeRepository(Protocol):
    def get_document(
        self, tenant_id: str, document_id: str, *, for_update: bool = False, include_deleted: bool = False
    ) -> Any | None: ...
    def scrub_private_data(self, tenant_id: str, document: Any, deleted_at: datetime) -> None: ...
    def by_checksum(self, tenant_id: str, checksum: str, document_type: str) -> Any | None: ...
    def list_documents(self, tenant_id: str) -> list[Any]: ...
    def deactivate(self, document: Any) -> None: ...
    def add(self, document: Any) -> None: ...
    def reload_document(self, tenant_id: str, document_id: str) -> Any | None: ...


class MatchingRepository(Protocol):
    def scrub_private_results(self, tenant_id: str, *, resume_id: str | None = None) -> None: ...
    def get_succeeded_resume(self, tenant_id: str, resume_id: str) -> Any | None: ...
    def active_jobs(self, tenant_id: str) -> list[Any]: ...
    def add_run(self, run: Any) -> None: ...
    def get_run(self, tenant_id: str, run_id: str) -> Any | None: ...
    def latest_for_resume(self, tenant_id: str, resume_id: str) -> Any | None: ...


class ResumeRepository(Protocol):
    def get(
        self,
        tenant_id: str,
        resume_id: str,
        include_deleted: bool = False,
        *,
        for_update: bool = False,
    ) -> Any | None: ...
    def find_by_hash(self, tenant_id: str, sha256: str) -> Any | None: ...
    def list(self, tenant_id: str) -> list[Any]: ...
    def list_succeeded(self, tenant_id: str) -> List[Any]: ...
    def add(self, resume: Any) -> None: ...
    def reload(self, tenant_id: str, resume_id: str) -> Any | None: ...
    def scrub_private_data(self, tenant_id: str, resume: Any, deleted_at: datetime) -> None: ...


class ModelTraceRepository(Protocol):
    def succeeded(
        self,
        tenant_id: str,
        business_type: str,
        business_id: str,
        source_ids: list[str],
        request: ModelRequest,
        response: ModelResponse,
        fallback_reason: str | None = None,
    ) -> Any: ...

    def failed(
        self,
        tenant_id: str,
        business_type: str,
        business_id: str,
        source_ids: list[str],
        request: ModelRequest,
        error: Any,
        latency_ms: float,
    ) -> Any: ...
