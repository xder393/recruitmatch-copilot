"""Provider-independent structured model contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ModelRequest(Generic[T]):
    operation: str
    prompt_version: str
    system: str
    user: str
    schema: Type[T]


@dataclass(frozen=True)
class ModelResponse(Generic[T]):
    value: T
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: float
    attempts: int = 1


class StructuredModel(Protocol):
    def generate(self, request: ModelRequest[T]) -> ModelResponse[T]: ...
