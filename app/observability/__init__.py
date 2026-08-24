"""Domain-observability contracts."""

from app.observability.events import DomainEvent, DomainEventRecorder, NoopDomainEventRecorder

__all__ = ["DomainEvent", "DomainEventRecorder", "NoopDomainEventRecorder"]
