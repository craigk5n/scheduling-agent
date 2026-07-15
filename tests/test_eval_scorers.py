"""Tests for the deterministic eval scorers (must pass good output AND fail bad)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scheduling_agent.evals.models import (
    EvalCase,
    ExpectedOutcome,
    FixtureEvent,
)
from scheduling_agent.evals.scorers import score_case
from scheduling_agent.models import (
    Frequency,
    RecurrenceSpec,
    ScheduleAction,
    ScheduleProposal,
)

NY = ZoneInfo("America/New_York")


def _proposal(**kw: object) -> ScheduleProposal:
    base: dict[str, object] = {
        "action": ScheduleAction.CREATE_RECURRING,
        "title": "Standup",
        "timezone": "America/New_York",
        "start": datetime(2026, 8, 3, 9, 15, tzinfo=NY),
        "duration_minutes": 15,
        "recurrence": RecurrenceSpec(freq=Frequency.WEEKLY, by_day=["MO", "WE", "FR"]),
    }
    base.update(kw)
    return ScheduleProposal(**base)  # type: ignore[arg-type]


def _case(expected: ExpectedOutcome, **kw: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "c1",
        "request": "set up standup",
        "timezone": "America/New_York",
        "today": "2026-07-15",
        "reference": _proposal(),
        "expected": expected,
    }
    base.update(kw)
    return EvalCase(**base)  # type: ignore[arg-type]


def test_good_weekly_proposal_passes() -> None:
    case = _case(
        ExpectedOutcome(
            action=ScheduleAction.CREATE_RECURRING,
            rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
            require_weekdays=["MO", "WE", "FR"],
        )
    )
    result = score_case(_proposal(), case)
    assert result.passed
    assert {c.name for c in result.checks} >= {"action", "rrule_valid", "rrule_match"}


def test_wrong_action_fails() -> None:
    case = _case(ExpectedOutcome(action=ScheduleAction.CREATE_RECURRING))
    result = score_case(_proposal(action=ScheduleAction.CREATE, recurrence=None), case)
    assert not result.passed
    assert any(c.name == "action" and not c.passed for c in result.checks)


def test_rrule_mismatch_fails() -> None:
    case = _case(
        ExpectedOutcome(
            action=ScheduleAction.CREATE_RECURRING, rrule="FREQ=WEEKLY;BYDAY=MO"
        )
    )
    result = score_case(_proposal(), case)  # proposal is MO,WE,FR
    assert not result.passed
    assert any(c.name == "rrule_match" and not c.passed for c in result.checks)


def test_forbid_weekday_violation_fails() -> None:
    # "Avoid Fridays" but the proposal includes Friday.
    case = _case(
        ExpectedOutcome(
            action=ScheduleAction.CREATE_RECURRING, forbid_weekdays=["FR", "SA", "SU"]
        )
    )
    result = score_case(_proposal(), case)  # MO,WE,FR -> Friday violates
    assert not result.passed
    assert any(c.name == "forbid_weekdays" and not c.passed for c in result.checks)


def test_forbid_weekday_satisfied_passes() -> None:
    good = _proposal(
        recurrence=RecurrenceSpec(
            freq=Frequency.WEEKLY, by_day=["MO", "TU", "WE", "TH"]
        )
    )
    case = _case(
        ExpectedOutcome(
            action=ScheduleAction.CREATE_RECURRING, forbid_weekdays=["FR", "SA", "SU"]
        )
    )
    assert score_case(good, case).passed


def test_occurrence_count_check() -> None:
    prop = _proposal(recurrence=RecurrenceSpec(freq=Frequency.DAILY, count=5))
    case = _case(
        ExpectedOutcome(action=ScheduleAction.CREATE_RECURRING, occurrence_count=5)
    )
    assert score_case(prop, case).passed


def test_dst_local_hour_preserved_passes() -> None:
    # Daily across US spring-forward (2026-03-08); 09:00 local must hold.
    prop = _proposal(
        start=datetime(2026, 3, 7, 9, 0, tzinfo=NY),
        recurrence=RecurrenceSpec(freq=Frequency.DAILY, count=4),
    )
    case = _case(
        ExpectedOutcome(
            action=ScheduleAction.CREATE_RECURRING, local_hour=9, occurrence_count=4
        )
    )
    assert score_case(prop, case).passed


def test_conflict_expectation() -> None:
    fixtures = [FixtureEvent(name="Busy", date="20260803", time="131500", duration=60)]
    prop = _proposal(
        recurrence=None,
        action=ScheduleAction.CREATE,
        start=datetime(2026, 8, 3, 9, 15, tzinfo=NY),  # 13:15 UTC, overlaps 13:15 busy
        duration_minutes=30,
    )
    hit = _case(
        ExpectedOutcome(action=ScheduleAction.CREATE, expect_conflict=True),
        fixtures=fixtures,
    )
    assert score_case(prop, hit).passed

    miss = _case(
        ExpectedOutcome(action=ScheduleAction.CREATE, expect_conflict=False),
        fixtures=fixtures,
    )
    assert not score_case(prop, miss).passed


def test_invalid_recurrence_marks_rrule_invalid() -> None:
    # RecurrenceSpec allows out-of-range by_month_day; build+validate catches it.
    prop = _proposal(
        recurrence=RecurrenceSpec(freq=Frequency.MONTHLY, by_month_day=[32])
    )
    case = _case(ExpectedOutcome(action=ScheduleAction.CREATE_RECURRING))
    result = score_case(prop, case)
    assert not result.passed
    assert any(c.name == "rrule_valid" and not c.passed for c in result.checks)


def test_untimed_fixture_never_conflicts() -> None:
    fixtures = [FixtureEvent(name="Holiday", date="20260803")]  # time defaults to -1
    prop = _proposal(
        recurrence=None,
        action=ScheduleAction.CREATE,
        start=datetime(2026, 8, 3, 9, 15, tzinfo=NY),
        duration_minutes=30,
    )
    case = _case(
        ExpectedOutcome(action=ScheduleAction.CREATE, expect_conflict=False),
        fixtures=fixtures,
    )
    assert score_case(prop, case).passed
