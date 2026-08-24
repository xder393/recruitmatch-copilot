"""Safe local artifact namespace for recruiting knowledge."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import ResourceNotFoundError, UnsupportedFileError

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class StoredKnowledgeArtifact:
    key: str
    size_bytes: int
    sha256: str


class KnowledgeArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, tenant_id: str, document_id: str, filename: str, content: bytes) -> StoredKnowledgeArtifact:
        if not _SAFE_SEGMENT.fullmatch(tenant_id) or not _SAFE_SEGMENT.fullmatch(document_id):
            raise UnsupportedFileError("非法知识文档标识")
        extension = Path(filename).suffix.lower()
        key = f"knowledge/{tenant_id}/{document_id}/{uuid.uuid4().hex}{extension}"
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredKnowledgeArtifact(key, len(content), hashlib.sha256(content).hexdigest())

    def read(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise ResourceNotFoundError("知识文档文件不存在")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        target = self._path(key)
        if target.is_file():
            target.unlink()

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise UnsupportedFileError("非法知识文档存储键") from exc
        return target
