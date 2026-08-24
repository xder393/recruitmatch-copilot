"""向量存储：增删、检索、阈值、过滤、持久化。"""

from __future__ import annotations

from app.storage.vector_store import VectorStore, make_chunk_id


def test_add_increases_length(store):
    assert len(store) == 3


def test_search_returns_top_k(store):
    results = store.search("链表是什么", top_k=2)
    assert len(results) == 2
    assert results[0].chunk.source == "数据结构.txt"
    assert results[0].score >= results[1].score  # 降序


def test_search_threshold_filters(store):
    results = store.search("年假制度", top_k=5, threshold=0.5)
    assert all(r.score >= 0.5 for r in results)
    assert any(r.chunk.source == "公司制度.pdf" for r in results)


def test_search_where_filter(store):
    results = store.search("年假", top_k=5, where={"source": "公司制度.pdf"})
    assert results and all(r.chunk.source == "公司制度.pdf" for r in results)


def test_search_empty_store(embedder):
    empty = VectorStore(embedder)
    assert empty.search("任意问题") == []


def test_delete_by_source(store):
    removed = store.delete_by_source("数据结构.txt")
    assert removed == 2
    assert len(store) == 1


def test_save_load_roundtrip(store, tmp_path):
    store.save(str(tmp_path / "index"))
    reloaded = VectorStore(store._embedder)
    assert reloaded.load(str(tmp_path / "index")) is True
    assert len(reloaded) == len(store)
    assert reloaded.chunks[0].text == store.chunks[0].text
    assert reloaded.chunks[0].metadata == store.chunks[0].metadata


def test_make_chunk_id_is_deterministic():
    assert make_chunk_id("a.txt", 1, 0) == make_chunk_id("a.txt", 1, 0)
    assert make_chunk_id("a.txt", 1, 0) != make_chunk_id("a.txt", 1, 1)
