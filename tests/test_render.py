"""Tests for human-readable rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from scheduling_agent.models import (
    BusyBlock,
    Conflict,
    ConflictResult,
    Frequency,
    RecurrenceSpec,
    ScheduleAction,
    ScheduleProposal,
)
from scheduling_agent.render import render_events, render_proposal

NY = ZoneInfo("America/New_York")


def test_render_minimal_proposal() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE,
        title="Lunch",
        timezone="America/New_York",
        start=datetime(2026, 8, 4, 12, 0, tzinfo=NY),
    )
    text = render_proposal(p)
    assert "Create: Lunch" in text
    assert "Repeats" not in text and "Conflicts" not in text


def test_render_full_proposal_with_rrule_and_conflicts() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE_RECURRING,
        title="Standup",
        timezone="America/New_York",
        start=datetime(2026, 8, 3, 9, 15, tzinfo=NY),
        duration_minutes=15,
        location="Zoom",
        participants=["Bob"],
        recurrence=RecurrenceSpec(freq=Frequency.WEEKLY, by_day=["MO"]),
    )
    conflicts = ConflictResult(
        has_conflict=True,
        conflicts=[Conflict(id=1, name="Busy", date="20260803", time="131500")],
    )
    text = render_proposal(p, "FREQ=WEEKLY;BYDAY=MO", conflicts)
    assert "Create Recurring: Standup" in text
    assert "15 min" in text
    assert "Repeats: Every week on Mon" in text
    assert "Where: Zoom" in text
    assert "With: Bob" in text
    assert "Conflicts with: Busy" in text


def test_render_events_empty() -> None:
    assert "No events" in render_events(None)
    assert "No events" in render_events([])


def test_render_events_shows_local_date_and_time() -> None:
    # list_events returns local YYYYMMDD / HHMMSS, so the rendered clock time is
    # the user's local time (here 07:30), not GMT.
    events = [
        BusyBlock(id=1, name="Haircut", date="20260716", time="073000", duration=60)
    ]
    text = render_events(events)
    assert "Haircut" in text
    assert "2026-07-16 07:30" in text


def test_render_events_marks_all_day() -> None:
    events = [BusyBlock(id=2, name="Holiday", date="20260704", time="-1")]
    text = render_events(events)
    assert "2026-07-04 (all day)" in text
    assert "Holiday" in text


def test_render_events_tolerates_malformed_time() -> None:
    # Defensive: MCP data is external; an unexpected time string is shown as-is.
    events = [BusyBlock(id=3, name="Odd", date="20260704", time="nope")]
    text = render_events(events)
    assert "2026-07-04 nope" in text


def test_render_date_only_move_keeps_time() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.UPDATE,
        title="Dog Grooming",
        timezone="America/New_York",
        start=datetime(2026, 7, 19, 0, 0, tzinfo=NY),  # midnight -> date-only move
        target_event_id=5,
    )
    text = render_proposal(p)
    assert "keeping the current time" in text
    assert "2026-07-19" in text
    assert "Target: event #5" in text


def test_render_no_conflict_when_flag_false() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE,
        title="X",
        timezone="UTC",
        start=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
    )
    text = render_proposal(p, None, ConflictResult(has_conflict=False))
    assert "Conflicts" not in text
