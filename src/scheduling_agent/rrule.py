"""RFC 5545 RRULE building, validation, expansion, and description.

The validation subset is the Python twin of WebCalendar's server-side
``mcp_validate_rrule`` (docs/SCHEMA_AUDIT.md): the agent validates before
sending so it never proposes a rule the backend would reject. Expansion uses
python-dateutil and preserves wall-clock time across DST transitions.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from dateutil.rrule import rrulestr

from scheduling_agent.models import RecurrenceSpec

WEEKDAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
_DAY_NAME = {
    "SU": "Sun",
    "MO": "Mon",
    "TU": "Tue",
    "WE": "Wed",
    "TH": "Thu",
    "FR": "Fri",
    "SA": "Sat",
}
_UNIT = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}

_SUPPORTED_FREQ = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_KNOWN = (
    "FREQ",
    "INTERVAL",
    "COUNT",
    "UNTIL",
    "BYMONTH",
    "BYMONTHDAY",
    "BYDAY",
    "BYSETPOS",
    "BYWEEKNO",
    "BYYEARDAY",
    "WKST",
)
_UNSUPPORTED = ("BYHOUR", "BYMINUTE", "BYSECOND")
_WIDTH = {
    "BYMONTH": 50,
    "BYMONTHDAY": 100,
    "BYDAY": 100,
    "BYSETPOS": 50,
    "BYWEEKNO": 50,
    "BYYEARDAY": 50,
    "WKST": 2,
}
_BYDAY_RE = re.compile(r"^([+-]?([1-9]|[1-4][0-9]|5[0-3]))?(SU|MO|TU|WE|TH|FR|SA)$")
_UNTIL_RE = re.compile(r"^\d{8}(T\d{6}Z?)?$")
_INT_RANGES = {
    # name: (min, max, allow_negative)
    "BYMONTH": (1, 12, False),
    "BYMONTHDAY": (1, 31, True),
    "BYSETPOS": (1, 366, True),
    "BYWEEKNO": (1, 53, True),
    "BYYEARDAY": (1, 366, True),
}


class RruleError(ValueError):
    """Raised when an RRULE is outside the WebCalendar-supported subset."""


def build_rrule(spec: RecurrenceSpec) -> str:
    """Serialize a RecurrenceSpec to an RRULE string within the supported subset.

    UNTIL is emitted as an end-of-day UTC datetime so the final day is included
    when the rule is expanded.
    """
    parts = [f"FREQ={spec.freq.value}"]
    if spec.interval > 1:
        parts.append(f"INTERVAL={spec.interval}")
    if spec.by_month:
        parts.append("BYMONTH=" + ",".join(str(v) for v in spec.by_month))
    if spec.by_month_day:
        parts.append("BYMONTHDAY=" + ",".join(str(v) for v in spec.by_month_day))
    if spec.by_day:
        parts.append("BYDAY=" + ",".join(spec.by_day))
    if spec.by_week_no:
        parts.append("BYWEEKNO=" + ",".join(str(v) for v in spec.by_week_no))
    if spec.by_set_pos:
        parts.append("BYSETPOS=" + ",".join(str(v) for v in spec.by_set_pos))
    if spec.wkst != "MO":
        parts.append(f"WKST={spec.wkst}")
    if spec.until is not None:
        parts.append("UNTIL=" + spec.until.strftime("%Y%m%d") + "T235959Z")
    elif spec.count is not None:
        parts.append(f"COUNT={spec.count}")
    return ";".join(parts)


def validate_rrule(rrule: str) -> dict[str, str]:
    """Validate an RRULE against the supported subset; return normalized parts.

    Raises:
        RruleError: if the rule is empty, malformed, uses an unsupported part,
            or carries an out-of-range/overflowing value.
    """
    text = re.sub(r"^RRULE:", "", rrule.strip(), flags=re.IGNORECASE).strip()
    if not text:
        raise RruleError("RRULE is required")

    parts: dict[str, str] = {}
    for token in text.split(";"):
        if not token:
            continue
        if "=" not in token:
            raise RruleError(f"Malformed RRULE part: {token}")
        key, _, value = token.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if key in parts:
            raise RruleError(f"Duplicate RRULE part: {key}")
        if key in _UNSUPPORTED:
            raise RruleError(f"Unsupported RRULE part: {key}")
        if key not in _KNOWN:
            raise RruleError(f"Unknown RRULE part: {key}")
        parts[key] = value

    if "FREQ" not in parts:
        raise RruleError("RRULE must include FREQ")
    parts["FREQ"] = parts["FREQ"].upper()
    if parts["FREQ"] not in _SUPPORTED_FREQ:
        raise RruleError(f"Unsupported FREQ: {parts['FREQ']}")

    if "COUNT" in parts and "UNTIL" in parts:
        raise RruleError("RRULE may not set both COUNT and UNTIL")

    if "INTERVAL" in parts and not _positive_int(parts["INTERVAL"]):
        raise RruleError("INTERVAL must be a positive integer")

    if "COUNT" in parts:
        if not _positive_int(parts["COUNT"]):
            raise RruleError("COUNT must be a positive integer")
        if int(parts["COUNT"]) == 999:
            raise RruleError("COUNT=999 is reserved as WebCalendar's infinite sentinel")

    if "UNTIL" in parts and not _UNTIL_RE.match(parts["UNTIL"]):
        raise RruleError("UNTIL must be YYYYMMDD or YYYYMMDDTHHMMSSZ")

    if "WKST" in parts:
        parts["WKST"] = parts["WKST"].upper()
        if parts["WKST"] not in WEEKDAYS:
            raise RruleError("WKST must be a weekday abbreviation")

    if "BYDAY" in parts:
        parts["BYDAY"] = parts["BYDAY"].upper()
        for tok in parts["BYDAY"].split(","):
            if not _BYDAY_RE.match(tok):
                raise RruleError(f"Invalid BYDAY value: {tok}")

    for name, (lo, hi, allow_neg) in _INT_RANGES.items():
        if name in parts:
            _check_int_list(name, parts[name], lo, hi, allow_neg=allow_neg)

    for name, width in _WIDTH.items():
        if name in parts and len(parts[name]) > width:
            raise RruleError(f"{name} exceeds the {width}-character column limit")

    return parts


def expand(rrule: str, dtstart: datetime, limit: int = 10) -> list[datetime]:
    """Expand an RRULE to at most ``limit`` occurrences from ``dtstart``.

    ``dtstart`` should be timezone-aware; occurrences keep its wall-clock time
    across DST boundaries (python-dateutil does calendar arithmetic on the
    aware datetime rather than shifting the offset).
    """
    spec = rrule if rrule.upper().startswith("RRULE") else f"RRULE:{rrule}"
    rule: Any = rrulestr(spec, dtstart=dtstart)
    occurrences: list[datetime] = []
    for i, occ in enumerate(rule):
        if i >= limit:
            break
        occurrences.append(occ)
    return occurrences


def describe_rrule(rrule: str) -> str:
    """Return a short human-readable description of an RRULE (for HITL display)."""
    parts = validate_rrule(rrule)
    unit = _UNIT[parts["FREQ"]]
    interval = int(parts.get("INTERVAL", "1"))
    text = f"Every {unit}" if interval == 1 else f"Every {interval} {unit}s"

    if "BYDAY" in parts:
        days = ", ".join(_describe_byday(tok) for tok in parts["BYDAY"].split(","))
        text += f" on {days}"
    if "BYMONTHDAY" in parts:
        text += f" (day {parts['BYMONTHDAY']} of the month)"

    if "COUNT" in parts:
        text += f", {parts['COUNT']} times"
    elif "UNTIL" in parts:
        text += f", until {parts['UNTIL'][:8]}"
    return text


def _describe_byday(token: str) -> str:
    match = _BYDAY_RE.match(token)
    if match is None:  # pragma: no cover - validate_rrule already guarantees match
        return token
    offset, _, day = match.group(1), match.group(2), token[-2:]
    name = _DAY_NAME[day]
    return f"{offset}{name}" if offset else name


def _positive_int(value: str) -> bool:
    return value.isdigit() and int(value) >= 1


def _check_int_list(
    name: str, value: str, lo: int, hi: int, *, allow_neg: bool
) -> None:
    for item in value.split(","):
        if not re.match(r"^-?\d+$", item):
            raise RruleError(f"Invalid {name} value: {item}")
        n = int(item)
        if n == 0:
            raise RruleError(f"{name} value may not be zero")
        if not allow_neg and n < 0:
            raise RruleError(f"{name} value may not be negative: {item}")
        if not (lo <= abs(n) <= hi):
            raise RruleError(f"{name} value out of range: {item}")
