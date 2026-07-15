"""Terminal chat interface for the scheduling agent.

The REPL takes injectable ``read``/``write`` callables so the loop — including
the human-in-the-loop approval round-trip — is exercised in tests without real
stdin/stdout.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from scheduling_agent.calendar import HttpMcpCalendarTools
from scheduling_agent.graph import build_agent
from scheduling_agent.observability import (
    configure_logging,
    log_event,
    set_correlation_id,
    tracing_enabled,
)
from scheduling_agent.providers import get_chat_model
from scheduling_agent.settings import Settings

_APPROVE_WORDS = {"y", "yes", "approve", "ok"}
_REJECT_WORDS = {"n", "no", ""}
_QUIT_WORDS = {"quit", "exit"}


def build_cli_agent(
    settings: Settings, *, mcp_url: str, mcp_token: str, checkpointer: Any
) -> Any:
    """Wire a model + live MCP calendar tools + graph into a runnable agent."""
    model = get_chat_model(settings)
    tools = HttpMcpCalendarTools(mcp_url, mcp_token)
    return build_agent(model, tools, checkpointer)


def _prompt_decision(
    read: Callable[[str], str], write: Callable[[str], None]
) -> dict[str, str]:
    answer = read("approve? [y / n / or type feedback] > ").strip()
    if answer.lower() in _APPROVE_WORDS:
        return {"decision": "approve"}
    if answer.lower() in _REJECT_WORDS:
        feedback = read("what should change? > ").strip()
    else:
        feedback = answer
    return {
        "decision": "reject",
        "feedback": feedback or "The user rejected the plan.",
    }


def run_repl(
    agent: Any,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    thread_prefix: str = "cli",
) -> None:
    """Drive a chat loop: each request runs to an approval interrupt (if any),
    prompts the user, resumes, and prints the response. A new thread id per
    turn keeps requests independent while the checkpointer persists each."""
    turn = 0
    while True:
        try:
            request = read("\nyou> ").strip()
        except EOFError:
            break
        if request.lower() in _QUIT_WORDS:
            break
        if not request:
            continue
        turn += 1
        thread_id = f"{thread_prefix}-{turn}"
        set_correlation_id(thread_id)
        log_event("request", request=request)
        config = {"configurable": {"thread_id": thread_id}}
        state = agent.invoke({"request": request}, config)
        while "__interrupt__" in state:
            write(state["__interrupt__"][0].value["summary"])
            decision = _prompt_decision(read, write)
            log_event("decision", decision=decision.get("decision"))
            state = agent.invoke(Command(resume=decision), config)
        log_event("responded")
        write(state.get("response", ""))


def main() -> None:  # pragma: no cover - reads env, opens sqlite, network I/O
    configure_logging()
    settings = Settings.from_env()
    mcp_url = os.environ.get("MCP_URL", "").strip()
    mcp_token = os.environ.get("MCP_TOKEN", "").strip()
    if not mcp_url or not mcp_token:
        raise SystemExit("MCP_URL and MCP_TOKEN must be set (see .env.example)")
    conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
    agent = build_cli_agent(
        settings, mcp_url=mcp_url, mcp_token=mcp_token, checkpointer=SqliteSaver(conn)
    )
    trace = "on" if tracing_enabled() else "off"
    print(
        f"Scheduling agent ready (provider={settings.model_provider.value}, "
        f"tracing={trace}). Describe what to schedule, or type 'quit'."
    )
    run_repl(agent)
