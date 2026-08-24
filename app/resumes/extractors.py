"""Validated PDF, DOCX, and TXT text extraction."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import IngestionError, UnsupportedFileError

_MAX_BYTES = 10 * 1024 * 1024
_ALLOWED_MEDIA_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
}


class FilePolicy:
    def __init__(self, max_bytes: int = _MAX_BYTES):
        self.max_bytes = max_bytes

    def validate(self, filename: str, media_type: str, content: bytes) -> str:
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_MEDIA_TYPES:
            raise UnsupportedFileError("仅支持 PDF、DOCX 和 TXT 简历")
        if media_type not in _ALLOWED_MEDIA_TYPES[extension]:
            raise UnsupportedFileError("文件类型与扩展名不匹配")
        if not content:
            raise UnsupportedFileError("文件内容为空")
        if len(content) > self.max_bytes:
            raise UnsupportedFileError("文件不能超过 10 MiB")
        return extension


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in _ALLOWED_MEDIA_TYPES:
        raise UnsupportedFileError("仅支持 PDF、DOCX 和 TXT 简历")
    if len(content) > _MAX_BYTES:
        raise UnsupportedFileError("文件不能超过 10 MiB")

    try:
        if extension == ".txt":
            text = content.decode("utf-8-sig")
        elif extension == ".pdf":
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        else:
            text = "\n".join(paragraph.text for paragraph in Document(BytesIO(content)).paragraphs)
    except (BadZipFile, OSError, PackageNotFoundError, PdfReadError, UnicodeDecodeError, ValueError) as exc:
        raise IngestionError("简历文本提取失败", code="resume_extraction_failed") from exc

    normalized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.strip() for line in normalized.splitlines() if line.strip())
    if not normalized:
        raise IngestionError("简历中没有可提取文本", code="resume_empty_text")
    return normalized
