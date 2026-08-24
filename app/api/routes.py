"""API 路由：文档管理 / 问答 / 会话 / 健康检查。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from app.agents.agent import RagAgent
from app.api.deps import get_agent, get_conversations, get_ingestion
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationResponse,
    FileListResponse,
    Source,
    StatusResponse,
    UploadResponse,
)
from app.core.exceptions import DocumentNotFoundError
from app.core.logging import get_logger
from app.services.ingestion import IngestionService
from app.storage.conversations import ConversationStore
from app.storage.vector_store import SearchResult

router = APIRouter(prefix="/api")
logger = get_logger(__name__)


def _to_source(result: SearchResult) -> Source:
    return Source(
        source=result.chunk.source,
        page=result.chunk.page,
        score=round(result.score, 4),
        snippet=result.chunk.text[:100],
    )


# ---------- 健康检查 ----------
@router.get("/health")
async def health(ingestion: IngestionService = Depends(get_ingestion)):
    return {"ok": True, "status": "healthy", "total_chunks": ingestion.store.__len__()}


@router.get("/status", response_model=StatusResponse)
async def status(ingestion: IngestionService = Depends(get_ingestion)):
    return StatusResponse(
        total_chunks=len(ingestion.store),
        documents=len(ingestion.list_documents()),
    )


# ---------- 文档管理 ----------
@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    ingestion: IngestionService = Depends(get_ingestion),
):
    filename = file.filename or "untitled"
    content = await file.read()
    chunks = ingestion.ingest_bytes(filename, content)
    return UploadResponse(message=f"「{filename}」上传成功", chunks=chunks)


@router.get("/files", response_model=FileListResponse)
async def list_files(ingestion: IngestionService = Depends(get_ingestion)):
    return FileListResponse(files=ingestion.list_documents())


@router.delete("/files/{filename}")
async def delete_file(
    filename: str,
    ingestion: IngestionService = Depends(get_ingestion),
):
    try:
        ingestion.delete_document(filename)
    except DocumentNotFoundError as exc:
        return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message}}, status_code=404)
    return {"ok": True, "message": f"已删除「{filename}」"}


# ---------- 问答（无状态，走 Agent）----------
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, agent: RagAgent = Depends(get_agent)):
    result = agent.run(request.question)
    return ChatResponse(
        answer=result.answer,
        sources=[_to_source(r) for r in result.sources],
        tool_calls=result.tool_calls,
    )


# ---------- 会话管理 ----------
@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(convs: ConversationStore = Depends(get_conversations)):
    return ConversationListResponse(conversations=convs.list())


@router.post("/conversations", response_model=CreateConversationResponse)
async def create_conversation(convs: ConversationStore = Depends(get_conversations)):
    conv = convs.create()
    return CreateConversationResponse(conversation=_to_detail(conv))


@router.get("/conversations/{conv_id}", response_model=ConversationResponse)
async def get_conversation(conv_id: str, convs: ConversationStore = Depends(get_conversations)):
    conv = convs.get(conv_id)
    if conv is None:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "对话不存在"}}, status_code=404)
    return ConversationResponse(conversation=_to_detail(conv))


@router.post("/conversations/{conv_id}/ask", response_model=ChatResponse)
async def ask_in_conversation(
    conv_id: str,
    request: ChatRequest,
    convs: ConversationStore = Depends(get_conversations),
    agent: RagAgent = Depends(get_agent),
):
    conv = convs.get(conv_id)
    if conv is None:
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "对话不存在"}}, status_code=404)

    # 历史（不含当前问题）注入 Agent，实现多轮上下文
    history = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
    convs.append_message(conv_id, "user", request.question)

    result = agent.run(request.question, chat_history=history)
    convs.append_message(
        conv_id,
        "assistant",
        result.answer,
        sources=[_to_source(r).model_dump() for r in result.sources],
        tool_calls=result.tool_calls,
    )
    return ChatResponse(
        answer=result.answer,
        sources=[_to_source(r) for r in result.sources],
        tool_calls=result.tool_calls,
    )


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, convs: ConversationStore = Depends(get_conversations)):
    if not convs.delete(conv_id):
        return JSONResponse({"ok": False, "error": {"code": "not_found", "message": "对话不存在"}}, status_code=404)
    return {"ok": True, "message": "对话已删除"}


def _to_detail(conv: dict) -> ConversationDetail:
    messages = []
    for m in conv.get("messages", []):
        sources = [Source(**s) if isinstance(s, dict) else s for s in m.get("sources", [])]
        messages.append({"role": m["role"], "content": m["content"], "sources": sources})
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        messages=messages,
    )
