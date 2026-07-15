"""Tests for the calendar client layer: FakeCalendarTools + HttpMcpCalendarTools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scheduling_agent.calendar import (
    FakeCalendarTools,
    HttpMcpCalendarTools,
    McpError,
)
from scheduling_agent.models import AvailabilityResult, ConflictResult, WriteResult

# --- FakeCalendarTools ---------------------------------------------------------


def test_fake_add_recurring_shows_busy_and_detects_conflict() -> None:
    cal = FakeCalendarTools()
    res = cal.add_recurring_event(
        "Standup", "20260803", "FREQ=WEEKLY;BYDAY=MO", time="091500", duration=30
    )
    assert res.success and res.event_id is not None

    avail = cal.get_availability("20260803", "20260803")
    assert len(avail.busy) == 1
    assert avail.busy[0].name == "Standup"

    # Overlapping slot (09:20-09:40) conflicts; non-overlapping (10:00) does not.
    assert cal.check_conflicts("20260803", "092000", 20).has_conflict
    assert not cal.check_conflicts("20260803", "100000", 20).has_conflict


def test_fake_add_recurring_rejects_bad_rrule() -> None:
    cal = FakeCalendarTools()
    res = cal.add_recurring_event("Bad", "20260803", "FREQ=HOURLY")
    assert not res.success and res.error is not None


def test_fake_add_event_is_untimed_all_day() -> None:
    cal = FakeCalendarTools()
    cal.add_event("Holiday", "20260704")
    avail = cal.get_availability("20260704", "20260704")
    assert avail.busy == []
    assert len(avail.all_day) == 1
    # Untimed events never conflict with a timed slot.
    assert not cal.check_conflicts("20260704", "090000", 60).has_conflict


def test_fake_update_and_delete() -> None:
    cal = FakeCalendarTools()
    eid = cal.add_recurring_event(
        "Sync", "20260803", "FREQ=DAILY", time="090000", duration=30
    ).event_id
    assert eid is not None

    upd = cal.update_event(eid, name="Renamed")
    assert upd.success
    assert cal.get_availability("20260803", "20260803").busy[0].name == "Renamed"

    assert cal.delete_event(eid).success
    assert cal.get_availability("20260803", "20260803").busy == []


def test_fake_update_delete_missing_event_errors() -> None:
    cal = FakeCalendarTools()
    assert not cal.update_event(999, name="x").success
    assert not cal.delete_event(999).success


def test_fake_list_events_in_range() -> None:
    cal = FakeCalendarTools()
    cal.add_recurring_event("A", "20260803", "FREQ=DAILY", time="090000")
    cal.add_recurring_event("B", "20260810", "FREQ=DAILY", time="090000")
    events = cal.list_events("20260801", "20260805")
    assert [e.name for e in events] == ["A"]


# --- HttpMcpCalendarTools (via httpx MockTransport) ----------------------------


def _client(handler: Any) -> HttpMcpCalendarTools:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return HttpMcpCalendarTools("https://ex/mcp.php", token="tok", client=http)


def test_http_get_availability_parses_and_sends_jsonrpc() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["token"] = request.headers.get("X-MCP-Token")
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "busy": [
                        {
                            "id": 1,
                            "name": "M",
                            "date": "20260803",
                            "time": "090000",
                            "duration": 30,
                        }
                    ],
                    "all_day": [],
                    "timezone": "GMT",
                },
            },
        )

    cal = _client(handler)
    result = cal.get_availability("20260803", "20260803")
    assert isinstance(result, AvailabilityResult)
    assert result.busy[0].name == "M"
    assert captured["token"] == "tok"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "get_availability"
    assert captured["body"]["params"]["arguments"] == {
        "start_date": "20260803",
        "end_date": "20260803",
    }


def test_http_check_conflicts_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "has_conflict": True,
                    "conflicts": [
                        {
                            "id": 5,
                            "name": "Busy",
                            "date": "20260803",
                            "time": "090000",
                            "duration": 60,
                        }
                    ],
                },
            },
        )

    result = _client(handler).check_conflicts("20260803", "091500", 30)
    assert isinstance(result, ConflictResult)
    assert result.has_conflict and result.conflicts[0].id == 5


def test_http_add_recurring_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"success": True, "event_id": 12, "cal_type": "weekly"},
            },
        )

    result = _client(handler).add_recurring_event(
        "S", "20260803", "FREQ=WEEKLY;BYDAY=MO"
    )
    assert isinstance(result, WriteResult)
    assert result.success and result.event_id == 12


def test_http_tool_error_result_becomes_failed_write() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"error": "MCP write access is not enabled"},
            },
        )

    result = _client(handler).delete_event(3)
    assert not result.success
    assert result.error is not None and "write access" in result.error


def test_http_jsonrpc_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32603, "message": "Unknown tool: bogus"},
            },
        )

    with pytest.raises(McpError, match="Unknown tool"):
        _client(handler).get_availability("20260803", "20260803")


def test_http_update_event_omits_none_fields() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["args"] = json.loads(request.content)["params"]["arguments"]
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"success": True, "event_id": 7},
            },
        )

    _client(handler).update_event(7, name="New")
    assert captured["args"] == {"event_id": 7, "name": "New"}


def test_fake_rejects_empty_name() -> None:
    cal = FakeCalendarTools()
    assert not cal.add_event("  ", "20260803").success
    assert not cal.add_recurring_event("", "20260803", "FREQ=DAILY").success


def test_http_list_events_parses_events_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "events": [
                        {
                            "id": 1,
                            "name": "M",
                            "date": "20260803",
                            "time": "090000",
                            "duration": 30,
                            "description": "d",
                            "location": "l",
                            "priority": 5,
                        }
                    ]
                },
            },
        )

    events = _client(handler).list_events("20260801", "20260805")
    assert len(events) == 1 and events[0].name == "M"


def test_http_add_event_sends_arguments() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["args"] = json.loads(request.content)["params"]["arguments"]
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"success": True, "event_id": 3},
            },
        )

    res = _client(handler).add_event("Lunch", "20260804", duration=45)
    assert res.success and res.event_id == 3
    assert captured["args"]["name"] == "Lunch"
    assert captured["args"]["duration"] == 45
