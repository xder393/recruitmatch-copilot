"""统一日志：注入 request_id，格式统一，便于排查。"""

from __future__ import annotations

import contextvars
import logging
import sys
import json
import re
from opentelemetry import trace

# 每个请求一个 id，日志里自动带上
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


_EVENTS = frozenset(
    {
        "http_access",
        "internal_error",
        "audit_write_failed",
        "processing_database_unavailable",
        "processing_dispatch_unavailable",
        "maintenance_database_unavailable",
        "recovery_database_unavailable",
        "maintenance_dispatch_unavailable",
        "heartbeat_unavailable",
        "otel_exporter_diagnostic",
        "telemetry_probe_unavailable",
        "processing_task_failed",
        "maintenance_task_failed",
    }
)


class PrivateJsonFormatter(logging.Formatter):
    def format(self, record):
        event = record.msg if isinstance(record.msg, str) and record.msg in _EVENTS else "application_diagnostic"
        fields = {}
        if record.name == "uvicorn.access":
            event = "http_access"
            if isinstance(record.args, tuple) and len(record.args) == 5:
                status = record.args[4]
                if type(status) is int and 100 <= status <= 599:
                    fields["http.response.status_code"] = status
        status = getattr(record, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            fields["http.response.status_code"] = status
        if record.exc_info or event == "internal_error":
            fields["error.code"] = "internal_error"
        context = trace.get_current_span().get_span_context()
        request_id = request_id_var.get()
        return json.dumps(
            {
                "level": record.levelname if record.levelno in {10, 20, 30, 40, 50} else "INFO",
                "event": event,
                "request_id": request_id if re.fullmatch(r"[0-9a-f]{8,32}", request_id) else None,
                "trace_id": f"{context.trace_id:032x}" if context.is_valid else None,
                "span_id": f"{context.span_id:016x}" if context.is_valid else None,
                **fields,
            },
            separators=(",", ":"),
        )


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PrivateJsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "celery", "celery.task", "celery.redirected"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
