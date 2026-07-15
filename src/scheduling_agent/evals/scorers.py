"""Deterministic scorers: does a ScheduleProposal satisfy a case's expected
semantics? No LLM involved — these run in CI and catch regressions in the
RRULE/DST/conflict machinery."""

from __future__ import annotations

from datetime import UTC

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.evals.models import (
    CaseResult,
    CheckResult,
    EvalCase,
    ExpectedOutcome,
    FixtureEvent,
)
from scheduling_agent.models import ScheduleProposal
from scheduling_agent.rrule import RruleError, build_rrule, expand, validate_rrule

_WEEKDAY_INDEX = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def score_case(proposal: ScheduleProposal, case: EvalCase) -> CaseResult:
    """Score a proposal against a case's expected outcome."""
    expected = case.expected
    checks: list[CheckResult] = [
        _check(
            "action",
            proposal.action == expected.action,
            f"{proposal.action.value} vs {expected.action.value}",
        )
    ]

    rrule: str | None = None
    if proposal.recurrence is not None:
        try:
            rrule = build_rrule(proposal.recurrence)
            validate_rrule(rrule)
            checks.append(_check("rrule_valid", True, rrule))
        except RruleError as exc:
            checks.append(_check("rrule_valid", False, str(exc)))

    if expected.rrule is not None:
        checks.append(
            _check(
                "rrule_match",
                _normalize(rrule) == _normalize(expected.rrule),
                f"{rrule} vs {expected.rrule}",
            )
        )

    if rrule is not None and _needs_expansion(expected):
        checks.extend(_expansion_checks(rrule, proposal, expected))

    if expected.expect_conflict is not None:
        has = _has_conflict(case.fixtures, proposal)
        checks.append(
            _check(
                "conflict",
                has == expected.expect_conflict,
                f"has_conflict={has}, expected={expected.expect_conflict}",
            )
        )

    return CaseResult(id=case.id, passed=all(c.passed for c in checks), checks=checks)


def _needs_expansion(expected: ExpectedOutcome) -> bool:
    return bool(
        expected.forbid_weekdays
        or expected.require_weekdays
        or expected.occurrence_count is not None
        or expected.local_hour is not None
    )


def _expansion_checks(
    rrule: str, proposal: ScheduleProposal, expected: ExpectedOutcome
) -> list[CheckResult]:
    limit = expected.occurrence_count or 30
    occurrences = expand(rrule, proposal.start, limit=limit)
    checks: list[CheckResult] = []

    if expected.forbid_weekdays:
        banned = {_WEEKDAY_INDEX[d] for d in expected.forbid_weekdays}
        hits = [o.date().isoformat() for o in occurrences if o.weekday() in banned]
        checks.append(_check("forbid_weekdays", not hits, f"violations={hits[:3]}"))

    if expected.require_weekdays:
        allowed = {_WEEKDAY_INDEX[d] for d in expected.require_weekdays}
        hits = [o.date().isoformat() for o in occurrences if o.weekday() not in allowed]
        checks.append(_check("require_weekdays", not hits, f"violations={hits[:3]}"))

    if expected.occurrence_count is not None:
        checks.append(
            _check(
                "occurrence_count",
                len(occurrences) == expected.occurrence_count,
                str(len(occurrences)),
            )
        )

    if expected.local_hour is not None:
        hours = sorted({o.hour for o in occurrences})
        checks.append(_check("local_hour", hours == [expected.local_hour], str(hours)))

    return checks


def _has_conflict(fixtures: list[FixtureEvent], proposal: ScheduleProposal) -> bool:
    cal = FakeCalendarTools()
    for fixture in fixtures:
        if fixture.time == "-1":
            cal.add_event(fixture.name, fixture.date, duration=fixture.duration)
        else:
            cal.add_recurring_event(
                fixture.name,
                fixture.date,
                fixture.rrule or "FREQ=DAILY",
                time=fixture.time,
                duration=fixture.duration,
            )
    dt = proposal.start.astimezone(UTC)
    return cal.check_conflicts(
        dt.strftime("%Y%m%d"), dt.strftime("%H%M%S"), proposal.duration_minutes
    ).has_conflict


def _check(name: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def _normalize(rrule: str | None) -> str | None:
    return rrule.upper().replace(" ", "") if rrule is not None else None
