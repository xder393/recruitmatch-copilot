"""对话存储：JSON 文件持久化 + 线程锁，保证并发读写安全。

说明：当前规模用 JSON 足够；若未来需要多用户 / 并发写入，
可平滑替换为 SQLite / PostgreSQL，接口不变。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid


class ConversationStore:
    def __init__(self, file_path: str):
        self._file_path = file_path
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not os.path.exists(self._file_path):
            return []
        with open(self._file_path, encoding="utf-8") as f:
            return json.load(f)

    def _write(self, conversations: list[dict]) -> None:
        os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)

    def list(self) -> list[dict]:
        with self._lock:
            convs = self._read()
        return [{"id": c["id"], "title": c["title"], "created_at": c["created_at"]} for c in convs]

    def create(self) -> dict:
        conv = {
            "id": uuid.uuid4().hex[:8],
            "title": "新对话",
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "messages": [],
        }
        with self._lock:
            convs = self._read()
            convs.insert(0, conv)
            self._write(convs)
        return conv

    def get(self, conv_id: str) -> dict | None:
        with self._lock:
            for c in self._read():
                if c["id"] == conv_id:
                    return c
        return None

    def append_message(self, conv_id: str, role: str, content: str, **extra) -> dict | None:
        with self._lock:
            convs = self._read()
            conv = next((c for c in convs if c["id"] == conv_id), None)
            if conv is None:
                return None
            message = {"role": role, "content": content}
            message.update(extra)
            conv["messages"].append(message)
            if conv["title"] == "新对话" and role == "user":
                conv["title"] = content[:20]
            self._write(convs)
            return conv

    def delete(self, conv_id: str) -> bool:
        with self._lock:
            convs = self._read()
            new_convs = [c for c in convs if c["id"] != conv_id]
            if len(new_convs) == len(convs):
                return False
            self._write(new_convs)
            return True
