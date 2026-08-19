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


@dataclass(frozen=True)
class Settings:
    """全局配置对象。字段名与环境变量一一对应（大写）。"""

    # —— LLM ——
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-chat"

    # —— Embedding ——
    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # —— 存储路径 ——
    data_dir: str = "data"
    index_dir: str = "data/index"
    conversations_file: str = "data/conversations.json"

    # —— 企业业务数据库 ——
    database_url: str = "sqlite:///data/recruitmatch.db"

    # —— 身份认证 ——
    jwt_secret: str = "dev-only-change-me-before-production"
    access_token_minutes: int = 30

    # —— RAG ——
    chunk_size: int = 500
    chunk_overlap: int = 100
    top_k: int = 5
    similarity_threshold: float = 0.35

    # —— Agent ——
    llm_temperature: float = 0.2
    max_agent_iterations: int = 3

    @classmethod
    def load(cls) -> "Settings":
        settings = cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url).strip(),
            chat_model=os.getenv("OPENAI_MODEL", cls.chat_model).strip(),
            embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", cls.embedding_model).strip(),
            data_dir=os.getenv("DATA_DIR", cls.data_dir).strip(),
            index_dir=os.getenv("INDEX_DIR", cls.index_dir).strip(),
            conversations_file=os.getenv("CONVERSATIONS_FILE", cls.conversations_file).strip(),
            database_url=os.getenv("DATABASE_URL", cls.database_url).strip(),
            jwt_secret=os.getenv("JWT_SECRET", cls.jwt_secret).strip(),
            access_token_minutes=_get_int("ACCESS_TOKEN_MINUTES", cls.access_token_minutes),
            chunk_size=_get_int("CHUNK_SIZE", cls.chunk_size),
            chunk_overlap=_get_int("CHUNK_OVERLAP", cls.chunk_overlap),
            top_k=_get_int("TOP_K", cls.top_k),
            similarity_threshold=_get_float("SIMILARITY_THRESHOLD", cls.similarity_threshold),
            llm_temperature=_get_float("LLM_TEMPERATURE", cls.llm_temperature),
            max_agent_iterations=_get_int("MAX_AGENT_ITERATIONS", cls.max_agent_iterations),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.api_key:
            raise ConfigError("缺少 OPENAI_API_KEY，请在 .env 中配置")
        if self.chunk_overlap >= self.chunk_size:
            raise ConfigError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE")
        if not (0.0 <= self.similarity_threshold <= 1.0):
            raise ConfigError("SIMILARITY_THRESHOLD 必须在 [0, 1] 区间")
        if self.top_k <= 0:
            raise ConfigError("TOP_K 必须大于 0")


# 供需要直接读取单个字段的旧代码/脚本使用
def get_settings() -> Settings:
    return Settings.load()
