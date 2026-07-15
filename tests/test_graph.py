"""Tests for the LangGraph scheduling agent: HITL interrupt, replan, query,
error handling, and resume-after-restart via the SQLite checkpointer."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.graph import build_agent

STANDUP = {
    "action": "create_recurring",
    "title": "Team Standup",
    "timezone": "America/New_York",
    "start": "2026-08-03T09:15:00-04:00",
    "duration_minutes": 15,
    "recurrence": {"freq": "WEEKLY", "by_day": ["MO", "WE", "FR"]},
}


def _model(*proposals: dict[str, Any]) -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(p)) for p in proposals])
    )


def _cfg(thread: str = "t1") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


def test_happy_path_creates_after_approval() -> None:
    tools = FakeCalendarTools()
    app = build_agent(_model(STANDUP), tools, MemorySaver())

    paused = app.invoke({"request": "set up our standup"}, _cfg())
    assert "__interrupt__" in paused
    assert "Team Standup" in paused["__interrupt__"][0].value["summary"]

    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    busy = tools.get_availability("20260803", "20260803").busy
    assert [b.name for b in busy] == ["Team Standup"]


def test_reject_then_replan_then_approve() -> None:
    first = {**STANDUP, "title": "Wrong Name"}
    second = {**STANDUP, "title": "Right Name"}
    tools = FakeCalendarTools()
    app = build_agent(_model(first, second), tools, MemorySaver())

    app.invoke({"request": "standup"}, _cfg())
    replanned = app.invoke(
        Command(resume={"decision": "reject", "feedback": "call it Right Name"}),
        _cfg(),
    )
    # Rejection loops back and re-plans, producing a fresh approval interrupt.
    assert "__interrupt__" in replanned
    assert "Right Name" in replanned["__interrupt__"][0].value["summary"]

    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Right Name"


def test_query_skips_approval_and_lists_events() -> None:
    tools = FakeCalendarTools()
    tools.add_event("Existing", "20260803", time="100000")
    query = {
        "action": "query",
        "title": "",
        "timezone": "America/New_York",
        "start": "2026-08-03T00:00:00-04:00",
    }
    app = build_agent(_model(query), tools, MemorySaver())

    result = app.invoke({"request": "what does my week look like"}, _cfg())
    assert "__interrupt__" not in result
    assert "Existing" in result["response"]
    # Times come from list_events (local frame), rendered as HH:MM.
    assert "10:00" in result["response"]


def test_query_honors_range_end_window() -> None:
    # An event inside the requested window shows; one past it does not.
    tools = FakeCalendarTools()
    tools.add_event("In Window", "20260720", time="120000")
    tools.add_event("Too Far", "20260901", time="120000")
    query = {
        "action": "query",
        "title": "",
        "timezone": "America/New_York",
        "start": "2026-07-15T00:00:00-04:00",
        "range_end": "2026-08-14",
    }
    app = build_agent(_model(query), tools, MemorySaver())

    result = app.invoke({"request": "events for the next 30 days"}, _cfg())
    assert "In Window" in result["response"]
    assert "Too Far" not in result["response"]


def test_query_with_no_events_reports_empty() -> None:
    query = {
        "action": "query",
        "title": "",
        "timezone": "America/New_York",
        "start": "2026-07-15T00:00:00-04:00",
    }
    app = build_agent(_model(query), FakeCalendarTools(), MemorySaver())
    result = app.invoke({"request": "anything coming up?"}, _cfg())
    assert "No events" in result["response"]


def test_invalid_recurrence_errors_without_write() -> None:
    bad = {**STANDUP, "recurrence": {"freq": "MONTHLY", "by_month_day": [32]}}
    tools = FakeCalendarTools()
    app = build_agent(_model(bad), tools, MemorySaver())

    result = app.invoke({"request": "monthly on the 32nd"}, _cfg())
    assert "__interrupt__" not in result
    assert "couldn't" in result["response"].lower()
    assert tools.get_availability("20260803", "20260803").busy == []


def test_create_oneoff_flow() -> None:
    tools = FakeCalendarTools()
    create = {
        "action": "create",
        "title": "Lunch with Dana",
        "timezone": "America/New_York",
        "start": "2026-08-04T12:00:00-04:00",
        "duration_minutes": 60,
        "location": "Cafe",
    }
    app = build_agent(_model(create), tools, MemorySaver())
    app.invoke({"request": "lunch tuesday"}, _cfg())
    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    # The one-off now keeps its time: 12:00-04:00 -> 16:00 GMT, a busy block.
    avail = tools.get_availability("20260804", "20260804")
    assert len(avail.busy) == 1
    assert avail.busy[0].time == "160000"
    assert avail.all_day == []


def test_update_event_flow_with_string_decision() -> None:
    tools = FakeCalendarTools()
    eid = tools.add_recurring_event(
        "Old", "20260803", "FREQ=DAILY", time="090000"
    ).event_id
    update = {
        "action": "update",
        "title": "New Name",
        "timezone": "America/New_York",
        "start": "2026-08-03T09:00:00-04:00",
        "target_event_id": eid,
    }
    app = build_agent(_model(update), tools, MemorySaver())

    app.invoke({"request": "rename it"}, _cfg())
    # Resume with a bare string decision (not a dict) to exercise that path.
    done = app.invoke(Command(resume="approve"), _cfg())
    assert "Done" in done["response"]
    assert tools.get_availability("20260803", "20260803").busy[0].name == "New Name"


def test_delete_event_flow_verifies_absence() -> None:
    tools = FakeCalendarTools()
    eid = tools.add_recurring_event(
        "Doomed", "20260803", "FREQ=DAILY", time="090000"
    ).event_id
    delete = {
        "action": "delete",
        "title": "",
        "timezone": "America/New_York",
        "start": "2026-08-03T09:00:00-04:00",
        "target_event_id": eid,
    }
    app = build_agent(_model(delete), tools, MemorySaver())

    app.invoke({"request": "cancel it"}, _cfg())
    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"] and "verified" in done["response"]
    assert tools.get_availability("20260803", "20260803").busy == []


def test_failed_write_is_reported() -> None:
    missing = {
        "action": "delete",
        "title": "",
        "timezone": "UTC",
        "start": "2026-08-03T09:00:00+00:00",
        "target_event_id": 999,
    }
    app = build_agent(_model(missing), FakeCalendarTools(), MemorySaver())
    app.invoke({"request": "delete 999"}, _cfg())
    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "didn't succeed" in done["response"]


def test_conflict_and_details_shown_in_summary() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event(
        "Existing Mtg", "20260803", "FREQ=DAILY", time="130000", duration=60
    )
    proposal = {
        "action": "create_recurring",
        "title": "Overlapping",
        "timezone": "America/New_York",
        "start": "2026-08-03T09:10:00-04:00",  # 13:10 UTC, overlaps 13:00 event
        "duration_minutes": 30,
        "location": "Room B",
        "participants": ["Bob", "Dana"],
        "recurrence": {"freq": "DAILY"},
    }
    app = build_agent(_model(proposal), tools, MemorySaver())
    paused = app.invoke({"request": "book it"}, _cfg())
    summary = paused["__interrupt__"][0].value["summary"]
    assert "Conflicts with: Existing Mtg" in summary
    assert "Room B" in summary and "Bob" in summary


def test_update_by_description_resolves_and_moves_event() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Dog Grooming", "20260718", "FREQ=DAILY", time="140000")
    move = {
        "action": "update",
        "title": "Dog Grooming",
        "timezone": "America/New_York",
        "start": "2026-07-19T10:00:00-04:00",  # -> 20260719 14:00 GMT
    }
    app = build_agent(_model(move), tools, MemorySaver())

    paused = app.invoke({"request": "move dog grooming to sunday"}, _cfg())
    # Resolved: the approval summary names the target event.
    assert "Target: event #" in paused["__interrupt__"][0].value["summary"]

    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    assert tools.get_availability("20260718", "20260718").busy == []
    assert tools.get_availability("20260719", "20260719").busy[0].name == "Dog Grooming"


def test_update_disambiguated_by_target_date() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Dog Grooming", "20260718", "FREQ=DAILY", time="140000")
    tools.add_recurring_event("Dog Grooming", "20260725", "FREQ=DAILY", time="140000")
    move = {
        "action": "update",
        "title": "Dog Grooming",
        "timezone": "America/New_York",
        "start": "2026-07-19T10:00:00-04:00",  # -> 20260719 14:00 GMT
        "target_date": "2026-07-18",  # picks the 18th, not the 25th
    }
    app = build_agent(_model(move), tools, MemorySaver())

    paused = app.invoke({"request": "move the july 18 grooming to sunday"}, _cfg())
    assert "Target: event #" in paused["__interrupt__"][0].value["summary"]

    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    assert (
        tools.get_availability("20260718", "20260718").busy == []
    )  # moved off the 18th
    assert tools.get_availability("20260719", "20260719").busy[0].name == "Dog Grooming"
    assert tools.get_availability(
        "20260725", "20260725"
    ).busy  # the other one untouched


def test_move_preserves_time_and_name_on_date_only_move() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Dog Grooming", "20260718", "FREQ=DAILY", time="140000")
    move = {
        "action": "update",
        "title": "Dog Grooming",  # a search key, not a new name
        "timezone": "America/New_York",
        "start": "2026-07-19T00:00:00-04:00",  # midnight -> no new time (same time)
        "target_date": "2026-07-18",
    }
    app = build_agent(_model(move), tools, MemorySaver())

    paused = app.invoke(
        {"request": "move july 18 grooming to sunday, same time"}, _cfg()
    )
    assert "keeping the current time" in paused["__interrupt__"][0].value["summary"]

    done = app.invoke(Command(resume={"decision": "approve"}), _cfg())
    assert "Done" in done["response"]
    busy = tools.get_availability("20260719", "20260719").busy
    assert busy[0].time == "140000"  # original time preserved (not clobbered)
    assert busy[0].name == "Dog Grooming"  # not renamed to the search term
    assert tools.get_availability("20260718", "20260718").busy == []


def test_update_target_date_no_match_errors() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Dog Grooming", "20260718", "FREQ=DAILY", time="140000")
    move = {
        "action": "update",
        "title": "Dog Grooming",
        "timezone": "America/New_York",
        "start": "2026-07-19T10:00:00-04:00",
        "target_date": "2026-01-01",  # no grooming on that date
    }
    app = build_agent(_model(move), tools, MemorySaver())
    result = app.invoke({"request": "move it"}, _cfg())
    assert "__interrupt__" not in result
    assert "couldn't find" in result["response"].lower()
    assert "2026-01-01" in result["response"]


def test_update_by_description_not_found_errors() -> None:
    ghost = {
        "action": "update",
        "title": "Nonexistent Event",
        "timezone": "America/New_York",
        "start": "2026-07-19T10:00:00-04:00",
    }
    app = build_agent(_model(ghost), FakeCalendarTools(), MemorySaver())
    result = app.invoke({"request": "move nonexistent"}, _cfg())
    assert "__interrupt__" not in result
    assert "couldn't find" in result["response"].lower()


def test_update_by_description_ambiguous_errors() -> None:
    tools = FakeCalendarTools()
    tools.add_recurring_event("Dog Grooming", "20260718", "FREQ=DAILY", time="140000")
    tools.add_recurring_event("Dog Grooming", "20260725", "FREQ=DAILY", time="140000")
    move = {
        "action": "update",
        "title": "Dog Grooming",
        "timezone": "America/New_York",
        "start": "2026-07-19T10:00:00-04:00",
    }
    app = build_agent(_model(move), tools, MemorySaver())
    result = app.invoke({"request": "move dog grooming"}, _cfg())
    assert "__interrupt__" not in result
    assert "multiple events match" in result["response"].lower()


def test_resume_after_restart_via_sqlite_checkpointer() -> None:
    tools = FakeCalendarTools()  # shared: the write is observable across "restart"
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "checkpoints.sqlite")

    # First process: run up to the approval interrupt, then "crash".
    conn1 = sqlite3.connect(db_path, check_same_thread=False)
    app1 = build_agent(_model(STANDUP), tools, SqliteSaver(conn1))
    paused = app1.invoke({"request": "standup"}, _cfg("resume-thread"))
    assert "__interrupt__" in paused
    conn1.close()

    # Second process: a brand-new graph + checkpointer over the same file
    # resumes the interrupted thread from persisted state.
    conn2 = sqlite3.connect(db_path, check_same_thread=False)
    app2 = build_agent(_model(), tools, SqliteSaver(conn2))
    done = app2.invoke(Command(resume={"decision": "approve"}), _cfg("resume-thread"))
    conn2.close()

    assert "Done" in done["response"]
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Team Standup"
