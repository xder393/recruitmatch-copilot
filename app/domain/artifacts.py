"""Storage lifecycle vocabulary, independent of Source processing state."""

from enum import Enum


class ArtifactStatus(str, Enum):
    PENDING = "PENDING"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    DELETED = "DELETED"


class ArtifactOwnerType(str, Enum):
    RESUME = "resume"
    KNOWLEDGE_DOCUMENT = "knowledge_document"


class ArtifactErrorCode(str, Enum):
    OBJECT_NOT_FOUND = "object_not_found"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    SIZE_EXCEEDED = "size_exceeded"
    ACCESS_DENIED = "access_denied"
    STORAGE_UNAVAILABLE = "storage_unavailable"


class ArtifactNotFoundError(Exception):
    """The requested artifact is absent from the authorized scope."""


class ArtifactTransitionError(Exception):
    """The persisted lifecycle does not permit the requested transition."""


def validate_transition(current: ArtifactStatus, target: ArtifactStatus) -> None:
    allowed = {
        ArtifactStatus.PENDING: {ArtifactStatus.AVAILABLE, ArtifactStatus.FAILED},
        ArtifactStatus.AVAILABLE: {ArtifactStatus.CLEANUP_PENDING},
        ArtifactStatus.FAILED: {ArtifactStatus.CLEANUP_PENDING},
        ArtifactStatus.CLEANUP_PENDING: {ArtifactStatus.DELETED, ArtifactStatus.CLEANUP_FAILED},
        ArtifactStatus.CLEANUP_FAILED: {ArtifactStatus.CLEANUP_PENDING},
        ArtifactStatus.DELETED: set(),
    }
    if current != target and target not in allowed[current]:
        raise ArtifactTransitionError("artifact_transition_invalid")
