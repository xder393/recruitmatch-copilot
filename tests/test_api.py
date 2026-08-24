"""API：健康检查、参数校验、上传、问答（注入假 Agent）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.agent import AgentResult
from app.main import create_app
from app.services.ingestion import IngestionService
from app.storage.conversations import ConversationStore


class _FakeAgent:
    def run(self, question, chat_history=None):
        return AgentResult(answer="测试回答", tool_calls=[], sources=[], latency_ms=0.0)


@pytest.fixture
def client(store, settings):
    app = create_app(settings)
    app.state.store = store
    # 使用真实入库服务，确保扩展名校验等逻辑真正被执行
    app.state.ingestion = IngestionService(store, settings)
    app.state.agent = _FakeAgent()
    app.state.conversations = ConversationStore(settings.conversations_file)
    app.state._initialized = True
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["total_chunks"] == 3


def test_chat_empty_question_rejected(client):
    r = client.post("/api/chat", json={"question": ""})
    assert r.status_code == 422


def test_chat_returns_answer(client):
    r = client.post("/api/chat", json={"question": "你好"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["answer"] == "测试回答"


def test_upload_rejects_bad_ext(client):
    r = client.post("/api/upload", files={"file": ("a.md", b"x", "text/markdown")})
    assert r.status_code == 400


def test_upload_txt(client):
    r = client.post("/api/upload", files={"file": ("笔记.txt", "这是测试内容".encode(), "text/plain")})
    assert r.status_code == 200
    assert r.json()["chunks"] >= 1


def test_conversation_flow(client):
    r = client.post("/api/conversations")
    conv = r.json()["conversation"]
    conv_id = conv["id"]

    r = client.post(f"/api/conversations/{conv_id}/ask", json={"question": "你好"})
    assert r.status_code == 200
    assert r.json()["answer"] == "测试回答"

    r = client.get(f"/api/conversations/{conv_id}")
    assert r.status_code == 200
    assert len(r.json()["conversation"]["messages"]) == 2
