"""A stalled fork child must fail within a deadline and be reaped."""

import time

import pytest

from tests.observability.fork_probe import run_probe


@pytest.mark.parametrize("mode", ["stall", "exit-stall"])
def test_stalled_child_is_killed_and_reaped_before_returning_failure(mode):
    started = time.monotonic()
    result = run_probe(mode, timeout=3)
    assert result["error"] == "fork_child_deadline_exceeded"
    assert result["child_reaped"] is True
    assert time.monotonic() - started < 3


def test_crashed_child_is_reaped_and_reported_as_failure():
    result = run_probe("crash")
    assert result["error"] == "fork_child_failed"
    assert result["child_reaped"] is True


def test_supervisor_deadline_also_reaps_its_stalled_child():
    started = time.monotonic()
    result = run_probe("supervisor-stall", timeout=1)
    assert result["error"] == "fork_supervisor_deadline_exceeded"
    assert result["child_reaped"] is True
    assert time.monotonic() - started < 4
