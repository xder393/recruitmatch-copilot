"""Pydantic 请求 / 响应模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---- 请求 ----
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="用户问题")


# ---- 响应 ----
class Source(BaseModel):
    source: str = Field(..., description="来源文件名")
    page: Optional[int] = None
    score: float
    snippet: str


class ChatResponse(BaseModel):
    ok: bool = True
    answer: str
    sources: list[Source] = []
    tool_calls: list[str] = []


class FileInfo(BaseModel):
    name: str
    size: int


class FileListResponse(BaseModel):
    ok: bool = True
    files: list[FileInfo]


class UploadResponse(BaseModel):
    ok: bool = True
    message: str
    chunks: int


class StatusResponse(BaseModel):
    ok: bool = True
    total_chunks: int
    documents: int


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str


class ConversationListResponse(BaseModel):
    ok: bool = True
    conversations: list[ConversationSummary]


class Message(BaseModel):
    role: str
    content: str
    sources: list[Source] = []


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: str
    messages: list[Message]


class ConversationResponse(BaseModel):
    ok: bool = True
    conversation: ConversationDetail


class CreateConversationResponse(BaseModel):
    ok: bool = True
    conversation: ConversationDetail


class ErrorResponse(BaseModel):
    ok: bool = False
    error: dict
