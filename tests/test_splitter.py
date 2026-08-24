"""切块：大小约束、元数据传递。"""

from __future__ import annotations

from app.rag.loader import Segment
from app.rag.splitter import TextSplitter


def test_split_produces_chunks_with_metadata():
    splitter = TextSplitter(chunk_size=20, chunk_overlap=5)
    seg = Segment(text="这是第一句话。这是第二句话。这是第三句话。这是第四句话。", source="a.txt", page=1)
    chunks = splitter.split([seg])
    assert len(chunks) > 1
    for c in chunks:
        assert c.source == "a.txt"
        assert c.page == 1
        assert c.metadata["source"] == "a.txt"
        assert c.text  # 非空


def test_chunk_size_respected():
    splitter = TextSplitter(chunk_size=50, chunk_overlap=0)
    seg = Segment(text="长文本" * 100, source="b.txt")
    chunks = splitter.split([seg])
    assert all(len(c.text) <= 50 for c in chunks)


def test_empty_text_no_chunks():
    splitter = TextSplitter()
    assert splitter.split([Segment(text="   ", source="c.txt")]) == []
