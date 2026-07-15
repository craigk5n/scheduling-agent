"""Tests for the CLI REPL (injectable I/O) and agent wiring."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.cli import _prompt_decision, build_cli_agent, run_repl
from scheduling_agent.graph import build_agent
from scheduling_agent.settings import Settings

STANDUP = {
    "action": "create_recurring",
    "title": "Team Standup",
    "timezone": "America/New_York",
    "start": "2026-08-03T09:15:00-04:00",
    "duration_minutes": 15,
    "recurrence": {"freq": "WEEKLY", "by_day": ["MO"]},
}


def _scripted(inputs: list[str]) -> Callable[[str], str]:
    it = iter(inputs)

    def read(_prompt: str = "") -> str:
        return next(it)

    return read


def _sink() -> tuple[Callable[[str], None], list[str]]:
    out: list[str] = []
    return (lambda s: out.append(s)), out


def _agent(*proposals: dict[str, Any], tools: FakeCalendarTools) -> Any:
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(p)) for p in proposals])
    )
    return build_agent(model, tools, MemorySaver())


# --- _prompt_decision ----------------------------------------------------------


def test_prompt_decision_approve() -> None:
    write, _ = _sink()
    assert _prompt_decision(_scripted(["y"]), write) == {"decision": "approve"}


def test_prompt_decision_freeform_feedback_is_rejection() -> None:
    write, _ = _sink()
    d = _prompt_decision(_scripted(["make it Tuesdays"]), write)
    assert d["decision"] == "reject" and d["feedback"] == "make it Tuesdays"


def test_prompt_decision_no_then_feedback_prompt() -> None:
    write, _ = _sink()
    d = _prompt_decision(_scripted(["n", "change the time"]), write)
    assert d == {"decision": "reject", "feedback": "change the time"}


# --- run_repl ------------------------------------------------------------------


def test_repl_approve_creates_event() -> None:
    tools = FakeCalendarTools()
    write, out = _sink()
    run_repl(
        _agent(STANDUP, tools=tools),
        read=_scripted(["set up standup", "y", "quit"]),
        write=write,
    )
    joined = "\n".join(out)
    assert "Team Standup" in joined  # proposal summary shown
    assert "Done" in joined
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Team Standup"


def test_repl_reject_then_replan_then_approve() -> None:
    first = {**STANDUP, "title": "Wrong"}
    second = {**STANDUP, "title": "Right"}
    tools = FakeCalendarTools()
    write, out = _sink()
    run_repl(
        _agent(first, second, tools=tools),
        read=_scripted(["standup", "n", "call it Right", "y", "quit"]),
        write=write,
    )
    assert "Done" in "\n".join(out)
    assert tools.get_availability("20260803", "20260803").busy[0].name == "Right"


def test_repl_quit_immediately_does_nothing() -> None:
    tools = FakeCalendarTools()
    write, out = _sink()
    run_repl(_agent(tools=tools), read=_scripted(["quit"]), write=write)
    assert out == []


def test_repl_skips_blank_input() -> None:
    tools = FakeCalendarTools()
    write, out = _sink()
    run_repl(
        _agent(STANDUP, tools=tools),
        read=_scripted(["", "set up standup", "y", "quit"]),
        write=write,
    )
    assert "Done" in "\n".join(out)


def test_repl_stops_on_eof() -> None:
    def read(_prompt: str = "") -> str:
        raise EOFError

    write, out = _sink()
    run_repl(_agent(tools=FakeCalendarTools()), read=read, write=write)
    assert out == []


# --- build_cli_agent -----------------------------------------------------------


def test_build_cli_agent_returns_runnable() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-x"}
    )
    agent = build_cli_agent(
        settings,
        mcp_url="https://example/mcp.php",
        mcp_token="tok",
        checkpointer=MemorySaver(),
    )
    assert hasattr(agent, "invoke")
