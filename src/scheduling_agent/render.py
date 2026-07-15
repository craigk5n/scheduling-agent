"""Human-readable rendering of proposals and events for the CLI/HITL."""

from __future__ import annotations

from scheduling_agent.models import (
    BusyBlock,
    ConflictResult,
    ScheduleAction,
    ScheduleProposal,
)
from scheduling_agent.rrule import describe_rrule


def render_proposal(
    proposal: ScheduleProposal,
    rrule: str | None = None,
    conflicts: ConflictResult | None = None,
) -> str:
    """Render a proposal as an approval summary, expanding recurrence and
    flagging any conflicts."""
    header = proposal.action.value.replace("_", " ").title()
    lines = [f"{header}: {proposal.title}".rstrip(": ")]
    start = proposal.start
    is_date_only_move = (
        proposal.action is ScheduleAction.UPDATE
        and start.hour == 0
        and start.minute == 0
        and start.second == 0
    )
    if is_date_only_move:
        lines.append(
            f"  When: {start.date().isoformat()} ({proposal.timezone}), "
            "keeping the current time"
        )
    else:
        lines.append(
            f"  When: {start.isoformat()} ({proposal.timezone})"
            + (
                f", {proposal.duration_minutes} min"
                if proposal.duration_minutes
                else ""
            )
        )
    if (
        proposal.action in (ScheduleAction.UPDATE, ScheduleAction.DELETE)
        and proposal.target_event_id is not None
    ):
        lines.append(f"  Target: event #{proposal.target_event_id}")
    if rrule:
        lines.append(f"  Repeats: {describe_rrule(rrule)}")
    if proposal.location:
        lines.append(f"  Where: {proposal.location}")
    if proposal.participants:
        lines.append(f"  With: {', '.join(proposal.participants)}")
    if conflicts is not None and conflicts.has_conflict:
        names = ", ".join(c.name for c in conflicts.conflicts)
        lines.append(f"  ⚠ Conflicts with: {names}")
    return "\n".join(lines)


def render_events(events: list[BusyBlock] | None) -> str:
    """Render a listing query's events. ``list_events`` returns local dates
    (YYYYMMDD) and times (HHMMSS, or "-1" for all-day), so the clock times shown
    here match the user's calendar."""
    if not events:
        return "No events found in that range."
    rows = [f"  - {_format_when(e.date, e.time)}  {e.name}" for e in events]
    return "Upcoming events:\n" + "\n".join(rows)


def _format_when(date: str, time: str) -> str:
    """Format a local YYYYMMDD date and HHMMSS time (or "-1") for display."""
    day = f"{date[0:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date
    if time == "-1":
        return f"{day} (all day)"
    if len(time) >= 4 and time.isdigit():
        padded = time.zfill(6)
        return f"{day} {padded[0:2]}:{padded[2:4]}"
    return f"{day} {time}"
