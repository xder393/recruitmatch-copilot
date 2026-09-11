"""Bounded administrative operations; CP4 schedules these outside Beat object I/O."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.artifacts.cleanup import cleanup_location
from app.artifacts.errors import storage_error_code
from app.artifacts.ports import ArtifactListingCursor, ArtifactStore, ArtifactStoreInspector, ArtifactStoreLister
from app.core.exceptions import AppError
from app.domain.artifacts import ArtifactErrorCode, ArtifactStatus
from app.domain.enums import ResumeStatus
from app.repositories.unit_of_work import UnitOfWorkFactory
from app.tasks.dispatcher import TaskDispatcher


@dataclass
class ReconciliationReport:
    """This invocation's observations only, never global cleanup health/counts."""

    partial: bool = True
    selected: int = 0
    inspected: int = 0
    repaired: int = 0
    invalid: int = 0
    cleaned: int = 0
    cleanup_failures: int = 0
    known_terminal_failures: int = 0
    inspection_failures: int = 0
    dispatch_failures: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class OrphanObservation:
    """Page-local candidates, not global orphan counts or permission to delete."""

    observed: int = 0
    unassociated: int = 0
    unrecognized: int = 0
    partial: bool = True
    error_code: str | None = None


class ArtifactReconciliationService:
    """One tenant and at most batch_size entries per independent lane per run.

    PENDING gets a five-minute UTC grace from creation. Missing/invalid objects
    after that grace become a stable Source failure, then legal Artifact cleanup.
    Transient Head errors remain PENDING for later bounded sweeps. A delayed Put
    is covered by live compensation and permanent tombstones even after grace.
    """

    pending_grace = timedelta(minutes=5)

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        store: ArtifactStore,
        inspector: ArtifactStoreInspector,
        dispatcher: TaskDispatcher,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.uow_factory = uow_factory
        self.store = store
        self.inspector = inspector
        self.dispatcher = dispatcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self, *, batch_size: int = 100) -> ReconciliationReport:
        stale_before = self.clock() - self.pending_grace
        with self.uow_factory() as uow:
            batch = uow.artifacts.reserve_reconciliation_batch(stale_before, limit=batch_size)
            uow.commit()
        report = ReconciliationReport(selected=len(batch.pending) + len(batch.cleanup) + len(batch.deleted))
        for candidate in batch.pending:
            self._repair(candidate, stale_before, report)
        for location in batch.cleanup:
            self._cleanup(location, report)
        for tombstone in batch.deleted:
            report.known_terminal_failures += int(tombstone.error_code is not None)
            # Delete is idempotent. Always revisit exact DB-associated tombstones,
            # including ones whose previous Delete found nothing.
            self._cleanup(tombstone.location, report)
        return report

    def inspect_orphan_page(
        self, lister: ArtifactStoreLister, *, cursor: ArtifactListingCursor | None = None, limit: int = 100
    ) -> tuple[OrphanObservation, ArtifactListingCursor | None]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("artifact_page_limit_invalid")
        try:
            page = lister.list_page(cursor=cursor, limit=limit)
        except Exception as error:
            return OrphanObservation(error_code=storage_error_code(error).value), cursor
        with self.uow_factory() as uow:
            associated = uow.artifacts.count_associated_locations(page.locations)
        return OrphanObservation(
            observed=len(page.locations) + page.unrecognized,
            unassociated=len(page.locations) - associated,
            unrecognized=page.unrecognized,
        ), page.next_cursor

    @staticmethod
    def _source(uow, location):
        if location.namespace == "resumes":
            return uow.resumes.get(location.tenant_id, location.owner_id, include_deleted=True, for_update=True)
        return uow.knowledge.get_document(location.tenant_id, location.owner_id, include_deleted=True, for_update=True)

    def _repair(self, candidate, stale_before, report):
        location = candidate.location
        owner_type = "resume" if location.namespace == "resumes" else "knowledge_document"
        code = None
        report.inspected += 1
        try:
            info = self.inspector.inspect(location)
            if info.size_bytes != candidate.size_bytes:
                code = ArtifactErrorCode.SIZE_MISMATCH
            elif info.sha256 != candidate.sha256:
                code = ArtifactErrorCode.CHECKSUM_MISMATCH
        except Exception as error:
            code = storage_error_code(error)
            if code in {ArtifactErrorCode.STORAGE_UNAVAILABLE, ArtifactErrorCode.ACCESS_DENIED}:
                report.inspection_failures += 1
                return
        dispatch = False
        with self.uow_factory() as uow:
            source = self._source(uow, location)
            artifact = uow.artifacts.get(
                location.tenant_id, owner_type, location.owner_id, location.artifact_id, for_update=True
            )
            if (
                source is None
                or source.lifecycle_status != "active"
                or source.artifact_id != location.artifact_id
                or artifact is None
                or artifact.status != ArtifactStatus.PENDING
                or artifact.created_at > stale_before
                or artifact.sha256 != candidate.sha256
                or artifact.size_bytes != candidate.size_bytes
            ):
                report.skipped += 1
                uow.rollback()
                return
            scope = {"owner_type": owner_type, "owner_id": location.owner_id}
            if code is None:
                uow.artifacts.mark_available(location.tenant_id, location.artifact_id, **scope)
                if location.namespace == "resumes":
                    source.status = ResumeStatus.QUEUED
                    dispatch = True
                elif source.status != "inactive":
                    source.status = "uploaded"
                    dispatch = True
                source.error_code = None
                source.error_message = None
                report.repaired += 1
            else:
                uow.artifacts.mark_failed(location.tenant_id, location.artifact_id, code, **scope)
                uow.artifacts.mark_cleanup_pending(location.tenant_id, location.artifact_id, **scope)
                if location.namespace == "resumes":
                    source.sha256 = None
                    source.status = ResumeStatus.FAILED
                else:
                    source.checksum = None
                    if source.status != "inactive":
                        source.status = "failed"
                source.error_code = code.value
                source.error_message = "原始文件验证失败"
                report.invalid += 1
            uow.commit()
        if code is not None:
            self._cleanup(location, report)
        elif dispatch:
            try:
                if location.namespace == "resumes":
                    self.dispatcher.dispatch_resume(location.tenant_id, location.owner_id)
                else:
                    self.dispatcher.dispatch_knowledge(location.tenant_id, location.owner_id)
            except Exception:
                # AVAILABLE and queue state committed first; CP4 can redispatch.
                report.dispatch_failures += 1

    def _cleanup(self, location, report):
        owner_type = "resume" if location.namespace == "resumes" else "knowledge_document"
        with self.uow_factory() as uow:
            try:
                cleanup_location(uow, self.store, location, owner_type)
            except AppError:
                report.cleanup_failures += 1
            else:
                report.cleaned += 1
