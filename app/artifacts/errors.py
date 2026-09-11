"""Explicit translation from storage-port failures to durable bounded codes."""

from app.artifacts.ports import (
    ArtifactAccessDenied,
    ArtifactChecksumMismatch,
    ArtifactLengthMismatch,
    ArtifactMissing,
    ArtifactTooLarge,
)
from app.domain.artifacts import ArtifactErrorCode


def storage_error_code(error: Exception) -> ArtifactErrorCode:
    for exception_type, code in (
        (ArtifactMissing, ArtifactErrorCode.OBJECT_NOT_FOUND),
        (ArtifactAccessDenied, ArtifactErrorCode.ACCESS_DENIED),
        (ArtifactChecksumMismatch, ArtifactErrorCode.CHECKSUM_MISMATCH),
        (ArtifactLengthMismatch, ArtifactErrorCode.SIZE_MISMATCH),
        (ArtifactTooLarge, ArtifactErrorCode.SIZE_EXCEEDED),
    ):
        if isinstance(error, exception_type):
            return code
    return ArtifactErrorCode.STORAGE_UNAVAILABLE
