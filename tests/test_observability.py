"""Tests for structured logging, correlation ids, and the tracing flag."""

from __future__ import annotations

import io
import json

from scheduling_agent.observability import (
    configure_logging,
    get_correlation_id,
    get_logger,
    log_event,
    new_correlation_id,
    set_correlation_id,
    tracing_enabled,
)


def _capture() -> io.StringIO:
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    return buf


def test_log_output_is_json_with_level_and_message() -> None:
    buf = _capture()
    get_logger().info("hello world")
    record = json.loads(buf.getvalue().strip())
    assert record["level"] == "INFO"
    assert record["message"] == "hello world"
    assert "correlation_id" not in record


def test_correlation_id_is_attached() -> None:
    buf = _capture()
    set_correlation_id("conv-abc123")
    get_logger().info("with cid")
    record = json.loads(buf.getvalue().strip())
    assert record["correlation_id"] == "conv-abc123"
    set_correlation_id(None)


def test_log_event_includes_extra_fields() -> None:
    buf = _capture()
    log_event("turn", request="book lunch", turn=3)
    record = json.loads(buf.getvalue().strip())
    assert record["message"] == "turn"
    assert record["request"] == "book lunch"
    assert record["turn"] == 3


def test_get_correlation_id_roundtrip() -> None:
    set_correlation_id("conv-xyz")
    assert get_correlation_id() == "conv-xyz"


def test_new_correlation_id_is_unique_and_prefixed() -> None:
    a = new_correlation_id("conv")
    b = new_correlation_id("conv")
    assert a.startswith("conv-") and b.startswith("conv-")
    assert a != b


def test_tracing_enabled_requires_flag_and_key() -> None:
    assert tracing_enabled({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k"})
    assert not tracing_enabled({"LANGSMITH_TRACING": "true"})  # no key
    assert not tracing_enabled({"LANGSMITH_API_KEY": "k"})  # flag off
    assert not tracing_enabled({})
    assert isinstance(tracing_enabled(), bool)  # default env branch (os.environ)


def test_exception_is_logged() -> None:
    buf = _capture()
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger().exception("failed")
    record = json.loads(buf.getvalue().strip())
    assert record["message"] == "failed"
    assert "ValueError" in record["exc"]
