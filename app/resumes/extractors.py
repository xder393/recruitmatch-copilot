"""Validated PDF, DOCX, and TXT text extraction."""

from __future__ import annotations

from io import BytesIO
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterator, cast
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader, filters
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from app.core.exceptions import IngestionError, UnsupportedFileError

_MAX_BYTES = 10 * 1024 * 1024
_MAX_EXPANDED = 16 * 1024 * 1024
_MAX_TEXT_CHARACTERS = 1_048_576
# The locked pypdf version has module-level configuration. Set once during
# module initialization, before any request can enter extraction; never toggle
# these globals per request. All parser callers use this module.
filters.ZLIB_MAX_OUTPUT_LENGTH = 8 * 1024 * 1024
filters.LZW_MAX_OUTPUT_LENGTH = 8 * 1024 * 1024
filters.RUN_LENGTH_MAX_OUTPUT_LENGTH = 8 * 1024 * 1024
filters.JBIG2_MAX_OUTPUT_LENGTH = 8 * 1024 * 1024
filters.MAX_DECLARED_STREAM_LENGTH = 8 * 1024 * 1024
filters.MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH = 8 * 1024 * 1024
filters.FLATE_MAX_BUFFER_SIZE = 8 * 1024 * 1024
filters.ZLIB_MAX_RECOVERY_INPUT_LENGTH = 1024 * 1024
filters.JBIG2DEC_BINARY = None
_ALLOWED_MEDIA_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
}


@dataclass(frozen=True)
class PreparedUpload:
    stream: BinaryIO
    sha256: str
    size_bytes: int


def _resource_limit() -> None:
    raise IngestionError("文档超过安全处理上限", code="document_resource_limit")


def _docx_text(stream: BinaryIO) -> str:
    with ZipFile(stream) as archive:
        entries = archive.infolist()
        if len(entries) > 512 or sum(item.file_size for item in entries) > _MAX_EXPANDED:
            _resource_limit()
        for item in entries:
            if item.file_size > 8 * 1024 * 1024 or item.file_size > max(item.compress_size, 1) * 200:
                _resource_limit()
        # Read each ZIP entry within the advertised total bound; validates CRC
        # and real expanded bytes before python-docx/lxml sees the package.
        expanded = 0
        for item in entries:
            with archive.open(item) as member:
                while part := member.read(65536):
                    expanded += len(part)
                    if expanded > _MAX_EXPANDED:
                        _resource_limit()
    stream.seek(0)
    parts, total = [], 0
    for paragraph in Document(stream).paragraphs:
        total += len(paragraph.text) + 1
        if total > _MAX_TEXT_CHARACTERS:
            _resource_limit()
        parts.append(paragraph.text)
    return "\n".join(parts)


def _pdf_text(stream: BinaryIO) -> str:
    reader = PdfReader(stream, root_object_recovery_limit=1000)
    if reader.is_encrypted:
        raise ValueError("encrypted document")
    # Bound referenced objects and decoded resources BEFORE text extraction,
    # including nested form XObjects and fonts. The parser's fixed filter caps
    # bound each decoding operation; this traversal bounds their aggregate.
    stack, expanded, objects = [reader.trailer], 0, 0
    visited: set[tuple[int, int]] = set()
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            identity = (obj.idnum, obj.generation)
            if identity in visited:
                continue
            visited.add(identity)
            obj = obj.get_object()
        objects += 1
        if objects > 10000:
            _resource_limit()
        if isinstance(obj, StreamObject):
            expanded += len(obj.get_data())
            if expanded > _MAX_EXPANDED:
                _resource_limit()
        if isinstance(obj, DictionaryObject):
            stack.extend(obj.values())
        elif isinstance(obj, ArrayObject):
            stack.extend(obj)
        if len(stack) > 10000:
            _resource_limit()
    if len(reader.pages) > 100:
        _resource_limit()
    parts, total, content_total = [], 0, 0
    for page in reader.pages:
        contents = page.get_contents()
        content_size = len(contents.get_data()) if contents is not None else 0
        content_total += content_size
        if content_size > 2 * 1024 * 1024 or content_total > 8 * 1024 * 1024:
            _resource_limit()
        part = page.extract_text() or ""
        total += len(part) + 1
        if total > _MAX_TEXT_CHARACTERS:
            _resource_limit()
        parts.append(part)
    return "\n".join(parts)


def _parse(extension: str, stream: BinaryIO) -> str:
    try:
        stream.seek(0)
        if extension == ".pdf":
            text = _pdf_text(stream)
        elif extension == ".docx":
            text = _docx_text(stream)
        else:
            text = stream.read(_MAX_BYTES + 1).decode("utf-8-sig")
        if len(text) > _MAX_TEXT_CHARACTERS:
            _resource_limit()
        return text
    except IngestionError:
        raise
    except Exception as exc:
        # Parser libraries raise multiple bounded-read and malformed-object
        # exception types; never expose their document-derived exception text.
        raise IngestionError("简历文本提取失败", code="resume_extraction_failed") from exc
    finally:
        stream.seek(0)


class FilePolicy:
    def __init__(self, max_bytes: int = _MAX_BYTES):
        self.max_bytes = max_bytes

    def validate(self, filename: str, media_type: str, content: bytes) -> str:
        return self.validate_stream(filename, media_type, BytesIO(content), len(content))

    def validate_stream(self, filename: str, media_type: str, stream: BinaryIO, size_bytes: int) -> str:
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_MEDIA_TYPES:
            raise UnsupportedFileError("仅支持 PDF、DOCX 和 TXT 简历")
        if media_type not in _ALLOWED_MEDIA_TYPES[extension]:
            raise UnsupportedFileError("文件类型与扩展名不匹配")
        if not size_bytes:
            raise UnsupportedFileError("文件内容为空")
        if size_bytes > min(self.max_bytes, _MAX_BYTES):
            raise UnsupportedFileError("文件不能超过 10 MiB")
        stream.seek(0)
        signature = stream.read(8)
        stream.seek(0)
        if (
            (extension == ".pdf" and not signature.startswith(b"%PDF-"))
            or (extension == ".docx" and not signature.startswith(b"PK\x03\x04"))
            or (extension == ".txt" and signature.startswith((b"%PDF-", b"PK\x03\x04")))
        ):
            raise UnsupportedFileError("文件内容与扩展名不匹配")
        _parse(extension, stream)
        return extension

    @contextmanager
    def prepare(self, filename: str, media_type: str, source: BinaryIO | bytes) -> Iterator[PreparedUpload]:
        source = BytesIO(source) if isinstance(source, bytes) else source
        with SpooledTemporaryFile(max_size=_MAX_BYTES, mode="w+b") as temporary:
            digest, size = hashlib.sha256(), 0
            while part := source.read(65536):
                size += len(part)
                if size > min(self.max_bytes, _MAX_BYTES):
                    raise UnsupportedFileError("文件不能超过 10 MiB")
                digest.update(part)
                temporary.write(part)
            temporary.seek(0)
            buffered = cast(BinaryIO, temporary)
            self.validate_stream(filename, media_type, buffered, size)
            yield PreparedUpload(buffered, digest.hexdigest(), size)


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in _ALLOWED_MEDIA_TYPES:
        raise UnsupportedFileError("仅支持 PDF、DOCX 和 TXT 简历")
    if len(content) > _MAX_BYTES:
        raise UnsupportedFileError("文件不能超过 10 MiB")

    text = _parse(extension, BytesIO(content))

    normalized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.strip() for line in normalized.splitlines() if line.strip())
    if not normalized:
        raise IngestionError("简历中没有可提取文本", code="resume_empty_text")
    return normalized
