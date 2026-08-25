"""Explicit SQLite application composition for unit/API tests."""

from __future__ import annotations

from app.main import create_app
from tests.fakes.retrieval import sqlite_retrieval_dependencies


class DeterministicEmbeddingAdapter:
    model_name = "fake-512-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * 512
        vector[sum(text.encode()) % 512] = 1.0
        return vector


def create_sqlite_test_app(settings, structured_model=None, knowledge_embedder=None):
    embedder = knowledge_embedder or DeterministicEmbeddingAdapter()
    retrieval_index, source_indexer = sqlite_retrieval_dependencies(settings.database_url, embedder)
    return create_app(
        settings,
        structured_model=structured_model,
        knowledge_embedder=embedder,
        retrieval_index=retrieval_index,
        source_indexer=source_indexer,
    )
