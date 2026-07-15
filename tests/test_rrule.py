"""Tests for RRULE building, validation, expansion, and description.

The validation subset mirrors WebCalendar's server-side mcp_validate_rrule
(docs/SCHEMA_AUDIT.md); expansion uses python-dateutil and must be DST-correct.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from scheduling_agent.models import Frequency, RecurrenceSpec
from scheduling_agent.rrule import (
    RruleError,
    build_rrule,
    describe_rrule,
    expand,
    validate_rrule,
)

NY = ZoneInfo("America/New_York")


# --- build_rrule --------------------------------------------------------------


def test_build_weekly_byday() -> None:
    r = build_rrule(RecurrenceSpec(freq=Frequency.WEEKLY, by_day=["MO", "WE", "FR"]))
    assert r == "FREQ=WEEKLY;BYDAY=MO,WE,FR"


def test_build_daily_interval() -> None:
    assert build_rrule(RecurrenceSpec(freq=Frequency.DAILY, interval=2)) == (
        "FREQ=DAILY;INTERVAL=2"
    )


def test_build_with_count() -> None:
    r = build_rrule(RecurrenceSpec(freq=Frequency.DAILY, count=10))
    assert "COUNT=10" in r and "UNTIL" not in r


def test_build_with_until_is_end_of_day_utc() -> None:
    r = build_rrule(RecurrenceSpec(freq=Frequency.DAILY, until=date(2030, 12, 31)))
    assert "UNTIL=20301231T235959Z" in r


def test_build_monthly_setpos() -> None:
    r = build_rrule(
        RecurrenceSpec(freq=Frequency.MONTHLY, by_day=["MO"], by_set_pos=[1])
    )
    assert "FREQ=MONTHLY" in r and "BYDAY=MO" in r and "BYSETPOS=1" in r


def test_build_with_bymonth_and_byweekno() -> None:
    r = build_rrule(
        RecurrenceSpec(freq=Frequency.YEARLY, by_month=[6, 12], by_week_no=[1])
    )
    assert "BYMONTH=6,12" in r and "BYWEEKNO=1" in r
    validate_rrule(r)


def test_build_output_validates() -> None:
    # Anything we build must pass our own validator.
    spec = RecurrenceSpec(
        freq=Frequency.MONTHLY, interval=2, by_month_day=[1, 15, -1], wkst="SU"
    )
    validate_rrule(build_rrule(spec))


# --- validate_rrule -----------------------------------------------------------


def test_validate_accepts_supported() -> None:
    parts = validate_rrule("FREQ=WEEKLY;BYDAY=MO,WE;INTERVAL=2")
    assert parts["FREQ"] == "WEEKLY"
    assert parts["BYDAY"] == "MO,WE"


def test_validate_normalizes_prefix_and_case() -> None:
    parts = validate_rrule("rrule:freq=daily")
    assert parts["FREQ"] == "DAILY"


def test_validate_rejects_subdaily() -> None:
    with pytest.raises(RruleError, match="HOURLY"):
        validate_rrule("FREQ=HOURLY")


def test_validate_rejects_byhour() -> None:
    with pytest.raises(RruleError, match="BYHOUR"):
        validate_rrule("FREQ=DAILY;BYHOUR=9")


def test_validate_rejects_count_and_until() -> None:
    with pytest.raises(RruleError):
        validate_rrule("FREQ=DAILY;COUNT=5;UNTIL=20301231T000000Z")


def test_validate_rejects_count_999_sentinel() -> None:
    with pytest.raises(RruleError, match="999"):
        validate_rrule("FREQ=DAILY;COUNT=999")


def test_validate_rejects_bad_byday() -> None:
    with pytest.raises(RruleError, match="BYDAY"):
        validate_rrule("FREQ=WEEKLY;BYDAY=XY")


def test_validate_rejects_missing_freq() -> None:
    with pytest.raises(RruleError, match="FREQ"):
        validate_rrule("BYDAY=MO")


@pytest.mark.parametrize(
    "rule",
    [
        "",
        "   ",
        "FREQ=DAILY;NOEQUALS",
        "FREQ=DAILY;FREQ=WEEKLY",  # duplicate
        "FREQ=DAILY;FOO=BAR",  # unknown
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;INTERVAL=-1",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;UNTIL=nope",
        "FREQ=WEEKLY;BYDAY=MO;WKST=XX",
        "FREQ=YEARLY;BYMONTH=13",
        "FREQ=YEARLY;BYMONTH=-1",  # negative not allowed
        "FREQ=MONTHLY;BYMONTHDAY=0",
        "FREQ=MONTHLY;BYMONTHDAY=32",
        "FREQ=MONTHLY;BYSETPOS=x",  # non-numeric
        "FREQ=WEEKLY;BYDAY=" + ",".join(["MO"] * 40),  # width overflow
    ],
)
def test_validate_rejects_invalid(rule: str) -> None:
    with pytest.raises(RruleError):
        validate_rrule(rule)


def test_validate_ignores_trailing_semicolon() -> None:
    assert validate_rrule("FREQ=DAILY;")["FREQ"] == "DAILY"


def test_describe_until_and_bymonthday() -> None:
    assert "until 20301231" in describe_rrule("FREQ=DAILY;UNTIL=20301231T235959Z")
    assert "day 15" in describe_rrule("FREQ=MONTHLY;BYMONTHDAY=15")


def test_describe_interval_plural() -> None:
    assert describe_rrule("FREQ=WEEKLY;INTERVAL=2").startswith("Every 2 weeks")


# --- expand -------------------------------------------------------------------


def test_expand_weekly_only_selected_days() -> None:
    # Weekly MO,WE starting Mon 2026-08-03; no occurrence should be a Friday.
    dtstart = datetime(2026, 8, 3, 9, 0, tzinfo=NY)
    occ = expand("FREQ=WEEKLY;BYDAY=MO,WE", dtstart, limit=6)
    assert len(occ) == 6
    weekdays = {d.weekday() for d in occ}  # 0=Mon .. 4=Fri
    assert weekdays <= {0, 2}  # only Monday/Wednesday
    assert 4 not in weekdays  # never a Friday


def test_expand_count_terminates() -> None:
    dtstart = datetime(2026, 8, 3, 9, 0, tzinfo=NY)
    occ = expand("FREQ=DAILY;COUNT=3", dtstart, limit=100)
    assert len(occ) == 3


def test_expand_preserves_wall_clock_across_dst() -> None:
    # US spring-forward is 2026-03-08. A 09:00 daily event must stay 09:00
    # local on both sides, while its UTC offset shifts -5h -> -4h.
    dtstart = datetime(2026, 3, 7, 9, 0, tzinfo=NY)
    occ = expand("FREQ=DAILY;COUNT=3", dtstart, limit=3)
    assert [d.hour for d in occ] == [9, 9, 9]  # wall clock unchanged
    before_utc = occ[0].astimezone(UTC).hour  # Mar 7 -> 14:00Z (UTC-5)
    after_utc = occ[2].astimezone(UTC).hour  # Mar 9 -> 13:00Z (UTC-4)
    assert before_utc == 14
    assert after_utc == 13


def test_expand_until_is_inclusive_of_last_day() -> None:
    dtstart = datetime(2026, 8, 3, 9, 0, tzinfo=NY)
    r = build_rrule(RecurrenceSpec(freq=Frequency.DAILY, until=date(2026, 8, 5)))
    occ = expand(r, dtstart, limit=100)
    assert occ[-1].date() == date(2026, 8, 5)  # end-of-day UNTIL includes Aug 5


# --- describe_rrule -----------------------------------------------------------


def test_describe_weekly() -> None:
    text = describe_rrule("FREQ=WEEKLY;BYDAY=MO,WE,FR")
    assert "week" in text.lower()
    assert "Mon" in text and "Fri" in text


def test_describe_with_count() -> None:
    assert "10 times" in describe_rrule("FREQ=DAILY;COUNT=10")
