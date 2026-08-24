"""Lazy local BGE embedding adapter."""

from __future__ import annotations

from typing import Optional

import numpy as np


def _normalized(values) -> list[float]:
    vector = np.asarray(values, dtype="float32")
    norm = float(np.linalg.norm(vector))
    return (vector / norm if norm else vector).tolist()


class BGEEmbedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Optional[object] = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load().encode(texts, normalize_embeddings=True)
        return [_normalized(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._load().encode([text], normalize_embeddings=True)[0]
        return _normalized(vector)
