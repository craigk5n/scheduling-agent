# Schema Audit: WebCalendar recurrence ↔ RFC 5545 RRULE

**Status:** Phase 1, task 1 (complete)
**Date:** 2026-07-14
**Source:** `craigk5n/webcalendar` @ `master` (read from local checkout
`/var/www/html/webcalendar`)

## Purpose

Determine exactly what recurrence WebCalendar can **store** and
**correctly expand**, so we can fix the RRULE subset the agent is
allowed to emit (ARCHITECTURE §3) and the server-side validation the
new `add_recurring_event` MCP tool must enforce. This document is the
spec the Phase 2 RRULE validator's tests are written against.

## Evidence map

| Concern | File · lines |
|---|---|
| Table definitions | `wizard/shared/tables-mysql.sql` |
| RRULE builder (columns → RRULE) | `includes/xcal.php:375–461` |
| RRULE parser (RRULE → internal) | `includes/xcal.php:2875–2948` |
| Frequency-code → `cal_type` map | `includes/xcal.php:2588–2589` (`RepeatType()`) |
| DB insert (internal → columns) | `includes/xcal.php:1695–1745` |
| Occurrence expansion engine | `includes/classes/RptEvent.php:169–193` |

## Tables

### `webcal_entry_repeats` — one row per event (`PRIMARY KEY (cal_id)`)

| Column | Type | RFC 5545 meaning |
|---|---|---|
| `cal_id` | INT | Event id (FK to `webcal_entry`) |
| `cal_type` | VARCHAR(20) | Derived FREQ + monthly flavor (see below) |
| `cal_frequency` | INT default 1 | `INTERVAL` |
| `cal_count` | INT null | `COUNT` (sentinel `999` = infinite) |
| `cal_end` | INT (YYYYMMDD) | `UNTIL` date (local) |
| `cal_endtime` | INT null | `UNTIL` time-of-day (local) |
| `cal_bymonth` | VARCHAR(50) | `BYMONTH` (verbatim, e.g. `1,6,12`) |
| `cal_bymonthday` | VARCHAR(100) | `BYMONTHDAY` (e.g. `1,15,-1`) |
| `cal_byday` | VARCHAR(100) | `BYDAY` (e.g. `MO,WE,FR` or `2MO,-1FR`) |
| `cal_bysetpos` | VARCHAR(50) | `BYSETPOS` (e.g. `-1`) |
| `cal_byweekno` | VARCHAR(50) | `BYWEEKNO` |
| `cal_byyearday` | VARCHAR(50) | `BYYEARDAY` |
| `cal_wkst` | CHAR(2) default `MO` | `WKST` |
| `cal_days` | CHAR(7) | **Legacy, NO LONGER USED** (per schema comment) |

The `BY*` columns store RFC values **verbatim** as comma-separated
strings — the parser writes `$match[1]` (everything after `=`)
straight through, and the builder emits them unchanged. No
transformation, so the agent can emit standard RRULE `BY*` values and
they round-trip.

### `webcal_entry_repeats_not` — EXDATE / RDATE

| Column | Type | Meaning |
|---|---|---|
| `cal_id` | INT | Event id |
| `cal_date` | INT (YYYYMMDD) | The excepted/added date |
| `cal_exdate` | INT(1) default 1 | `1` = EXDATE (exclude), `0` = RDATE (include) |

## `cal_type` derivation

`RepeatType()` maps an internal frequency code to a `cal_type` string
(`xcal.php:2588`):

```
index:   0    1        2         3              4              5                  6         7
value:   0  'daily'  'weekly'  'monthlyByDay'  'monthlyByDate'  'monthlyBySetPos'  'yearly'  'manual'
```

The parser assigns the code (`xcal.php:2890–2939`):

| RRULE input | code | `cal_type` |
|---|---|---|
| `FREQ=DAILY` | 1 | `daily` |
| `FREQ=WEEKLY` | 2 | `weekly` |
| `FREQ=MONTHLY` | 3 | `monthlyByDay` |
| `FREQ=MONTHLY` + `BYSETPOS` (and not yearly) | 5 | `monthlyBySetPos` |
| `FREQ=YEARLY` | 6 | `yearly` |
| RDATE/EXDATE only (no RRULE) | 7 | `manual` |

**Quirk (documented, low-risk):** a `FREQ=MONTHLY;BYMONTHDAY=…` rule is
labelled `monthlyByDay`, not `monthlyByDate` — the line that would
switch it is commented out (`xcal.php:2934`). This is cosmetic:
the expansion engine (`RptEvent`) is driven by the actual `BY*`
columns, **not** by the `cal_type` label (it receives `cal_bymonthday`,
`cal_byday`, `cal_bysetpos`, … directly — `RptEvent.php:187–193`). So
occurrence generation is correct regardless of the label. To confirm
end-to-end, the eval suite compares live expansion against
`dateutil.rrule` (see Verification below).

