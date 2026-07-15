"""Tests for human-readable rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from scheduling_agent.models import (
    AvailabilityResult,
    BusyBlock,
    Conflict,
    ConflictResult,
    Frequency,
    RecurrenceSpec,
    ScheduleAction,
    ScheduleProposal,
)
from scheduling_agent.render import render_availability, render_proposal

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


def test_render_availability_empty() -> None:
    assert "No busy" in render_availability(None)
    assert "No busy" in render_availability(AvailabilityResult())


def test_render_availability_with_busy() -> None:
    avail = AvailabilityResult(
        busy=[
            BusyBlock(id=1, name="Meeting", date="20260803", time="090000", duration=30)
        ]
    )
    text = render_availability(avail)
    assert "Meeting" in text and "20260803" in text


def test_render_no_conflict_when_flag_false() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE,
        title="X",
        timezone="UTC",
        start=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
    )
    text = render_proposal(p, None, ConflictResult(has_conflict=False))
    assert "Conflicts" not in text
