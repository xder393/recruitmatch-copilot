"""检索层：top_k、空库、上下文拼接。"""

from __future__ import annotations

from app.rag.retriever import Retriever, format_context


def test_retrieve_respects_top_k(retriever):
    results = retriever.retrieve("链表", top_k=1)
    assert len(results) == 1


def test_retrieve_empty_store(embedder, settings):
    from app.storage.vector_store import VectorStore

    empty = Retriever(VectorStore(embedder), settings)
    assert empty.retrieve("任意问题") == []
    assert empty.count() == 0


def test_format_context_contains_source(retriever):
    results = retriever.retrieve("年假", top_k=1)
    ctx = format_context(results)
    assert "公司制度.pdf" in ctx
    assert "第 1 页" in ctx
