"""The real Beat adapter scans directly without constructing a Worker processor."""

from celery import Celery


def test_beat_tick_scans_directly_and_paces_maintenance(monkeypatch):
    from app.tasks.beat import RecoveryScheduler
    from app.tasks import celery_app as worker_module

    def forbidden(*args, **kwargs):
        raise AssertionError("Beat must never build processing dependencies")

    monkeypatch.setattr(worker_module, "build_worker_dependencies", forbidden)
    events = []

    class Scanner:
        def run_once(self):
            events.append("scan")

    class Heartbeats:
        def pulse_beat(self):
            events.append("heartbeat")

    now = [0.0]
    scheduler = RecoveryScheduler(
        app=Celery("test"),
        scanner=Scanner(),
        heartbeats=Heartbeats(),
        maintenance_dispatch=lambda: events.append("maintenance"),
        clock=lambda: now[0],
    )
    assert scheduler.tick() == 10
    assert events == ["scan", "maintenance", "heartbeat"]
    now[0] = 10
    scheduler.tick()
    assert events[-2:] == ["scan", "heartbeat"]
    now[0] = 60
    scheduler.tick()
    assert events.count("maintenance") == 2


def test_worker_timer_stops_before_removing_own_heartbeat():
    from app.tasks.beat import WorkerHeartbeatStep

    events = []

    class Timer:
        def call_repeatedly(self, interval, callback):
            self.callback = callback
            return self

        def cancel(self):
            events.append("cancel")

    class Heartbeats:
        def pulse_worker(self, identity):
            events.append(("pulse", identity))

        def remove_worker(self, identity):
            events.append(("remove", identity))

    class Worker:
        timer = Timer()
        steps = []

    step = WorkerHeartbeatStep(Worker())
    assert step.include(Worker()) is True
    assert Worker.steps == [step]
    step.heartbeats = Heartbeats()
    step.start(Worker())
    step.stop(Worker())
    Worker.timer.callback()
    assert len(events) == 3
    assert events[0][0] == "pulse" and events[1] == "cancel" and events[2][0] == "remove"
    assert events[0][1] == events[2][1]


def test_broker_connection_failure_does_not_wait_for_implicit_kombu_retries():
    from time import monotonic
    import pytest
    from app.tasks.celery_app import celery_app
    from app.tasks.dispatcher import CeleryTaskDispatcher
    from app.processing.recovery import RecoveryDispatchUnavailable

    app = Celery("unavailable", broker="redis://127.0.0.1:1/0")
    for name in ("broker_transport_options", "broker_connection_timeout", "broker_pool_limit", "task_publish_retry"):
        app.conf[name] = celery_app.conf[name]

    def never_executed():
        return None

    task = app.task(name="synthetic.never_executed")(never_executed)
    start = monotonic()
    with pytest.raises(RecoveryDispatchUnavailable):
        CeleryTaskDispatcher._publish(task, ())
    assert monotonic() - start < 1.5
    app.close()
