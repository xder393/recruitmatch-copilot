"""FastAPI 应用入口：组装配置、中间件、路由、异常处理与启动生命周期。"""
from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from langchain_openai import ChatOpenAI

from app.agents.agent import RagAgent
from app.api.routes import router
from app.api.v1.router import router as v1_router
from app.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, request_id_var, setup_logging
from app.embeddings.embedder import Embedder
from app.rag.retriever import Retriever
from app.services.ingestion import IngestionService
from app.storage.conversations import ConversationStore
from app.storage.vector_store import VectorStore
from app.database import Base, create_engine_and_session
from app.models import AuditLog, Job, JobTemplate, JobVersion, ModelTrace, Tenant, User  # noqa: F401
from app.security.tokens import TokenSettings
from app.resumes.artifacts import LocalArtifactStore
from app.resumes.parser import HeuristicResumeParser
from app.services.resume_processing import ResumeProcessingService
from app.tasks.dispatcher import CeleryTaskDispatcher, InlineTaskDispatcher
from app.tasks.knowledge_tasks import CeleryKnowledgeDispatcher, InlineKnowledgeDispatcher
from app.ai.gateway import OpenAICompatibleGateway
from app.ai.resume_parser import LLMResumeParser
from app.knowledge.artifacts import KnowledgeArtifactStore
from app.knowledge.embeddings import BGEEmbedder
from app.knowledge.index import RecruitingVectorIndex
from app.services.knowledge_processing import KnowledgeProcessingService
from app.ai.semantic_matching import SemanticMatcher
from app.matching.engine import MatchingEngine
from app.matching.hybrid import HybridMatchingEngine

logger = get_logger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_PROJECT_ROOT, "web", "index.html")


def init_state(app: FastAPI, settings: Settings) -> None:
    """初始化共享单例（幂等：测试注入时跳过）。"""
    if getattr(app.state, "_initialized", False):
        return

    embedder = Embedder(settings.embedding_model)
    store = VectorStore(embedder)

    if not store.load(settings.index_dir):
        # 索引不存在则从 data/ 重建并落盘
        ingestion = IngestionService(store, settings)
        ingestion.rebuild_index()

    retriever = Retriever(store, settings)
    ingestion = IngestionService(store, settings)
    conversations = ConversationStore(settings.conversations_file)

    llm = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.llm_temperature,
        timeout=60,
    )
    agent = RagAgent(
        llm,
        retriever,
        top_k=settings.top_k,
        max_iterations=settings.max_agent_iterations,
    )

    app.state.embedder = embedder
    app.state.store = store
    app.state.retriever = retriever
    app.state.ingestion = ingestion
    app.state.conversations = conversations
    app.state.agent = agent
    app.state._initialized = True
    logger.info("应用初始化完成，知识库共 %d 块", len(store))


def init_recruiting_state(app: FastAPI, settings: Settings, structured_model=None, knowledge_embedder=None) -> None:
    """Initialize recruiting persistence once per application instance."""
    if getattr(app.state, "_recruiting_initialized", False):
        return
    engine, session_factory = create_engine_and_session(settings.database_url)
    Base.metadata.create_all(engine)
    app.state.database_engine = engine
    app.state.session_factory = session_factory
    app.state.token_settings = TokenSettings(
        secret_key=settings.jwt_secret,
        access_token_minutes=settings.access_token_minutes,
    )
    artifact_store = LocalArtifactStore(Path(settings.artifact_dir))
    fallback_parser = HeuristicResumeParser()
    recruiting_model = structured_model
    if recruiting_model is None and settings.ai_enabled:
        recruiting_model = OpenAICompatibleGateway(settings)
    parser = (
        LLMResumeParser(
            recruiting_model,
            fallback_parser,
            prompt_version=settings.resume_prompt_version,
            max_evidence_characters=settings.max_evidence_characters,
        )
        if settings.ai_enabled
        else fallback_parser
    )
    knowledge_index = RecruitingVectorIndex(
        session_factory,
        knowledge_embedder or BGEEmbedder(settings.embedding_model),
    )
    # Automatic resume/JD indexing belongs to the AI feature. Keeping it off in
    # rule-only mode also prevents an accidental model download in basic setups.
    source_index = knowledge_index if settings.ai_enabled or knowledge_embedder is not None else None
    processor = ResumeProcessingService(session_factory, artifact_store, parser, source_index=source_index)
    knowledge_artifact_store = KnowledgeArtifactStore(Path(settings.knowledge_artifact_dir))
    knowledge_processor = KnowledgeProcessingService(session_factory, knowledge_artifact_store, knowledge_index)
    app.state.artifact_store = artifact_store
    app.state.resume_processor = processor
    app.state.knowledge_index = knowledge_index
    app.state.recruiting_source_index = source_index
    app.state.knowledge_artifact_store = knowledge_artifact_store
    app.state.knowledge_processor = knowledge_processor
    app.state.hybrid_matching_engine = HybridMatchingEngine(
        MatchingEngine(),
        SemanticMatcher(
            recruiting_model,
            knowledge_index,
            enabled=settings.ai_enabled,
            max_evidence_characters=settings.max_evidence_characters,
        ),
    )
    app.state.task_dispatcher = (
        CeleryTaskDispatcher() if settings.task_mode == "celery" else InlineTaskDispatcher(processor)
    )
    app.state.knowledge_dispatcher = (
        CeleryKnowledgeDispatcher()
        if settings.task_mode == "celery"
        else InlineKnowledgeDispatcher(knowledge_processor)
    )
    app.state._recruiting_initialized = True


def create_app(settings: Settings | None = None, structured_model=None, knowledge_embedder=None) -> FastAPI:
    settings = settings or Settings.load()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_recruiting_state(app, settings, structured_model, knowledge_embedder)
        if settings.legacy_rag_enabled:
            init_state(app, settings)
        yield

    app = FastAPI(
        title="知识库智能问答系统（RAG + Agent）",
        description="上传资料 → 建立知识库 → Agent 意图路由（知识库检索 / 计算 / 闲聊）→ 带来源的回答",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # ---- 请求中间件：request_id + 访问日志 + 延迟 ----
    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            status = getattr(response, "status_code", "-")
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            logger.info("%s %s -> %s (%.1fms)", request.method, request.url.path, status, latency_ms)
            if response is not None and not request.url.path.startswith("/api/v1/health/"):
                principal = getattr(request.state, "principal", None)
                try:
                    with request.app.state.session_factory() as audit_session:
                        audit_session.add(
                            AuditLog(
                                tenant_id=getattr(principal, "tenant_id", None),
                                user_id=getattr(principal, "user_id", None),
                                request_id=request_id,
                                method=request.method,
                                path=request.url.path,
                                status_code=int(status),
                                latency_ms=latency_ms,
                            )
                        )
                        audit_session.commit()
                except Exception:
                    logger.warning("审计日志写入失败 request_id=%s", request_id)
            request_id_var.reset(token)

    # ---- 异常处理 ----
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            {"ok": False, "error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"ok": False, "error": {"code": "validation_error", "message": "参数校验失败", "detail": exc.errors()}},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            {"ok": False, "error": {"code": "internal_error", "message": "服务器内部错误"}},
            status_code=500,
        )

    app.include_router(router)
    app.include_router(v1_router)

    # ---- 前端页面 ----
    @app.get("/", response_class=HTMLResponse)
    async def root():
        if os.path.exists(_INDEX_HTML):
            with open(_INDEX_HTML, encoding="utf-8") as f:
                return f.read()
        return "<h3>前端页面缺失（web/index.html）</h3>"

    return app


app = create_app()
