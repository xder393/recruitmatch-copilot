"""Pinned Celery 5.6.3 compatibility for direct Worker control diagnostics.

Celery's shutdown handler bypasses logging and writes through safe_say to the
original stdout descriptor. Bind only that utility at actual Worker startup.
This is a reviewed compatibility binding, not a supported Celery logging API;
recheck the real prefork/SIGTERM tests whenever Celery is upgraded.
"""

import sys
from typing import Callable


_CONTROL_RECORD = (
    b'{"level":"INFO","event":"application_diagnostic","request_id":null,"trace_id":null,"span_id":null}\n'
)
_original_write: Callable[[int, bytes], int] | None = None


def _write_control_diagnostic(message, f=sys.__stderr__):
    # Signal context: no logger/formatter locks, context reads, message parsing,
    # JSON serialization, or retry loop. Keep Celery's original descriptor/write.
    try:
        if _original_write is not None and hasattr(f, "fileno"):
            descriptor = f.fileno()
            if descriptor is not None:
                _original_write(descriptor, _CONTROL_RECORD)
    except (OSError, ValueError):
        # An unavailable output stream must not interrupt the shutdown callback.
        pass


def install_worker_control_logging():
    from celery.apps import worker  # type: ignore[import-untyped]

    global _original_write
    if worker.safe_say is _write_control_diagnostic:
        return
    # Deliberate pinned compatibility dependency: this is the unpatched os.write
    # primitive already selected by Celery for its signal-safe diagnostic path.
    _original_write = worker._original_os_write
    worker.safe_say = _write_control_diagnostic
