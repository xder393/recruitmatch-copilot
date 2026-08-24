"""配置管理：从环境变量 / .env 读取，带类型转换与校验。"""

from __future__ import annotations

import os
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
    artifact_dir: str = "data/resumes"
    knowledge_artifact_dir: str = "data/knowledge"
    task_mode: str = "inline"
    celery_broker_url: str = "redis://localhost:6379/0"

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

    @classmethod
    def load(cls) -> "Settings":
        settings = cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url).strip(),
            chat_model=os.getenv("OPENAI_MODEL", cls.chat_model).strip(),
            embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", cls.embedding_model).strip(),
            database_url=os.getenv("DATABASE_URL", cls.database_url).strip(),
            artifact_dir=os.getenv("ARTIFACT_DIR", cls.artifact_dir).strip(),
            knowledge_artifact_dir=os.getenv("KNOWLEDGE_ARTIFACT_DIR", cls.knowledge_artifact_dir).strip(),
            task_mode=os.getenv("TASK_MODE", cls.task_mode).strip(),
            celery_broker_url=os.getenv("CELERY_BROKER_URL", cls.celery_broker_url).strip(),
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
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.ai_enabled and not self.api_key:
            raise ConfigError("启用 AI 时必须配置 OPENAI_API_KEY")
        if self.model_timeout_seconds <= 0 or self.model_max_retries < 0:
            raise ConfigError("模型超时必须大于 0，重试次数不能小于 0")
        if self.retrieval_top_k <= 0 or not (0.0 <= self.retrieval_min_score <= 1.0):
            raise ConfigError("AI 检索参数非法")
        if self.max_evidence_characters <= 0:
            raise ConfigError("MAX_EVIDENCE_CHARACTERS 必须大于 0")
