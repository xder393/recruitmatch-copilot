"""文本切块：递归分隔符切分，保留来源元数据，生成 Chunk。"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.loader import Segment
from app.storage.vector_store import Chunk, make_chunk_id

# 针对中文 + 代码的递归分隔符：优先按段落/换行/标点切，最后才按字符兜底
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", " ", ""]


class TextSplitter:
    """封装 LangChain 的递归切分器，输出带元数据的 Chunk。"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 100):
        self._splitter = RecursiveCharacterTextSplitter(
            separators=_SEPARATORS,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )

    def split(self, segments: list[Segment]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for seg in segments:
            pieces = self._splitter.split_text(seg.text)
            for idx, piece in enumerate(pieces):
                chunk = Chunk(
                    id=make_chunk_id(seg.source, seg.page, idx),
                    text=piece,
                    source=seg.source,
                    page=seg.page,
                    chunk_index=idx,
                    metadata={"source": seg.source, "page": seg.page},
                )
                chunks.append(chunk)
        return chunks
