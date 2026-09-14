from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, get_ident

import pytest

from app.domain.artifacts import ArtifactOwnerType
from app.processing.outcomes import ClaimedLease, LeaseOwnershipLost


def test_renewal_uses_independent_uows_and_loss_stops_publication():
    from app.processing.renewal import LeaseRenewer

    observed = Event()
    calls = []
    lease = ClaimedLease("t", ArtifactOwnerType.RESUME, "r", "a", "worker", 1, datetime.now(timezone.utc))

    class Uow:
        @property
        def leases(self):
            return self

        def renew(self, current, *, duration):
            calls.append((get_ident(), current, duration))
            observed.set()
            return False

        def commit(self):
            pytest.fail("a lost renewal must not commit")

    @contextmanager
    def factory():
        yield Uow()

    with LeaseRenewer(factory, lease, duration=timedelta(milliseconds=30)) as renewer:
        assert observed.wait(2)
        assert renewer.lost.wait(2)
        with pytest.raises(LeaseOwnershipLost):
            renewer.ensure_owned()
    assert len(calls) == 1
    assert calls[0][0] != get_ident()
    assert calls[0][1:] == (lease, timedelta(milliseconds=30))


def test_shutdown_does_not_wait_indefinitely_for_blocked_database():
    from app.processing.renewal import LeaseRenewer
    from time import monotonic

    started, release = Event(), Event()
    lease = ClaimedLease("t", ArtifactOwnerType.RESUME, "r", "a", "worker", 1, datetime.now(timezone.utc))

    class Uow:
        @property
        def leases(self):
            return self

        def renew(self, current, *, duration):
            started.set()
            release.wait(3)
            return True

        def commit(self):
            pass

    @contextmanager
    def factory():
        yield Uow()

    try:
        before = monotonic()
        with LeaseRenewer(factory, lease, duration=timedelta(milliseconds=30), shutdown_timeout=0.05):
            assert started.wait(1)
        assert monotonic() - before < 1
    finally:
        release.set()


def test_successful_heartbeats_commit_each_independent_uow():
    from app.processing.renewal import LeaseRenewer

    committed = Event()
    transactions = []
    lease = ClaimedLease("t", ArtifactOwnerType.RESUME, "r", "a", "worker", 1, datetime.now(timezone.utc))

    class Uow:
        @property
        def leases(self):
            return self

        def renew(self, current, *, duration):
            assert current == lease and duration == timedelta(milliseconds=30)
            return True

        def commit(self):
            transactions.append(self)
            if len(transactions) == 2:
                committed.set()

    @contextmanager
    def factory():
        yield Uow()

    with LeaseRenewer(factory, lease, duration=timedelta(milliseconds=30)) as renewer:
        assert committed.wait(2)
        renewer.ensure_owned()
    assert len(transactions) >= 2
    assert len({id(item) for item in transactions}) == len(transactions)
