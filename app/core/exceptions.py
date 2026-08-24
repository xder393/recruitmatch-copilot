"""统一异常体系：每种失败场景一个明确类型，避免裸 except 吞错。"""

from __future__ import annotations


class AppError(Exception):
    """业务异常基类，携带 HTTP 状态码与错误码。"""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class ConfigError(AppError):
    code = "config_error"
    status_code = 500


class LLMError(AppError):
    code = "llm_error"
    status_code = 502


class EmbeddingError(AppError):
    code = "embedding_error"
    status_code = 500


class IngestionError(AppError):
    code = "ingestion_error"
    status_code = 400


class UnsupportedFileError(IngestionError):
    code = "unsupported_file"


class EmptyKnowledgeBaseError(AppError):
    code = "empty_knowledge_base"
    status_code = 400


class DocumentNotFoundError(AppError):
    code = "document_not_found"
    status_code = 404


class RetrievalError(AppError):
    code = "retrieval_error"
    status_code = 500


class AuthenticationError(AppError):
    code = "unauthorized"
    status_code = 401


class AuthorizationError(AppError):
    code = "forbidden"
    status_code = 403


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


class ResourceNotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ToolExecutionError(AppError):
    code = "tool_error"
    status_code = 500
