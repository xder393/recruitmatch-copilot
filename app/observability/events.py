"""Bounded domain events that do not depend on a telemetry SDK."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractContextManager, nullcontext
from types import MappingProxyType
from typing import Mapping, Protocol
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from inspect import iscoroutinefunction
import sys


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


_NOOP = NoopDomainEventRecorder()
_recorder: ContextVar[DomainEventRecorder] = ContextVar("event_recorder", default=_NOOP)
_continuation: ContextVar = ContextVar("event_continuation", default=lambda fresh: nullcontext)


@contextmanager
def bind_recorder(recorder: DomainEventRecorder, continuation_factory=None):
    token = _recorder.set(recorder)
    continuation_token = _continuation.set(continuation_factory or (lambda fresh: nullcontext))
    try:
        yield
    finally:
        _recorder.reset(token)
        _continuation.reset(continuation_token)


def continuation(*, fresh: bool = False):
    """Capture a safe continuation factory without importing a tracing SDK."""
    return _continuation.get()(fresh)


def record(name: str, attributes: Mapping[str, str | int | float | bool] | None = None, value: int | float = 1) -> None:
    try:
        _recorder.get().record(DomainEvent(name, attributes or {}, value))
    except Exception:
        pass


@contextmanager
def operation(name: str, attributes: Mapping[str, str | int | float | bool] | None = None):
    try:
        manager = _recorder.get().operation(name, attributes)
        manager.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    except BaseException:
        try:
            manager.__exit__(*sys.exc_info())
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


def observed(name: str, attributes: Mapping[str, str | int | float | bool] | None = None):
    """Static operation names only; arguments and return values never reach telemetry."""

    def decorate(function):
        if iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with operation(name, attributes):
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with operation(name, attributes):
                return function(*args, **kwargs)

        return synchronous

    return decorate
