"""Lease heartbeats with independent transactions and bounded shutdown."""

from datetime import timedelta
from threading import Event, Thread

from app.processing.outcomes import ClaimedLease, LeaseOwnershipLost
from app.repositories.unit_of_work import UnitOfWorkFactory


class LeaseRenewer:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, lease: ClaimedLease, *, duration: timedelta, shutdown_timeout: float = 1.0
    ):
        if duration.total_seconds() <= 0 or shutdown_timeout < 0:
            raise ValueError("invalid lease renewal timing")
        self.uow_factory = uow_factory
        self.lease = lease
        self.duration = duration
        self.shutdown_timeout = shutdown_timeout
        self.lost = Event()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="processing-lease-renewal", daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        self._thread.join(timeout=self.shutdown_timeout)

    def ensure_owned(self) -> None:
        if self.lost.is_set():
            raise LeaseOwnershipLost("processing_lease_lost")

    def _run(self) -> None:
        while not self._stop.wait(self.duration.total_seconds() / 3):
            try:
                with self.uow_factory() as uow:
                    if not uow.leases.renew(self.lease, duration=self.duration):
                        self.lost.set()
                        return
                    uow.commit()
            except Exception:
                # Unknown renewal outcome cannot authorize further publication.
                self.lost.set()
                return
