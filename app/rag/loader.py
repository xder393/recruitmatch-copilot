"""文档解析：txt / pdf → 带来源信息的片段（Segment）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import IngestionError


@dataclass
class Segment:
    """一段待切分的原始文本，已带上来源文件与页码。"""

    text: str
    source: str
    page: int | None = None


def load_text_file(path: str, source: str) -> list[Segment]:
    try:
        with open(path, encoding="utf-8") as f:
            return [Segment(f.read(), source)]
    except Exception as exc:
        raise IngestionError(f"读取文本文件失败 {source}: {exc}") from exc


def load_pdf_file(path: str, source: str) -> list[Segment]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
    except Exception as exc:
        raise IngestionError(f"解析 PDF 失败 {source}: {exc}") from exc

    segments: list[Segment] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            segments.append(Segment(text=text, source=source, page=i + 1))
    if not segments:
        raise IngestionError(f"PDF 未提取到任何文本（可能是扫描件）: {source}")
    return segments


def load_file(path: str, source: str) -> list[Segment]:
    if path.lower().endswith(".pdf"):
        return load_pdf_file(path, source)
    return load_text_file(path, source)
