"""FastAPI 依赖：从 app.state 取出共享单例（存储 / Agent / 服务）。"""

from __future__ import annotations

from fastapi import Request

from app.agents.agent import RagAgent
from app.config import Settings
from app.services.ingestion import IngestionService
from app.storage.conversations import ConversationStore
from app.storage.vector_store import VectorStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> VectorStore:
    return request.app.state.store


def get_agent(request: Request) -> RagAgent:
    return request.app.state.agent


def get_ingestion(request: Request) -> IngestionService:
    return request.app.state.ingestion


def get_conversations(request: Request) -> ConversationStore:
    return request.app.state.conversations
