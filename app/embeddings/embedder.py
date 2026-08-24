"""文本向量化：本地 sentence-transformers 模型，懒加载 + numpy 归一化。"""

from __future__ import annotations

import numpy as np

from app.core.exceptions import EmbeddingError


class Embedder:
    """封装本地 embedding 模型，输出归一化后的 numpy 向量。

    归一化后，向量点积 == 余弦相似度，检索时可直接用矩阵乘法。
    """

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name)
            except Exception as exc:  # 下载失败 / 模型加载失败
                raise EmbeddingError(f"加载 embedding 模型失败: {self._model_name} ({exc})") from exc
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        """批量向量化，返回 shape=(n, dim) 的 float32 数组。"""
        if not texts:
            return np.zeros((0, 1), dtype="float32")
        try:
            vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        except Exception as exc:
            raise EmbeddingError(f"向量化失败: {exc}") from exc
        return np.asarray(vectors, dtype="float32")

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed([query])[0]
