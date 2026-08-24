"""Bounded domain events that do not depend on a telemetry SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class DomainEvent:
    name: str
    attributes: Mapping[str, str | int | float | bool]


class DomainEventRecorder(Protocol):
    def record(self, event: DomainEvent) -> None:
        raise NotImplementedError


class NoopDomainEventRecorder:
    def record(self, event: DomainEvent) -> None:
        return None
