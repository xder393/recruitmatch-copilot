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

from app.ai.explanations import GroundedExplanationService
from app.ai.gateway import OpenAICompatibleGateway
from app.ai.resume_parser import LLMResumeParser
from app.ai.semantic_matching import SemanticMatcher
from app.api.v1.router import router as v1_router
from app.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, request_id_var, setup_logging
from app.database import create_engine_and_session
from app.knowledge.artifacts import KnowledgeArtifactStore
from app.knowledge.embeddings import BGEEmbedder
from app.matching.engine import MatchingEngine
from app.matching.hybrid import HybridMatchingEngine
from app.models import AuditLog, Job, JobTemplate, JobVersion, ModelTrace, Tenant, User  # noqa: F401
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.resumes.artifacts import LocalArtifactStore
from app.resumes.parser import HeuristicResumeParser
from app.security.tokens import TokenSettings
from app.services.ai_tracing import AITraceSink
from app.services.knowledge_processing import KnowledgeProcessingService
from app.services.resume_processing import ResumeProcessingService
from app.tasks.dispatcher import configure_task_dispatcher
from app.retrieval.generations import GenerationWriter
from app.retrieval.indexing import SourceIndexer
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from app.retrieval.ports import RecruitingVectorIndex

logger = get_logger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_PROJECT_ROOT, "web", "index.html")


def init_recruiting_state(
    app: FastAPI,
    settings: Settings,
    structured_model=None,
    knowledge_embedder=None,
    retrieval_index: RecruitingVectorIndex | None = None,
    source_indexer: SourceIndexer | None = None,
) -> None:
    """Initialize recruiting persistence once per application instance."""
    if getattr(app.state, "_recruiting_initialized", False):
        return
    engine, session_factory = create_engine_and_session(settings.database_url)
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
    embedder = knowledge_embedder or BGEEmbedder(settings.embedding_model)
    if retrieval_index is None:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("SQLite app composition requires an explicit retrieval fake")
        retrieval_index = PgVectorRecruitingIndex(session_factory)
    if source_indexer is None:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("SQLite app composition requires an explicit generation fake")
        source_indexer = SourceIndexer(GenerationWriter(session_factory), embedder)
    uow_factory = SqlAlchemyUnitOfWorkFactory(session_factory)
    processor = ResumeProcessingService(uow_factory, artifact_store, parser, source_indexer=source_indexer)
    knowledge_artifact_store = KnowledgeArtifactStore(Path(settings.knowledge_artifact_dir))
    knowledge_processor = KnowledgeProcessingService(uow_factory, knowledge_artifact_store, source_indexer)
    ai_trace_sink = AITraceSink(uow_factory)
    app.state.artifact_store = artifact_store
    app.state.resume_processor = processor
    app.state.retrieval_index = retrieval_index
    app.state.source_indexer = source_indexer
    app.state.embedding_adapter = embedder
    app.state.knowledge_artifact_store = knowledge_artifact_store
    app.state.knowledge_processor = knowledge_processor
    app.state.hybrid_matching_engine = HybridMatchingEngine(
        MatchingEngine(),
        SemanticMatcher(
            recruiting_model,
            retrieval_index,
            embedder,
            enabled=settings.ai_enabled,
            max_evidence_characters=settings.max_evidence_characters,
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
            trace_sink=ai_trace_sink,
        ),
    )
    app.state.grounded_explanation_service = GroundedExplanationService(
        recruiting_model,
        enabled=settings.ai_enabled,
        prompt_version=settings.explanation_prompt_version,
        max_evidence_characters=settings.max_evidence_characters,
        citation_resolver=retrieval_index.resolve_active_citations,
        trace_sink=ai_trace_sink,
    )
    configure_task_dispatcher(app, settings.task_mode, processor, knowledge_processor)
    app.state._recruiting_initialized = True


def create_app(
    settings: Settings | None = None,
    structured_model=None,
    knowledge_embedder=None,
    *,
    retrieval_index: RecruitingVectorIndex | None = None,
    source_indexer: SourceIndexer | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_recruiting_state(
            app,
            settings,
            structured_model,
            knowledge_embedder,
            retrieval_index,
            source_indexer,
        )
        yield

    app = FastAPI(
        title="RecruitMatch Copilot",
        description="面向招聘团队的岗位、简历、知识与可解释匹配工作台",
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
