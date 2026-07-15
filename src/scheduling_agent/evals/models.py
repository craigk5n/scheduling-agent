"""Data models for the evaluation suite."""

from __future__ import annotations

from pydantic import BaseModel, Field

from scheduling_agent.models import ScheduleAction, ScheduleProposal


class FixtureEvent(BaseModel):
    """A pre-existing calendar event used to test conflict awareness."""

    name: str
    date: str
    time: str = "-1"
    duration: int = 0
    rrule: str | None = None


class ExpectedOutcome(BaseModel):
    """The semantics a correct proposal must satisfy for a case."""

    action: ScheduleAction
    rrule: str | None = None
    forbid_weekdays: list[str] = Field(default_factory=list)
    require_weekdays: list[str] = Field(default_factory=list)
    occurrence_count: int | None = None
    local_hour: int | None = None
    expect_conflict: bool | None = None


class EvalCase(BaseModel):
    """One golden case: an NL request, fixtures, a reference proposal, and the
    expected outcome semantics."""

    id: str
    request: str
    timezone: str
    today: str
    fixtures: list[FixtureEvent] = Field(default_factory=list)
    reference: ScheduleProposal
    expected: ExpectedOutcome


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    id: str
    passed: bool
    checks: list[CheckResult]


class EvalReport(BaseModel):
    provider: str
    total: int
    passed: int
    pass_rate: float
    cases: list[CaseResult]
