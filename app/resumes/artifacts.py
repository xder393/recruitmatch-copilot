"""Artifact store interface and safe local implementation."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import ResourceNotFoundError, UnsupportedFileError

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    size_bytes: int
    sha256: str


class LocalArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, tenant_id: str, original_filename: str, content: bytes) -> StoredArtifact:
        if not _SAFE_SEGMENT.fullmatch(tenant_id):
            raise UnsupportedFileError("非法租户标识")
        extension = Path(original_filename).suffix.lower()
        key = f"{tenant_id}/{uuid.uuid4().hex}{extension}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredArtifact(key=key, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ResourceNotFoundError("简历文件不存在")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise UnsupportedFileError("非法文件存储键") from exc
        return path
