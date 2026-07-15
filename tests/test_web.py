"""Tests for the FastAPI web UI (via TestClient, fake model + fake tools)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.graph import build_agent
from scheduling_agent.web import create_app

STANDUP = {
    "action": "create_recurring",
    "title": "Team Standup",
    "timezone": "America/New_York",
    "start": "2026-08-03T09:15:00-04:00",
    "duration_minutes": 15,
    "recurrence": {"freq": "WEEKLY", "by_day": ["MO"]},
}


def _client(*proposals: dict[str, Any], tools: FakeCalendarTools) -> TestClient:
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(p)) for p in proposals])
    )
    return TestClient(create_app(build_agent(model, tools, MemorySaver())))


def test_index_serves_html() -> None:
    client = _client(STANDUP, tools=FakeCalendarTools())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Scheduling Agent" in resp.text


def test_schedule_then_approve_creates_event() -> None:
    tools = FakeCalendarTools()
    client = _client(STANDUP, tools=tools)

    r1 = client.post("/schedule", json={"request": "set up standup"}).json()
    assert r1["status"] == "needs_approval"
    assert "Team Standup" in r1["summary"]
    thread = r1["thread_id"]

    r2 = client.post(
        "/approve", json={"thread_id": thread, "decision": "approve"}
    ).json()
    assert r2["status"] == "done"
    assert "Done" in r2["response"]
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Team Standup"


def test_query_returns_done_without_approval() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Existing", "20260803", "FREQ=DAILY", time="100000")
    query = {
        "action": "query",
        "title": "",
        "timezone": "America/New_York",
        "start": "2026-08-03T00:00:00-04:00",
    }
    client = _client(query, tools=tools)
    r = client.post("/schedule", json={"request": "my week?"}).json()
    assert r["status"] == "done"
    assert "Existing" in r["response"]


def test_reject_then_replan_then_approve() -> None:
    first = {**STANDUP, "title": "Wrong"}
    second = {**STANDUP, "title": "Right"}
    tools = FakeCalendarTools()
    client = _client(first, second, tools=tools)

    r1 = client.post("/schedule", json={"request": "standup"}).json()
    thread = r1["thread_id"]
    r2 = client.post(
        "/approve",
        json={"thread_id": thread, "decision": "reject", "feedback": "call it Right"},
    ).json()
    assert r2["status"] == "needs_approval"
    assert "Right" in r2["summary"]
    r3 = client.post(
        "/approve", json={"thread_id": thread, "decision": "approve"}
    ).json()
    assert r3["status"] == "done"
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Right"
