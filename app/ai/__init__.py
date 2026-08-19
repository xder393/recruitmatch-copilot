"""RecruitMatch AI contracts and adapters."""

from app.ai.contracts import ModelRequest, ModelResponse, StructuredModel
from app.ai.gateway import ModelGatewayError, OpenAICompatibleGateway

__all__ = [
    "ModelGatewayError",
    "ModelRequest",
    "ModelResponse",
    "OpenAICompatibleGateway",
    "StructuredModel",
]
