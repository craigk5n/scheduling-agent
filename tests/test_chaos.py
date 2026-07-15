"""Chaos tests: the k5n-mcp-hub fault modes must surface as a clean McpError,
never an unexpected crash, and the REPL must degrade gracefully."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from scheduling_agent.calendar import (
    FakeCalendarTools,
    HttpMcpCalendarTools,
    McpError,
)
from scheduling_agent.cli import run_repl
from scheduling_agent.graph import build_agent
from scheduling_agent.models import AvailabilityResult, ConflictResult, WriteResult


def _client(handler: Any) -> HttpMcpCalendarTools:
    transport = httpx.MockTransport(handler)
    return HttpMcpCalendarTools(
        "https://ex/mcp.php", token="t", client=httpx.Client(transport=transport)
    )


def test_timeout_becomes_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(McpError, match="failed"):
        _client(handler).get_availability("20260803", "20260803")


def test_http_504_becomes_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="gateway timeout")

    with pytest.raises(McpError, match="504"):
        _client(handler).get_availability("20260803", "20260803")


def test_malformed_json_becomes_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"{bad json", headers={"Content-Type": "application/json"}
        )

    with pytest.raises(McpError, match="malformed"):
        _client(handler).get_availability("20260803", "20260803")


def test_non_object_response_becomes_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(McpError):
        _client(handler).get_availability("20260803", "20260803")


def test_dropped_stream_becomes_mcp_error() -> None:
    # k5n-mcp-hub "SSE Interrupt": an event-stream body that is not JSON.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"event: error\ndata: boom\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

    with pytest.raises(McpError):
        _client(handler).check_conflicts("20260803", "090000", 30)


def test_missing_result_becomes_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1})

    with pytest.raises(McpError):
        _client(handler).get_availability("20260803", "20260803")


class _FaultyTools(FakeCalendarTools):
    """A calendar whose reads fail, to test the REPL's graceful degradation."""

    def check_conflicts(self, date: str, time: str, duration: int) -> ConflictResult:
        raise McpError("simulated MCP fault")

    def get_availability(self, start_date: str, end_date: str) -> AvailabilityResult:
        raise McpError("simulated MCP fault")


def test_repl_survives_tool_fault() -> None:
    proposal = {
        "action": "create",
        "title": "Lunch",
        "timezone": "America/New_York",
        "start": "2026-08-04T12:00:00-04:00",
        "duration_minutes": 60,
    }
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=str(proposal).replace("'", '"'))])
    )
    agent = build_agent(model, _FaultyTools(), MemorySaver())

    out: list[str] = []
    inputs = iter(["book lunch", "quit"])

    def read(_prompt: str = "") -> str:
        return next(inputs)

    run_repl(agent, read=read, write=out.append)

    # The fault is reported, and the loop keeps going to accept 'quit'.
    assert any("error" in line.lower() or "fault" in line.lower() for line in out)


def test_write_result_unaffected() -> None:
    # Sanity: a normal client still returns a WriteResult (no false positives).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"success": True, "event_id": 1},
            },
        )

    result = _client(handler).delete_event(1)
    assert isinstance(result, WriteResult) and result.success
