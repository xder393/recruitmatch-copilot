"""PostgreSQL upload arbitration and persisted, tenant-qualified transitions."""

import re

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.artifacts.ports import ArtifactLocation
from app.domain.artifacts import (
    ArtifactErrorCode,
    ArtifactNotFoundError,
    ArtifactOwnerType,
    ArtifactStatus,
    ArtifactTransitionError,
    validate_transition,
)
from app.models.artifacts import Artifact
from app.repositories.ports import ArtifactTombstone


class ArtifactRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, tenant_id, owner_type, owner_id, artifact_id, *, for_update=False):
        query = (
            select(Artifact)
            .where(
                Artifact.tenant_id == tenant_id,
                Artifact.owner_type == owner_type,
                Artifact.owner_id == owner_id,
                Artifact.id == artifact_id,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_deleted_tombstones(self, tenant_id, *, after_id=None, limit=100) -> list[ArtifactTombstone]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("artifact_page_limit_invalid")
        query = select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.status == ArtifactStatus.DELETED)
        if after_id is not None:
            query = query.where(Artifact.id > after_id)
        rows = self.session.scalars(query.order_by(Artifact.id).limit(limit).execution_options(populate_existing=True))
        return [ArtifactTombstone(self._location(row), row.error_code) for row in rows]

    def record_deleted_cleanup_result(
        self,
        tenant_id,
        artifact_id,
        error_code,
        *,
        owner_type,
        owner_id,
    ):
        with self.session.no_autoflush:
            artifact = self._locked(tenant_id, artifact_id)
        if artifact.owner_type != owner_type or artifact.owner_id != owner_id:
            raise ArtifactNotFoundError("artifact_not_found")
        if artifact.status != ArtifactStatus.DELETED:
            raise ArtifactTransitionError("artifact_transition_invalid")
        artifact.error_code = ArtifactErrorCode(error_code) if error_code is not None else None
        self.session.flush()
        return artifact

    @staticmethod
    def _location(artifact: Artifact) -> ArtifactLocation:
        namespace = "resumes" if artifact.owner_type == ArtifactOwnerType.RESUME else "knowledge"
        return ArtifactLocation(artifact.tenant_id, namespace, artifact.owner_id, artifact.id)

    def claim_upload(
        self,
        tenant_id: str,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        sha256: str,
        media_type: str,
        size_bytes: int,
    ) -> Artifact:
        owner_type = ArtifactOwnerType(owner_type)
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("artifact_checksum_invalid")
        if not 0 < size_bytes <= 10 * 1024 * 1024:
            raise ValueError("artifact_size_invalid")
        if not tenant_id or not owner_id or len(owner_id) > 36 or not media_type or len(media_type) > 200:
            raise ValueError("artifact_metadata_invalid")
        # ON CONFLICT waits for a concurrent claimant without aborting this UoW.
        # A separate SELECT obtains a fresh READ COMMITTED snapshot after that wait.
        # Lock the winner so cleanup cannot remove its active checksum before return.
        while True:
            artifact_id = self.session.scalar(
                insert(Artifact)
                .values(
                    tenant_id=tenant_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    sha256=sha256,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    status=ArtifactStatus.PENDING,
                )
                .on_conflict_do_nothing(
                    index_elements=[Artifact.tenant_id, Artifact.owner_type, Artifact.sha256],
                    index_where=text("status IN ('PENDING', 'AVAILABLE', 'FAILED')"),
                )
                .returning(Artifact.id)
            )
            if artifact_id is not None:
                return self._locked(tenant_id, artifact_id)
            artifact = self.session.scalar(
                select(Artifact)
                .where(
                    Artifact.tenant_id == tenant_id,
                    Artifact.owner_type == owner_type,
                    Artifact.sha256 == sha256,
                    Artifact.status.in_([ArtifactStatus.PENDING, ArtifactStatus.AVAILABLE, ArtifactStatus.FAILED]),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if artifact is not None:
                return artifact
            # A cleanup committed between INSERT and SELECT; claim the now-free key.

    def _locked(self, tenant_id: str, artifact_id: str) -> Artifact:
        # Do not let stale in-memory status authorize a transition. Refresh from
        # the locked database row, also when Session has expire_on_commit=False.
        artifact = self.session.scalar(
            select(Artifact)
            .where(Artifact.tenant_id == tenant_id, Artifact.id == artifact_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if artifact is None:
            raise ArtifactNotFoundError("artifact_not_found")
        return artifact

    def _transition(
        self,
        tenant_id: str,
        artifact_id: str,
        target: ArtifactStatus,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        error_code: ArtifactErrorCode | None = None,
    ) -> Artifact:
        if error_code is not None:
            error_code = ArtifactErrorCode(error_code)
        with self.session.no_autoflush:
            artifact = self._locked(tenant_id, artifact_id)
        if artifact.owner_type != owner_type or artifact.owner_id != owner_id:
            raise ArtifactNotFoundError("artifact_not_found")
        validate_transition(artifact.status, target)
        artifact.status = target
        artifact.error_code = error_code
        if target in {ArtifactStatus.CLEANUP_PENDING, ArtifactStatus.CLEANUP_FAILED, ArtifactStatus.DELETED}:
            artifact.sha256 = None
        self.session.flush()
        return artifact

    def mark_available(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Artifact:
        return self._transition(tenant_id, artifact_id, ArtifactStatus.AVAILABLE, owner_type, owner_id)

    def mark_failed(
        self,
        tenant_id: str,
        artifact_id: str,
        error_code: ArtifactErrorCode,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Artifact:
        return self._transition(
            tenant_id, artifact_id, ArtifactStatus.FAILED, owner_type, owner_id, ArtifactErrorCode(error_code)
        )

    def mark_cleanup_pending(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Artifact:
        return self._transition(tenant_id, artifact_id, ArtifactStatus.CLEANUP_PENDING, owner_type, owner_id)

    def mark_cleanup_failed(
        self,
        tenant_id: str,
        artifact_id: str,
        error_code: ArtifactErrorCode,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Artifact:
        return self._transition(
            tenant_id, artifact_id, ArtifactStatus.CLEANUP_FAILED, owner_type, owner_id, ArtifactErrorCode(error_code)
        )

    def mark_deleted(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
    ) -> Artifact:
        return self._transition(tenant_id, artifact_id, ArtifactStatus.DELETED, owner_type, owner_id)

    def resolve_location(
        self,
        tenant_id: str,
        owner_type: ArtifactOwnerType | str,
        owner_id: str,
        artifact_id: str,
    ) -> ArtifactLocation:
        artifact = self.session.scalar(
            select(Artifact)
            .where(
                Artifact.tenant_id == tenant_id,
                Artifact.owner_type == owner_type,
                Artifact.owner_id == owner_id,
                Artifact.id == artifact_id,
            )
            .execution_options(populate_existing=True)
        )
        if artifact is None:
            raise ArtifactNotFoundError("artifact_not_found")
        return self._location(artifact)
