"""Exact-location compensation shared by uploads and privacy cleanup."""

from app.artifacts.errors import storage_error_code
from app.artifacts.ports import ArtifactLocation, ArtifactStore
from app.core.exceptions import AppError
from app.domain.artifacts import ArtifactStatus, ArtifactTransitionError
from app.repositories.unit_of_work import RecruitingUnitOfWork


def cleanup_location(
    uow: RecruitingUnitOfWork, store: ArtifactStore, location: ArtifactLocation, owner_type: str
) -> None:
    artifact = uow.artifacts.get(
        location.tenant_id, owner_type, location.owner_id, location.artifact_id, for_update=True
    )
    if artifact is None or artifact.status not in {
        ArtifactStatus.CLEANUP_PENDING,
        ArtifactStatus.CLEANUP_FAILED,
        ArtifactStatus.DELETED,
    }:
        uow.rollback()
        raise ArtifactTransitionError("artifact_cleanup_requires_tombstone")
    # Cleanup states cannot return to a live state. Release the DB lock before
    # network I/O while retaining the exact associated identity for retry.
    uow.commit()
    error = None
    try:
        store.delete(location)
    except Exception as exc:
        error = storage_error_code(exc)
    artifact = uow.artifacts.get(
        location.tenant_id, owner_type, location.owner_id, location.artifact_id, for_update=True
    )
    if artifact is not None:
        scope = {"owner_type": owner_type, "owner_id": location.owner_id}
        if artifact.status == ArtifactStatus.DELETED:
            uow.artifacts.record_deleted_cleanup_result(location.tenant_id, location.artifact_id, error, **scope)
        else:
            if artifact.status == ArtifactStatus.CLEANUP_FAILED:
                uow.artifacts.mark_cleanup_pending(location.tenant_id, location.artifact_id, **scope)
            if artifact.status != ArtifactStatus.CLEANUP_PENDING:
                raise ArtifactTransitionError("artifact_cleanup_requires_tombstone")
            if error is None:
                uow.artifacts.mark_deleted(location.tenant_id, location.artifact_id, **scope)
            else:
                uow.artifacts.mark_cleanup_failed(location.tenant_id, location.artifact_id, error, **scope)
        uow.commit()
    if error is not None:
        raise AppError("原始文件清理待重试", code="artifact_cleanup_pending", status_code=503)
