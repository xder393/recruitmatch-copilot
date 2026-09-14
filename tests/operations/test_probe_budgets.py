"""Real clients must stop waiting on a peer that accepts TCP but never answers."""

from contextlib import contextmanager
import socket
from threading import Event, Thread
from time import monotonic

import pytest


@contextmanager
def silent_peer():
    stop = Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(4)
    port = listener.getsockname()[1]

    def serve():
        try:
            connection, _ = listener.accept()
        except (TimeoutError, OSError):
            return
        with connection:
            stop.wait(5)

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("dependency", ["postgres", "redis", "bucket"])
def test_actual_driver_waits_are_bounded_without_background_request_threads(dependency):
    from app.operations.probes import PostgreSQLProbe, RedisProbe, BucketProbe
    from app.artifacts.s3 import S3Settings

    with silent_peer() as port:
        if dependency == "postgres":
            probe = PostgreSQLProbe(f"postgresql+psycopg://fake:fake@127.0.0.1:{port}/fake")
        elif dependency == "redis":
            probe = RedisProbe(f"redis://127.0.0.1:{port}/0")
        else:
            probe = BucketProbe(S3Settings(f"http://127.0.0.1:{port}", "synthetic", "synthetic"))
        start = monotonic()
        result = probe.check()
        duration = monotonic() - start
        assert result == (
            dict(database="unavailable", schema="unavailable", vector="unavailable")
            if dependency == "postgres"
            else "unavailable"
        )
        assert duration < (4 if dependency == "postgres" else 2)


def test_bucket_probe_closes_real_client_after_failure(monkeypatch):
    from app.operations import probes
    from app.artifacts.s3 import S3Settings

    original = probes.boto3.client
    closed = []

    def recording_client(*args, **kwargs):
        client = original(*args, **kwargs)
        close = client.close

        def recording_close():
            close()
            closed.append(True)

        client.close = recording_close
        return client

    monkeypatch.setattr(probes.boto3, "client", recording_client)
    assert probes.BucketProbe(S3Settings("http://127.0.0.1:1", "synthetic", "synthetic")).check() == "unavailable"
    assert closed == [True]
