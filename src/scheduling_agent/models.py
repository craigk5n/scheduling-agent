"""Pydantic domain models for the scheduling agent.

These are the structured contracts that cross node boundaries in the graph and
that the LLM is constrained to emit. Nothing free-form is trusted: timezones,
recurrence, and action-specific requirements are validated here.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

WEEKDAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")


class Frequency(StrEnum):
    """Recurrence frequencies WebCalendar can store and expand."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


class ScheduleAction(StrEnum):
    """What the user wants the agent to do."""

    CREATE = "create"
    CREATE_RECURRING = "create_recurring"
    UPDATE = "update"
    DELETE = "delete"
    QUERY = "query"


class RecurrenceSpec(BaseModel):
    """A structured recurrence rule (serialized to an RFC 5545 RRULE elsewhere).

    Field-level validation is intentionally light here; the RFC-subset rules
    (BYDAY token grammar, value ranges, column-width bounds) live in the rrule
    module so both this model and raw strings share one validator.
    """

    freq: Frequency
    interval: int = Field(default=1, ge=1)
    count: int | None = Field(default=None, ge=1)
    until: date | None = None
    by_day: list[str] = Field(default_factory=list)
    by_month_day: list[int] = Field(default_factory=list)
    by_month: list[int] = Field(default_factory=list)
    by_set_pos: list[int] = Field(default_factory=list)
    by_week_no: list[int] = Field(default_factory=list)
    wkst: str = "MO"

    @field_validator("wkst")
    @classmethod
    def _wkst_is_weekday(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in WEEKDAYS:
            raise ValueError(f"wkst must be one of {', '.join(WEEKDAYS)}")
        return up

    @model_validator(mode="after")
    def _count_until_exclusive(self) -> RecurrenceSpec:
        if self.count is not None and self.until is not None:
            raise ValueError("count and until are mutually exclusive")
        return self


class ScheduleProposal(BaseModel):
    """The agent's structured plan for a single scheduling action."""

    action: ScheduleAction
    title: str = ""
    timezone: str
    start: AwareDatetime
    duration_minutes: int = Field(default=0, ge=0)
    recurrence: RecurrenceSpec | None = None
    location: str = ""
    description: str = ""
    participants: list[str] = Field(default_factory=list)
    target_event_id: int | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _action_requirements(self) -> ScheduleProposal:
        if (
            self.action in (ScheduleAction.UPDATE, ScheduleAction.DELETE)
            and self.target_event_id is None
            and not self.title.strip()
        ):
            # The agent can resolve the id from a title (search_events); require
            # at least one way to identify the target event.
            raise ValueError(
                f"{self.action.value} requires target_event_id or a title "
                "to find the event"
            )
        if self.action is ScheduleAction.CREATE_RECURRING and self.recurrence is None:
            raise ValueError("create_recurring requires a recurrence spec")
        if (
            self.action in (ScheduleAction.CREATE, ScheduleAction.CREATE_RECURRING)
            and not self.title.strip()
        ):
            raise ValueError(f"{self.action.value} requires a title")
        return self


class BusyBlock(BaseModel):
    """A busy time block returned by get_availability (GMT frame)."""

    id: int
    name: str
    date: str
    time: str
    duration: int = 0


class Conflict(BaseModel):
    """An existing event overlapping a proposed slot."""

    id: int
    name: str
    date: str
    time: str
    duration: int = 0


class AvailabilityResult(BaseModel):
    """Result of a get_availability tool call."""

    busy: list[BusyBlock] = Field(default_factory=list)
    all_day: list[dict[str, object]] = Field(default_factory=list)
    timezone: str = "GMT"


class ConflictResult(BaseModel):
    """Result of a check_conflicts tool call."""

    has_conflict: bool = False
    conflicts: list[Conflict] = Field(default_factory=list)


class WriteResult(BaseModel):
    """Outcome of a create/update/delete tool call."""

    success: bool
    event_id: int | None = None
    cal_type: str | None = None
    error: str | None = None
