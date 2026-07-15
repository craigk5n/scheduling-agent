"""Tests for the Pydantic domain models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from scheduling_agent.models import (
    BusyBlock,
    Conflict,
    Frequency,
    RecurrenceSpec,
    ScheduleAction,
    ScheduleProposal,
    WriteResult,
)

NY = ZoneInfo("America/New_York")


def _start() -> datetime:
    return datetime(2026, 8, 3, 9, 15, tzinfo=NY)


def test_valid_create_proposal() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE,
        title="Lunch",
        timezone="America/New_York",
        start=_start(),
        duration_minutes=60,
    )
    assert p.action is ScheduleAction.CREATE
    assert p.title == "Lunch"
    assert p.recurrence is None


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError):
        ScheduleProposal(
            action=ScheduleAction.CREATE,
            title="X",
            timezone="Mars/Olympus",
            start=_start(),
        )


def test_naive_start_rejected() -> None:
    with pytest.raises(ValidationError):
        ScheduleProposal(
            action=ScheduleAction.CREATE,
            title="X",
            timezone="America/New_York",
            start=datetime(2026, 8, 3, 9, 15),  # naive
        )


def test_update_requires_target_event_id() -> None:
    with pytest.raises(ValidationError):
        ScheduleProposal(
            action=ScheduleAction.UPDATE,
            title="X",
            timezone="UTC",
            start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        )


def test_delete_with_target_is_valid() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.DELETE,
        title="X",
        timezone="UTC",
        start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        target_event_id=42,
    )
    assert p.target_event_id == 42


def test_create_recurring_requires_recurrence() -> None:
    with pytest.raises(ValidationError):
        ScheduleProposal(
            action=ScheduleAction.CREATE_RECURRING,
            title="Standup",
            timezone="America/New_York",
            start=_start(),
        )


def test_create_recurring_with_recurrence_is_valid() -> None:
    p = ScheduleProposal(
        action=ScheduleAction.CREATE_RECURRING,
        title="Standup",
        timezone="America/New_York",
        start=_start(),
        duration_minutes=15,
        recurrence=RecurrenceSpec(freq=Frequency.WEEKLY, by_day=["MO", "WE", "FR"]),
    )
    assert p.recurrence is not None
    assert p.recurrence.freq is Frequency.WEEKLY


def test_recurrence_defaults() -> None:
    r = RecurrenceSpec(freq=Frequency.DAILY)
    assert r.interval == 1
    assert r.wkst == "MO"
    assert r.count is None and r.until is None


def test_recurrence_count_and_until_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        RecurrenceSpec(freq=Frequency.DAILY, count=5, until=date(2030, 12, 31))


def test_recurrence_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RecurrenceSpec(freq=Frequency.DAILY, interval=0)


def test_recurrence_invalid_wkst_rejected() -> None:
    with pytest.raises(ValidationError):
        RecurrenceSpec(freq=Frequency.WEEKLY, wkst="XX")


def test_recurrence_wkst_normalized_uppercase() -> None:
    assert RecurrenceSpec(freq=Frequency.WEEKLY, wkst="su").wkst == "SU"


def test_create_without_title_rejected() -> None:
    with pytest.raises(ValidationError):
        ScheduleProposal(
            action=ScheduleAction.CREATE,
            title="   ",
            timezone="UTC",
            start=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        )


def test_busy_block_and_conflict_and_write_result() -> None:
    b = BusyBlock(id=1, name="Meeting", date="20260803", time="091500", duration=60)
    assert b.id == 1
    c = Conflict(id=2, name="Overlap", date="20260803", time="090000", duration=30)
    assert c.name == "Overlap"
    w = WriteResult(success=True, event_id=7, cal_type="weekly")
    assert w.success and w.event_id == 7
    err = WriteResult(success=False, error="nope")
    assert not err.success and err.error == "nope"
