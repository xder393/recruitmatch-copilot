"""统一日志：注入 request_id，格式统一，便于排查。"""

from __future__ import annotations

import contextvars
import logging
import sys

# 每个请求一个 id，日志里自动带上
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
