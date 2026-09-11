"""Infrastructure-independent artifact storage contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import BinaryIO, Protocol


MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


class ArtifactError(Exception):
    code = "artifact_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class ArtifactInvalidLocation(ArtifactError):
    code = "artifact_invalid_location"


class ArtifactMissing(ArtifactError):
    code = "artifact_missing"


class ArtifactAccessDenied(ArtifactError):
    code = "artifact_access_denied"


class ArtifactChecksumMismatch(ArtifactError):
    code = "artifact_checksum_mismatch"


class ArtifactLengthMismatch(ArtifactError):
    code = "artifact_length_mismatch"


class ArtifactTooLarge(ArtifactError):
    code = "artifact_too_large"


class ArtifactStorageFailure(ArtifactError):
    code = "artifact_storage_failure"


@dataclass(frozen=True)
class ArtifactLocation:
    tenant_id: str
    namespace: str
    owner_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        if self.namespace not in ("resumes", "knowledge"):
            raise ArtifactInvalidLocation()
        for value in (self.tenant_id, self.owner_id, self.artifact_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise ArtifactInvalidLocation()


@dataclass(frozen=True)
class ArtifactInspection:
    """Verified Head metadata; missing/invalid objects raise stable ArtifactError types."""

    size_bytes: int
    sha256: str


class ArtifactStoreInspector(Protocol):
    def inspect(self, location: ArtifactLocation) -> ArtifactInspection:
        raise NotImplementedError


class ArtifactStore(Protocol):
    def put(
        self,
        location: ArtifactLocation,
        stream: BinaryIO,
        size_bytes: int,
        sha256: str,
    ) -> None:
        raise NotImplementedError

    def read_bounded(self, location: ArtifactLocation, max_bytes: int) -> bytes:
        raise NotImplementedError

    def delete(self, location: ArtifactLocation) -> None:
        raise NotImplementedError


class ArtifactStoreHealthProbe(Protocol):
    def probe(self) -> bool:
        raise NotImplementedError
