"""Structured JSON logging, per-conversation correlation ids, and a helper
for reporting whether LangSmith tracing is active.

LangSmith tracing itself is enabled by LangChain automatically when
``LANGSMITH_TRACING`` / ``LANGSMITH_API_KEY`` are set — no code needed beyond
this reporting flag; every graph run is then traced.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, TextIO

_LOGGER_NAME = "scheduling_agent"
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON with the active correlation id."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = _correlation_id.get()
        if cid is not None:
            payload["correlation_id"] = cid
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str | None = None, stream: TextIO | None = None) -> None:
    """Install the JSON formatter on the package logger (idempotent)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    logger.propagate = False


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def log_event(message: str, /, **fields: Any) -> None:
    """Emit a structured event with arbitrary extra fields."""
    get_logger().info(message, extra={"extra_fields": fields})


def set_correlation_id(cid: str | None) -> None:
    _correlation_id.set(cid)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def new_correlation_id(prefix: str = "conv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def tracing_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether LangSmith tracing is active (flag set AND an API key present)."""
    source: Mapping[str, str] = os.environ if env is None else env
    flag = source.get("LANGSMITH_TRACING", "").strip().lower() in {"1", "true", "yes"}
    return flag and bool(source.get("LANGSMITH_API_KEY"))
