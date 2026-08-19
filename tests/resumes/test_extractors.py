from __future__ import annotations

from io import BytesIO

import pytest


def test_txt_extraction_normalizes_bom_nul_and_line_endings():
    """Catches parser input corruption from common exported-text artifacts."""
    from app.resumes.extractors import extract_text

    text = extract_text("resume.txt", b"\xef\xbb\xbfPython\x00\r\nFastAPI")
    assert text == "Python\nFastAPI"


def test_docx_extraction_reads_paragraphs():
    """Catches accepting DOCX while silently producing no profile text."""
    from docx import Document

    from app.resumes.extractors import extract_text

    document = Document()
    document.add_paragraph("Python 后端工程师")
    document.add_paragraph("使用 FastAPI 构建服务")
    buffer = BytesIO()
    document.save(buffer)

    assert extract_text("resume.docx", buffer.getvalue()) == "Python 后端工程师\n使用 FastAPI 构建服务"


def test_extractor_rejects_unsupported_or_oversized_file():
    """Catches unbounded or executable content reaching document parsers."""
    from app.core.exceptions import UnsupportedFileError
    from app.resumes.extractors import FilePolicy, extract_text

    with pytest.raises(UnsupportedFileError):
        extract_text("resume.html", b"<script>bad</script>")
    with pytest.raises(UnsupportedFileError, match="10 MiB"):
        FilePolicy().validate("large.txt", "text/plain", b"x" * (10 * 1024 * 1024 + 1))


@pytest.mark.parametrize("filename", ["broken.pdf", "broken.docx"])
def test_malformed_supported_document_returns_stable_ingestion_error(filename):
    """Catches third-party parser exceptions escaping the API contract."""
    from app.core.exceptions import IngestionError
    from app.resumes.extractors import extract_text

    with pytest.raises(IngestionError) as captured:
        extract_text(filename, b"not a real document")
    assert captured.value.code == "resume_extraction_failed"
