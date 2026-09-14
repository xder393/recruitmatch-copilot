"""Bounded domain events that do not depend on a telemetry SDK."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractContextManager, nullcontext
from types import MappingProxyType
from typing import Mapping, Protocol


@dataclass(frozen=True)
class DomainEvent:
    name: str
    attributes: Mapping[str, str | int | float | bool]
    value: int | float = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


class DomainEventRecorder(Protocol):
    def record(self, event: DomainEvent) -> None:
        raise NotImplementedError

    def operation(
        self, name: str, attributes: Mapping[str, str | int | float | bool] | None = None
    ) -> AbstractContextManager[None]: ...


class NoopDomainEventRecorder:
    def record(self, event: DomainEvent) -> None:
        return None

    def operation(
        self, name: str, attributes: Mapping[str, str | int | float | bool] | None = None
    ) -> AbstractContextManager[None]:
        return nullcontext()