## Supported RRULE subset (what the agent MAY emit)

**FREQ** — exactly one of: `DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY`.

**Rule parts, all honored by both storage and expansion:**
`INTERVAL`, `COUNT`, `UNTIL`, `BYMONTH`, `BYMONTHDAY`, `BYDAY`
(with numeric offsets like `2MO`, `-1FR`), `BYSETPOS`, `BYWEEKNO`,
`BYYEARDAY`, `WKST`.

**EXDATE / RDATE** — supported via `webcal_entry_repeats_not`
(per-date rows).

**COUNT vs UNTIL** — mutually exclusive (RFC rule); the builder emits
UNTIL if present, else COUNT (`xcal.php:444–455`). The agent must set
at most one.

## NOT supported (the agent MUST NOT emit these)

| Feature | Behavior in WebCalendar | Evidence |
|---|---|---|
| `FREQ=HOURLY/MINUTELY/SECONDLY` | Import **aborts the whole event** | `xcal.php:2899–2906` |
| `BYHOUR`, `BYMINUTE`, `BYSECOND` | Parsed, warned, **silently ignored** | `xcal.php:2917–2925` |
| Multiple RRULEs per event | Impossible — `PRIMARY KEY (cal_id)` = one repeat row | schema |
| `BYYEARDAY` on **export** | Stored + expanded, but the RRULE **builder omits it** | `xcal.php:375–461` (no BYYEARDAY branch) |

The `BYYEARDAY` export gap means an event created with BYYEARDAY would
expand correctly but not survive an iCal export round-trip. Treat
BYYEARDAY as **write-supported but not export-safe**; the agent should
avoid it in v1 unless a use case demands it.

## Correctness caveats to carry into implementation

1. **`UNTIL` is stored local, not UTC.** The insert converts the
   UNTIL timestamp via `localtime()` into `cal_end` (YYYYMMDD) +
   `cal_endtime` (`xcal.php:1743–1744`). RFC 5545 expects UNTIL in UTC
   for zoned DTSTART. The `add_recurring_event` MCP tool must replicate
   WebCalendar's local conversion, and the eval suite must include an
   UNTIL-crossing-a-DST-boundary case.
2. **`COUNT=999` is an "infinite" sentinel** (`xcal.php:453`). The
   agent should never emit a literal `COUNT=999`; use no COUNT/UNTIL
   for open-ended series.
3. **Column width limits** (validation bounds): `cal_byday`
   VARCHAR(100), `cal_bymonthday` VARCHAR(100), `cal_bymonth`/
   `cal_bysetpos`/`cal_byweekno`/`cal_byyearday` VARCHAR(50),
   `cal_type` VARCHAR(20), `cal_wkst` CHAR(2). Validation should reject
   `BY*` lists that would overflow these (only reachable with
   pathological rules, but the server tool must not truncate silently).
4. **Times are GMT in `webcal_entry`.** As the existing `mcp.php`
   read tools already handle (GMT↔local conversion), the recurrence
   write tool must store the base event consistently with how
   `add_event` does today.

## Implications for the next tasks

- **Phase 2 RRULE validator** (`scheduling_agent`): enforce the
  supported subset above as the *hard* contract — allowed FREQ set,
  allowed parts, COUNT/UNTIL exclusivity, no sub-daily, no
  BYHOUR/BYMINUTE/BYSECOND, width bounds, avoid BYYEARDAY in v1. Tests
  derive directly from this document.
- **Phase 1 `add_recurring_event` MCP tool** (`webcalendar/mcp.php`):
  mirror the `xcal.php:1695–1745` insert path (verbatim `BY*` storage,
  `RepeatType()` label, local UNTIL conversion, `webcal_entry` +
  `webcal_entry_repeats` in one logical write), and re-validate the
  subset server-side (defense in depth — never trust the client).
- **Eval suite:** compare WebCalendar's live occurrence expansion
  against `dateutil.rrule` for each supported construct, with explicit
  DST-boundary and UNTIL cases, to certify the label quirk and the
  local-UNTIL conversion are behaviorally correct.

## Open items requiring the LIVE instance (not resolvable by reading code)

- Confirm the running instance's `webcal_entry_repeats` matches this
  (MySQL) definition — no local migrations/drift.
- Verify live expansion vs `dateutil.rrule` on the edge cases above
  (this is a Phase 3 eval, run in live-smoke mode).
