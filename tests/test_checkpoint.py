"""Tests for the checkpoint serializer allowlist (silences msgpack warnings
without breaking model reconstruction)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import warnings

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.checkpoint import _model_types, default_serde
from scheduling_agent.graph import build_agent

STANDUP = {
    "action": "create_recurring",
    "title": "Standup",
    "timezone": "America/New_York",
    "start": "2026-08-03T09:15:00-04:00",
    "duration_minutes": 15,
    "recurrence": {"freq": "WEEKLY", "by_day": ["MO"]},
}


def test_model_types_cover_core_models() -> None:
    names = {name for _, name in _model_types()}
    assert {
        "ScheduleProposal",
        "ScheduleAction",
        "RecurrenceSpec",
        "WriteResult",
        "ConflictResult",
        "AvailabilityResult",
    } <= names
    assert all(mod == "scheduling_agent.models" for mod, _ in _model_types())


def test_checkpointer_reconstructs_models_without_warning() -> None:
    tmp = tempfile.mkdtemp()
    conn = sqlite3.connect(os.path.join(tmp, "c.sqlite"), check_same_thread=False)
    tools = FakeCalendarTools()
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(STANDUP))])
    )
    app = build_agent(model, tools, SqliteSaver(conn, serde=default_serde()))
    cfg = {"configurable": {"thread_id": "t1"}}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.invoke({"request": "standup"}, cfg)
        done = app.invoke(Command(resume={"decision": "approve"}), cfg)

    # Reconstructed correctly (a proper response, not a dict AttributeError)...
    assert "Done" in done["response"]
    # ...and no "unregistered type" deserialization warnings.
    assert not [w for w in caught if "unregistered type" in str(w.message)]
    conn.close()
