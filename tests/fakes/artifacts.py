"""Reusable SDK-free artifact Fake for service tests."""

import hashlib
from typing import BinaryIO

from app.artifacts.ports import (
    MAX_ARTIFACT_BYTES,
    ArtifactChecksumMismatch,
    ArtifactInspection,
    ArtifactLengthMismatch,
    ArtifactLocation,
    ArtifactMissing,
    ArtifactTooLarge,
)


class FakeArtifactStore:
    def __init__(self):
        self._objects: dict[ArtifactLocation, bytes] = {}

    def put(self, location: ArtifactLocation, stream: BinaryIO, size_bytes: int, sha256: str) -> None:
        location.__post_init__()
        if type(size_bytes) is not int or size_bytes < 0:
            raise ArtifactLengthMismatch()
        if size_bytes > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLarge()
        parts = []
        remaining = MAX_ARTIFACT_BYTES + 1
        while remaining:
            part = stream.read(min(65536, remaining))
            if not part:
                break
            parts.append(part)
            remaining -= len(part)
        data = b"".join(parts)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLarge()
        if len(data) != size_bytes:
            raise ArtifactLengthMismatch()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ArtifactChecksumMismatch()
        self._objects[location] = data

    def inspect(self, location: ArtifactLocation) -> ArtifactInspection:
        location.__post_init__()
        if location not in self._objects:
            raise ArtifactMissing()
        data = self._objects[location]
        return ArtifactInspection(len(data), hashlib.sha256(data).hexdigest())

    def read_bounded(self, location: ArtifactLocation, max_bytes: int) -> bytes:
        if type(max_bytes) is not int or max_bytes < 0:
            raise ArtifactTooLarge()
        info = self.inspect(location)
        if info.size_bytes > min(max_bytes, MAX_ARTIFACT_BYTES):
            raise ArtifactTooLarge()
        return self._objects[location]

    def delete(self, location: ArtifactLocation) -> None:
        location.__post_init__()
        self._objects.pop(location, None)

    def probe(self) -> bool:
        return True
