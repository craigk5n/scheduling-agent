"""The LangGraph scheduling agent.

Flow: parse_intent -> gather_context -> propose -> [human_approval interrupt]
-> execute -> verify -> respond. Queries skip approval/execution; a rejected
proposal loops back to parse_intent with the user's feedback. State is
checkpointed, so the approval interrupt survives a process restart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from scheduling_agent.calendar import CalendarTools
from scheduling_agent.models import (
    AvailabilityResult,
    ConflictResult,
    ScheduleAction,
    ScheduleProposal,
    WriteResult,
)
from scheduling_agent.render import render_availability, render_proposal
from scheduling_agent.rrule import RruleError, build_rrule, validate_rrule
from scheduling_agent.structured import structured_call

_PARSE_SYSTEM = (
    "You are a scheduling assistant. Turn the user's request into a single "
    "ScheduleProposal. Use timezone-aware start times in the user's timezone. "
    "For recurring events set action=create_recurring and fill the recurrence. "
    "For update/delete, set 'title' to the target event's name; 'start' is the "
    "NEW date/time; and if the user says which date the event is currently on "
    "(e.g. 'the one on July 18'), set 'target_date' to that current date. "
    "Today's date is {today}."
)


class AgentState(TypedDict, total=False):
    """Graph state (checkpointed)."""

    request: str
    feedback: str | None
    proposal: ScheduleProposal
    rrule: str | None
    availability: AvailabilityResult | None
    conflicts: ConflictResult | None
    approved: bool
    resolved_by_search: bool
    result: WriteResult | None
    verified: bool
    response: str
    error: str | None


def _gmt(proposal: ScheduleProposal) -> tuple[str, str]:
    dt = proposal.start.astimezone(UTC)
    return dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")


def _is_time_unspecified(dt: datetime) -> bool:
    """Midnight local time means the model gave a date but no time-of-day —
    i.e. a pure date move where the event's current time should be kept."""
    return dt.hour == 0 and dt.minute == 0 and dt.second == 0


def _parse_intent(
    state: AgentState, model: BaseChatModel, method: str | None
) -> dict[str, Any]:
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    messages = [
        SystemMessage(content=_PARSE_SYSTEM.format(today=today)),
        HumanMessage(content=state["request"]),
    ]
    feedback = state.get("feedback")
    if feedback:
        messages.append(
            HumanMessage(content=f"Revise the previous plan given: {feedback}")
        )
    proposal = structured_call(model, messages, ScheduleProposal, method=method)
    return {"proposal": proposal, "feedback": None, "error": None}


def _gather_context(state: AgentState, tools: CalendarTools) -> dict[str, Any]:
    proposal = state["proposal"]
    if proposal.action is ScheduleAction.QUERY:
        start, _ = _gmt(proposal)
        end = (proposal.start.astimezone(UTC) + timedelta(days=7)).strftime("%Y%m%d")
        return {"availability": tools.get_availability(start, end)}
    if proposal.action in (ScheduleAction.CREATE, ScheduleAction.CREATE_RECURRING):
        date, time = _gmt(proposal)
        return {
            "conflicts": tools.check_conflicts(date, time, proposal.duration_minutes)
        }
    if proposal.action in (ScheduleAction.UPDATE, ScheduleAction.DELETE):
        return _resolve_target(proposal, tools)
    return {}  # pragma: no cover - all ScheduleAction values are handled above


def _resolve_target(proposal: ScheduleProposal, tools: CalendarTools) -> dict[str, Any]:
    """Resolve an update/delete target to a concrete event id, searching by
    title when the model didn't provide an id."""
    if proposal.target_event_id is not None:
        return {}
    matches = tools.search_events(proposal.title, limit=25)
    # Narrow by the target's current date when the user gave one.
    if proposal.target_date is not None:
        wanted = proposal.target_date.strftime("%Y%m%d")
        matches = [m for m in matches if m.date == wanted]

    if not matches:
        on = f" on {proposal.target_date.isoformat()}" if proposal.target_date else ""
        return {"error": f"I couldn't find an event matching '{proposal.title}'{on}."}
    if len(matches) > 1:
        listing = ", ".join(f"{m.name} on {m.date}" for m in matches[:5])
        return {
            "error": (
                f"Multiple events match '{proposal.title}': {listing}. "
                "Please say which date the event is currently on."
            )
        }
    resolved = proposal.model_copy(update={"target_event_id": matches[0].id})
    # Flag that the title was a search key (so execute won't rename the event).
    return {"proposal": resolved, "resolved_by_search": True}


def _propose(state: AgentState) -> dict[str, Any]:
    proposal = state["proposal"]
    rrule: str | None = None
    if proposal.recurrence is not None:
        try:
            rrule = build_rrule(proposal.recurrence)
            validate_rrule(rrule)
        except RruleError as exc:
            return {"error": f"Invalid recurrence: {exc}"}
    return {"rrule": rrule}


