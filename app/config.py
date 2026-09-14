"""配置管理：从环境变量 / .env 读取，带类型转换与校验。"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """配置缺失或非法时抛出。"""


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数，当前值: {raw!r}") from exc


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是数字，当前值: {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"环境变量 {name} 必须是布尔值，当前值: {raw!r}")


@dataclass(frozen=True)
class Settings:
    """全局配置对象。字段名与环境变量一一对应（大写）。"""

    # —— RecruitMatch AI provider ——
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-chat"

    # —— Embedding ——
    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # —— 企业业务数据库 ——
    database_url: str = "sqlite:///data/recruitmatch.db"
    task_mode: str = "inline"
    celery_broker_url: str = "redis://localhost:6379/0"
    processing_lease_seconds: int = 300
    processing_max_attempts: int = 5
    task_soft_time_limit: int = 540
    task_hard_time_limit: int = 600
    redis_visibility_timeout: int = 900
    retry_short_seconds: int = 30
    retry_long_seconds: int = 300
    retry_safety_margin_seconds: int = 30

    # —— 身份认证 ——
    jwt_secret: str = "dev-only-change-me-before-production"
    access_token_minutes: int = 30

    # —— RecruitMatch AI ——
    ai_enabled: bool = False
    model_provider: str = "openai-compatible"
    llm_temperature: float = 0.2
    model_timeout_seconds: float = 20.0
    model_max_retries: int = 1
    model_input_cost_per_million: float = 0.0
    model_output_cost_per_million: float = 0.0
    resume_prompt_version: str = "resume-extract-v1"
    explanation_prompt_version: str = "match-explanation-v1"
    retrieval_top_k: int = 6
    retrieval_min_score: float = 0.35
    max_evidence_characters: int = 8000

    # Explicit opt-in; Compose enables telemetry separately from business readiness.
    telemetry_enabled: bool = False
    trace_sample_ratio: float = 1.0
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    telemetry_service_name: str = "recruitmatch-api"
    telemetry_environment: str = "development"

    @classmethod
    def load(cls) -> "Settings":
        settings = cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url).strip(),
            chat_model=os.getenv("OPENAI_MODEL", cls.chat_model).strip(),
            embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", cls.embedding_model).strip(),
            database_url=os.getenv("DATABASE_URL", cls.database_url).strip(),
            task_mode=os.getenv("TASK_MODE", cls.task_mode).strip(),
            celery_broker_url=os.getenv("CELERY_BROKER_URL", cls.celery_broker_url).strip(),
            processing_lease_seconds=_get_int("PROCESSING_LEASE_SECONDS", cls.processing_lease_seconds),
            processing_max_attempts=_get_int("PROCESSING_MAX_ATTEMPTS", cls.processing_max_attempts),
            task_soft_time_limit=_get_int("TASK_SOFT_TIME_LIMIT", cls.task_soft_time_limit),
            task_hard_time_limit=_get_int("TASK_HARD_TIME_LIMIT", cls.task_hard_time_limit),
            redis_visibility_timeout=_get_int("REDIS_VISIBILITY_TIMEOUT", cls.redis_visibility_timeout),
            retry_short_seconds=_get_int("RETRY_SHORT_SECONDS", cls.retry_short_seconds),
            retry_long_seconds=_get_int("RETRY_LONG_SECONDS", cls.retry_long_seconds),
            retry_safety_margin_seconds=_get_int("RETRY_SAFETY_MARGIN_SECONDS", cls.retry_safety_margin_seconds),
            jwt_secret=os.getenv("JWT_SECRET", cls.jwt_secret).strip(),
            access_token_minutes=_get_int("ACCESS_TOKEN_MINUTES", cls.access_token_minutes),
            ai_enabled=_get_bool("AI_ENABLED", cls.ai_enabled),
            model_provider=os.getenv("MODEL_PROVIDER", cls.model_provider).strip(),
            llm_temperature=_get_float("LLM_TEMPERATURE", cls.llm_temperature),
            model_timeout_seconds=_get_float("MODEL_TIMEOUT_SECONDS", cls.model_timeout_seconds),
            model_max_retries=_get_int("MODEL_MAX_RETRIES", cls.model_max_retries),
            model_input_cost_per_million=_get_float("MODEL_INPUT_COST_PER_MILLION", cls.model_input_cost_per_million),
            model_output_cost_per_million=_get_float(
                "MODEL_OUTPUT_COST_PER_MILLION", cls.model_output_cost_per_million
            ),
            resume_prompt_version=os.getenv("RESUME_PROMPT_VERSION", cls.resume_prompt_version).strip(),
            explanation_prompt_version=os.getenv("EXPLANATION_PROMPT_VERSION", cls.explanation_prompt_version).strip(),
            retrieval_top_k=_get_int("RETRIEVAL_TOP_K", cls.retrieval_top_k),
            retrieval_min_score=_get_float("RETRIEVAL_MIN_SCORE", cls.retrieval_min_score),
            max_evidence_characters=_get_int("MAX_EVIDENCE_CHARACTERS", cls.max_evidence_characters),
            telemetry_enabled=_get_bool("TELEMETRY_ENABLED", cls.telemetry_enabled),
            trace_sample_ratio=_get_float("TRACE_SAMPLE_RATIO", cls.trace_sample_ratio),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", cls.otel_exporter_otlp_endpoint),
            telemetry_service_name=os.getenv("OTEL_SERVICE_NAME", cls.telemetry_service_name),
            telemetry_environment=os.getenv("DEPLOYMENT_ENVIRONMENT", cls.telemetry_environment),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not math.isfinite(self.trace_sample_ratio) or not 0 <= self.trace_sample_ratio <= 1:
            raise ConfigError("trace sample ratio must be finite and between zero and one")
        if self.telemetry_service_name not in {"recruitmatch-api", "recruitmatch-worker", "recruitmatch-beat"}:
            raise ConfigError("telemetry service name must be a fixed API, Worker or Beat role")
        if self.telemetry_environment not in {"development", "test", "staging", "production"}:
            raise ConfigError("telemetry environment must be a fixed deployment environment")
        if os.getenv("CELERY_RESULT_BACKEND"):
            raise ConfigError("Celery result backend is disabled")
        timings = (
            self.processing_lease_seconds,
            self.processing_max_attempts,
            self.task_soft_time_limit,
            self.task_hard_time_limit,
            self.redis_visibility_timeout,
            self.retry_short_seconds,
            self.retry_long_seconds,
            self.retry_safety_margin_seconds,
        )
        if any(type(value) is not int or value <= 0 for value in timings):
            raise ConfigError("processing timing and attempt settings must be positive finite integers")
        if self.processing_lease_seconds > 86400:
            raise ConfigError("processing lease cannot exceed one day")
        if not self.task_soft_time_limit < self.task_hard_time_limit:
            raise ConfigError("processing soft time limit must precede hard time limit")
        if (
            self.redis_visibility_timeout
            <= max(self.task_hard_time_limit, self.retry_short_seconds) + self.retry_safety_margin_seconds
        ):
            raise ConfigError("visibility timeout must exceed hard limit/countdown plus safety margin")
        if self.retry_long_seconds <= self.retry_short_seconds:
            raise ConfigError("long retry delay must exceed short retry delay")
        if self.ai_enabled and not self.api_key:
            raise ConfigError("启用 AI 时必须配置 OPENAI_API_KEY")
        if self.model_timeout_seconds <= 0 or self.model_max_retries < 0:
            raise ConfigError("模型超时必须大于 0，重试次数不能小于 0")
        if self.retrieval_top_k <= 0 or not (0.0 <= self.retrieval_min_score <= 1.0):
            raise ConfigError("AI 检索参数非法")
        if self.max_evidence_characters <= 0:
            raise ConfigError("MAX_EVIDENCE_CHARACTERS 必须大于 0")
