"""Infrastructure-independent artifact storage contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class ArtifactLocation:
    tenant_id: str
    namespace: str
    owner_id: str
    artifact_id: str


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
