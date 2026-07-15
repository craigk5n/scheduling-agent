"""Human-readable rendering of proposals and availability for the CLI/HITL."""

from __future__ import annotations

from scheduling_agent.models import (
    AvailabilityResult,
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


def render_availability(availability: AvailabilityResult | None) -> str:
    """Render busy blocks (GMT frame) for a query response."""
    if availability is None or not availability.busy:
        return "No busy time blocks found in that range."
    rows = [
        f"  - {b.date} {b.time} {b.name} ({b.duration} min)" for b in availability.busy
    ]
    return "Busy (GMT):\n" + "\n".join(rows)
