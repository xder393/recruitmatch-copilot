"""Bounded recovery orchestration: durable reservations precede at-least-once dispatch."""

from dataclasses import dataclass
from typing import Protocol

from app.tasks.dispatcher import TaskDispatcher


class RecoveryDispatchUnavailable(Exception):
    """Known broker transport failure; never contains connection details."""


class RecoveryStore(Protocol):
    def candidates(self) -> list: ...
    def reserve(self, candidate): ...
    def dispatch_failed(self, reservation) -> None: ...


@dataclass
class RecoveryReport:
    selected: int = 0
    dispatched: int = 0
    dispatch_failures: int = 0


class RecoveryScanner:
    def __init__(self, repository: RecoveryStore, dispatcher: TaskDispatcher):
        self.repository = repository
        self.dispatcher = dispatcher

    def run_once(self) -> RecoveryReport:
        # No application-clock argument: selection and guards sample PostgreSQL.
        candidates = self.repository.candidates()
        report = RecoveryReport(selected=len(candidates))
        for candidate in candidates:
            reservation = self.repository.reserve(candidate)
            if reservation is None:
                continue
            try:
                dispatch = (
                    self.dispatcher.dispatch_resume
                    if reservation.source_type == "resume"
                    else self.dispatcher.dispatch_knowledge
                )
                dispatch(reservation.tenant_id, reservation.source_id)
                report.dispatched += 1
            except RecoveryDispatchUnavailable:
                self.repository.dispatch_failed(reservation)
                report.dispatch_failures += 1
        return report
