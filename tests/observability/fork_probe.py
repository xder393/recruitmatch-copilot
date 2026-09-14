"""Fresh-process fork diagnostics with bounded supervisor and child lifetimes."""

import json
import os
import select
import signal
import subprocess
import sys
import time
import warnings
from pathlib import Path


def run_probe(mode, *, timeout=15):
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.observability.fork_probe", mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=3)
            raise AssertionError("fork_supervisor_cleanup_deadline_exceeded") from None
        assert process.returncode == 0 and not stderr, "fork_supervisor_cleanup_failed"
        result = json.loads(stdout)
        assert result["child_reaped"], "fork_supervisor_left_child"
        result["error"] = "fork_supervisor_deadline_exceeded"
        return result
    assert process.returncode == 0 and not stderr, "fork_supervisor_failed"
    return json.loads(stdout)


def fork_result(callback, *, seconds=1):
    read_fd, write_fd = os.pipe()
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", DeprecationWarning)
        child = os.fork()
    if child == 0:
        os.close(read_fd)
        status = 1
        try:
            result = callback()
            os.write(write_fd, json.dumps(result).encode())
            status = 0
        finally:
            os.close(write_fd)
            os._exit(status)
    os.close(write_fd)
    reaped = False
    outcome = {}
    try:
        deadline = time.monotonic() + seconds
        if not select.select([read_fd], [], [], max(0, deadline - time.monotonic()))[0]:
            raise TimeoutError
        payload = os.read(read_fd, 4096)
        while True:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid:
                reaped = True
                break
            if time.monotonic() >= deadline:
                raise TimeoutError
            time.sleep(0.01)
        if status != 0 or not payload:
            outcome = {"error": "fork_child_failed"}
        else:
            outcome = {"result": json.loads(payload)}
    except TimeoutError:
        outcome = {"error": "fork_child_deadline_exceeded"}
    finally:
        os.close(read_fd)
        if not reaped:
            os.kill(child, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if os.waitpid(child, os.WNOHANG)[0]:
                    reaped = True
                    break
                time.sleep(0.01)
        assert reaped, "fork_child_reaping_deadline_exceeded"
    assert all(
        warning.category is DeprecationWarning
        and "multi-threaded" in str(warning.message)
        and "fork()" in str(warning.message)
        for warning in observed
    ), "unexpected_fork_warning"
    return {**outcome, "child_reaped": reaped, "fork_warning_count": len(observed)}


def main(mode):
    def terminate(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGTERM, terminate)
    if mode == "stall":
        return fork_result(lambda: time.sleep(60))
    if mode == "supervisor-stall":
        return fork_result(lambda: time.sleep(60), seconds=30)
    if mode == "crash":
        return fork_result(lambda: os._exit(7))
    if mode == "exit-stall":
        # Deliver and close the pipe, then stall: the waitpid path needs its
        # own deadline even after a complete result was received.
        os._exit = lambda status: time.sleep(60)
        return fork_result(lambda: {"delivered": True})
    if mode == "disabled":
        from app.config import Settings
        from app.observability.otel import configure_observability, shutdown_observability

        owner = object()
        inherited = configure_observability(Settings(), owner=owner)
        try:
            return fork_result(
                lambda: {"new_runtime": configure_observability(Settings(), owner=owner) is not inherited}
            )
        finally:
            shutdown_observability(owner=owner)
    if mode == "enabled":
        return enabled_worker_probe()
    raise AssertionError("unknown_probe")


def enabled_worker_probe():
    import threading
    from celery import signals
    from app.config import Settings
    from app.observability.events import record
    from app.observability.instrumentation import activate
    from app.observability.otel import Observability
    from app.tasks import celery_app as worker
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    sinks = []

    def configured(*args, **kwargs):
        exporter = InMemorySpanExporter()
        runtime = Observability(
            Settings(telemetry_enabled=True), span_exporter=exporter, metric_reader=InMemoryMetricReader()
        )
        sinks.append((runtime, exporter))
        return runtime

    worker.configure_observability = configured
    parent = configured()

    def child_work():
        responses = signals.worker_process_init.send(sender=None)
        assert not any(isinstance(result, Exception) for _, result in responses), "worker_init_failed"
        record("task.started", {"task.type": "resume"})
        runtime, exporter = sinks[-1]
        runtime.force_flush()
        identity = exporter.get_finished_spans()[-1].resource.attributes["service.instance.id"]
        runtime.shutdown()
        return {"new_runtime": runtime is not parent, "identity": identity}

    try:
        with activate(parent):
            record("task.started", {"task.type": "resume"})
            parent.force_flush()
            parent_id = sinks[0][1].get_finished_spans()[0].resource.attributes["service.instance.id"]
            python_threads = [thread.name for thread in threading.enumerate()]
            native_threads = len(list(Path("/proc/self/task").iterdir()))
            outcome = fork_result(child_work, seconds=5)
            return {
                **outcome,
                "parent_identity": parent_id,
                "python_threads": python_threads,
                "native_threads": native_threads,
            }
    finally:
        parent.shutdown()


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])))