def _after_propose(state: AgentState) -> str:
    if state.get("error"):
        return "respond"
    if state["proposal"].action is ScheduleAction.QUERY:
        return "respond"
    return "approve"


def _human_approval(state: AgentState) -> dict[str, Any]:
    proposal = state["proposal"]
    decision = interrupt(
        {
            "summary": render_proposal(
                proposal, state.get("rrule"), state.get("conflicts")
            ),
            "action": proposal.action.value,
        }
    )
    verdict, feedback = _read_decision(decision)
    if verdict == "approve":
        return {"approved": True}
    return {"approved": False, "feedback": feedback or "The user rejected the plan."}


def _read_decision(decision: Any) -> tuple[str, str | None]:
    if isinstance(decision, dict):
        return str(decision.get("decision", "reject")), decision.get("feedback")
    return str(decision), None


def _after_approval(state: AgentState) -> str:
    return "execute" if state.get("approved") else "replan"


def _execute(state: AgentState, tools: CalendarTools) -> dict[str, Any]:
    proposal = state["proposal"]
    date, time = _gmt(proposal)
    action = proposal.action
    if action is ScheduleAction.CREATE:
        result = tools.add_event(
            proposal.title,
            date,
            time,
            proposal.description,
            proposal.location,
            proposal.duration_minutes,
        )
    elif action is ScheduleAction.CREATE_RECURRING:
        result = tools.add_recurring_event(
            proposal.title,
            date,
            state["rrule"] or "",
            time,
            proposal.duration_minutes,
            proposal.description,
            proposal.location,
        )
    elif action is ScheduleAction.UPDATE:
        # A title used to *find* the event (search-resolved) must not be sent as
        # a new name, or a search term would rename the event. And a move with
        # no new time (midnight local) keeps the event's current time.
        resolved_by_search = state.get("resolved_by_search", False)
        name = None if resolved_by_search else (proposal.title or None)
        keep_time = _is_time_unspecified(proposal.start)
        result = tools.update_event(
            proposal.target_event_id or 0,
            name=name,
            date=date,
            time=None if keep_time else time,
            duration=proposal.duration_minutes or None,
        )
    else:  # DELETE
        result = tools.delete_event(proposal.target_event_id or 0)
    return {"result": result}


def _verify(state: AgentState, tools: CalendarTools) -> dict[str, Any]:
    result = state.get("result")
    if result is None or not result.success or result.event_id is None:
        return {"verified": False}
    proposal = state["proposal"]
    if proposal.action is ScheduleAction.DELETE:
        date, _ = _gmt(proposal)
        events = tools.list_events(date, date)
        return {"verified": all(e.id != result.event_id for e in events)}
    if proposal.action is ScheduleAction.CREATE_RECURRING:
        date, _ = _gmt(proposal)
        events = tools.list_events(date, date)
        return {"verified": any(e.id == result.event_id for e in events)}
    return {"verified": True}


def _respond(state: AgentState) -> dict[str, Any]:
    if state.get("error"):
        return {"response": f"I couldn't do that: {state['error']}"}
    proposal = state["proposal"]
    if proposal.action is ScheduleAction.QUERY:
        return {"response": render_availability(state.get("availability"))}
    result = state.get("result")
    if result is not None and result.success:
        verified = " (verified)" if state.get("verified") else ""
        return {
            "response": (
                f"Done — {proposal.action.value} (event {result.event_id}){verified}."
            )
        }
    reason = result.error if result is not None else "unknown error"
    return {"response": f"That didn't succeed: {reason}"}


def build_agent(
    model: BaseChatModel,
    tools: CalendarTools,
    checkpointer: Any,
    *,
    structured_method: str | None = None,
) -> Any:
    """Compile the scheduling agent graph with an interrupt-capable checkpointer.

    ``structured_method`` (e.g. "json_schema") enables provider-native
    structured output for parsing, with a repair-loop fallback.
    """
    builder = StateGraph(AgentState)
    builder.add_node(
        "parse_intent", lambda s: _parse_intent(s, model, structured_method)
    )
    builder.add_node("gather_context", lambda s: _gather_context(s, tools))
    builder.add_node("propose", _propose)
    builder.add_node("human_approval", _human_approval)
    builder.add_node("execute", lambda s: _execute(s, tools))
    builder.add_node("verify", lambda s: _verify(s, tools))
    builder.add_node("respond", _respond)

    builder.add_edge(START, "parse_intent")
    builder.add_edge("parse_intent", "gather_context")
    builder.add_edge("gather_context", "propose")
    builder.add_conditional_edges(
        "propose",
        _after_propose,
        {"approve": "human_approval", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "human_approval",
        _after_approval,
        {"execute": "execute", "replan": "parse_intent"},
    )
    builder.add_edge("execute", "verify")
    builder.add_edge("verify", "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)
