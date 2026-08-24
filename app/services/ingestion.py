"""文档入库编排：上传 → 解析 → 切块 → 向量化 → 入库；启动重建索引。"""

from __future__ import annotations

import os

from app.config import Settings
from app.core.exceptions import DocumentNotFoundError, UnsupportedFileError
from app.core.logging import get_logger
from app.rag.loader import load_file
from app.rag.splitter import TextSplitter
from app.storage.vector_store import VectorStore

logger = get_logger(__name__)

_ALLOWED_EXT = (".txt", ".pdf")


class IngestionService:
    def __init__(self, store: VectorStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.splitter = TextSplitter(settings.chunk_size, settings.chunk_overlap)

    # ---- 入库 ----
    def ingest_bytes(self, filename: str, content: bytes) -> int:
        self._check_filename(filename)
        filepath = os.path.join(self.settings.data_dir, filename)
        os.makedirs(self.settings.data_dir, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(content)

        # 覆盖同名文件：先移除旧块
        self.store.delete_by_source(filename)
        chunks = self._load_and_split(filepath, filename)
        self.store.add(chunks)
        self._persist()
        logger.info("入库 %s：%d 块", filename, len(chunks))
        return len(chunks)

    def rebuild_index(self) -> int:
        """启动时若索引不存在，从 data/ 下的 txt/pdf 重建。"""
        self.store.clear()
        total = 0
        if not os.path.isdir(self.settings.data_dir):
            return 0
        for filename in sorted(os.listdir(self.settings.data_dir)):
            if not filename.lower().endswith(_ALLOWED_EXT):
                continue
            path = os.path.join(self.settings.data_dir, filename)
            chunks = self._load_and_split(path, filename)
            self.store.add(chunks)
            total += len(chunks)
        self._persist()
        logger.info("重建索引完成：共 %d 块", total)
        return total

    # ---- 删除 ----
    def delete_document(self, filename: str) -> None:
        self._check_filename(filename)
        filepath = os.path.join(self.settings.data_dir, filename)
        if not os.path.exists(filepath):
            raise DocumentNotFoundError(f"文件不存在: {filename}")
        os.remove(filepath)
        self.store.delete_by_source(filename)
        self._persist()
        logger.info("删除文档 %s", filename)

    # ---- 查询 ----
    def list_documents(self) -> list[dict]:
        files = []
        if os.path.isdir(self.settings.data_dir):
            for filename in sorted(os.listdir(self.settings.data_dir)):
                if not filename.lower().endswith(_ALLOWED_EXT):
                    continue
                path = os.path.join(self.settings.data_dir, filename)
                files.append({"name": filename, "size": os.path.getsize(path)})
        return files

    # ---- 内部 ----
    def _load_and_split(self, path: str, source: str):
        segments = load_file(path, source)
        return self.splitter.split(segments)

    def _persist(self) -> None:
        try:
            self.store.save(self.settings.index_dir)
        except Exception as exc:
            # 索引持久化失败不应让上传请求整体失败，但需要留痕
            logger.warning("索引持久化失败: %s", exc)

    @staticmethod
    def _check_filename(filename: str) -> None:
        if not filename.lower().endswith(_ALLOWED_EXT):
            raise UnsupportedFileError(f"仅支持 txt / pdf 文件: {filename}")
        # 防路径穿越：只取 basename，拒绝包含目录分隔符的文件名
        if os.path.basename(filename) != filename:
            raise UnsupportedFileError(f"非法文件名: {filename}")
