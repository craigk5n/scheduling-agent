"""Calendar tool clients: the boundary between the agent and WebCalendar.

`CalendarTools` is the protocol the graph depends on. Two implementations:

- `FakeCalendarTools`: an in-memory backend used for unit tests and evals
  (the "mock MCP server"), mirroring the semantics of the real tools.
- `HttpMcpCalendarTools`: talks to a live WebCalendar ``mcp.php`` over its
  custom HTTP JSON-RPC transport (httpx).

All dates/times are the GMT storage frame, matching the MCP tool contract.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from scheduling_agent.models import (
    AvailabilityResult,
    BusyBlock,
    Conflict,
    ConflictResult,
    WriteResult,
)
from scheduling_agent.observability import log_event
from scheduling_agent.rrule import RruleError, validate_rrule


class McpError(RuntimeError):
    """Raised when the MCP server returns a JSON-RPC error."""


@runtime_checkable
class CalendarTools(Protocol):
    """The calendar operations the agent graph relies on (GMT frame)."""

    def list_events(self, start_date: str, end_date: str) -> list[BusyBlock]: ...
    def search_events(self, keyword: str, limit: int = 50) -> list[BusyBlock]: ...
    def get_availability(
        self, start_date: str, end_date: str
    ) -> AvailabilityResult: ...
    def check_conflicts(
        self, date: str, time: str, duration: int
    ) -> ConflictResult: ...
    def add_event(
        self,
        name: str,
        date: str,
        time: str = "-1",
        description: str = "",
        location: str = "",
        duration: int = 0,
    ) -> WriteResult: ...
    def add_recurring_event(
        self,
        name: str,
        date: str,
        rrule: str,
        time: str = "-1",
        duration: int = 0,
        description: str = "",
        location: str = "",
    ) -> WriteResult: ...
    def update_event(
        self,
        event_id: int,
        *,
        name: str | None = None,
        date: str | None = None,
        time: str | None = None,
        duration: int | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> WriteResult: ...
    def delete_event(self, event_id: int) -> WriteResult: ...


def _to_minutes(date_str: str, time_str: str) -> int:
    """Absolute GMT minute count for a YYYYMMDD date and HHMMSS time."""
    d = int(date_str)
    t = int(str(time_str))
    dt = datetime(
        d // 10000,
        (d // 100) % 100,
        d % 100,
        (t // 10000) % 100,
        (t // 100) % 100,
        t % 100,
        tzinfo=UTC,
    )
    return int(dt.timestamp() // 60)


class FakeCalendarTools:
    """In-memory calendar backend mirroring the WebCalendar MCP tool semantics."""

    def __init__(self) -> None:
        self._events: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    def _add(self, event: dict[str, Any]) -> int:
        event_id = self._next_id
        self._next_id += 1
        self._events[event_id] = {"id": event_id, **event}
        return event_id

    def list_events(self, start_date: str, end_date: str) -> list[BusyBlock]:
        rows = [e for e in self._events.values() if start_date <= e["date"] <= end_date]
        rows.sort(key=lambda e: (e["date"], e["time"]))
        return [
            BusyBlock(
                id=e["id"],
                name=e["name"],
                date=e["date"],
                time=e["time"],
                duration=e["duration"],
            )
            for e in rows
        ]

    def search_events(self, keyword: str, limit: int = 50) -> list[BusyBlock]:
        needle = keyword.strip().lower()
        rows = sorted(
            (e for e in self._events.values() if needle in e["name"].lower()),
            key=lambda e: (e["date"], e["time"]),
        )[: max(0, limit)]
        return [
            BusyBlock(
                id=e["id"],
                name=e["name"],
                date=e["date"],
                time=e["time"],
                duration=e["duration"],
            )
            for e in rows
        ]

    def get_availability(self, start_date: str, end_date: str) -> AvailabilityResult:
        busy: list[BusyBlock] = []
        all_day: list[dict[str, object]] = []
        rows = sorted(
            (e for e in self._events.values() if start_date <= e["date"] <= end_date),
            key=lambda e: (e["date"], e["time"]),
        )
        for e in rows:
            if e["time"] == "-1":
                all_day.append({"id": e["id"], "name": e["name"], "date": e["date"]})
            else:
                busy.append(
                    BusyBlock(
                        id=e["id"],
                        name=e["name"],
                        date=e["date"],
                        time=e["time"],
                        duration=e["duration"],
                    )
                )
        return AvailabilityResult(busy=busy, all_day=all_day, timezone="GMT")

    def check_conflicts(self, date: str, time: str, duration: int) -> ConflictResult:
        start = _to_minutes(date, time)
        end = start + max(0, duration)
        conflicts: list[Conflict] = []
        for e in self._events.values():
            if e["time"] == "-1":
                continue
            s = _to_minutes(e["date"], e["time"])
            en = s + max(0, e["duration"])
            if start < en and s < end:
                conflicts.append(
                    Conflict(
                        id=e["id"],
                        name=e["name"],
                        date=e["date"],
                        time=e["time"],
                        duration=e["duration"],
                    )
                )
        return ConflictResult(has_conflict=bool(conflicts), conflicts=conflicts)

    def add_event(
        self,
        name: str,
        date: str,
        time: str = "-1",
        description: str = "",
        location: str = "",
        duration: int = 0,
    ) -> WriteResult:
        if not name.strip():
            return WriteResult(success=False, error="Event name is required")
        event_id = self._add(
            {
                "name": name,
                "date": date,
                "time": time,
                "duration": duration,
                "description": description,
                "location": location,
                "rrule": None,
            }
        )
        return WriteResult(success=True, event_id=event_id)

    def add_recurring_event(
        self,
        name: str,
        date: str,
        rrule: str,
        time: str = "-1",
        duration: int = 0,
        description: str = "",
        location: str = "",
    ) -> WriteResult:
        if not name.strip():
            return WriteResult(success=False, error="Event name is required")
        try:
            validate_rrule(rrule)
        except RruleError as exc:
            return WriteResult(success=False, error=f"Invalid RRULE: {exc}")
        event_id = self._add(
            {
                "name": name,
                "date": date,
                "time": time,
                "duration": duration,
                "description": description,
                "location": location,
                "rrule": rrule,
            }
        )
        return WriteResult(success=True, event_id=event_id)

    def update_event(
        self,
        event_id: int,
        *,
        name: str | None = None,
        date: str | None = None,
        time: str | None = None,
        duration: int | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> WriteResult:
        event = self._events.get(event_id)
        if event is None:
            return WriteResult(success=False, error="Event not found")
        for field, value in {
            "name": name,
            "date": date,
            "time": time,
            "duration": duration,
            "description": description,
            "location": location,
        }.items():
            if value is not None:
                event[field] = value
        return WriteResult(success=True, event_id=event_id)

    def delete_event(self, event_id: int) -> WriteResult:
        if event_id not in self._events:
            return WriteResult(success=False, error="Event not found")
        del self._events[event_id]
        return WriteResult(success=True, event_id=event_id)


class HttpMcpCalendarTools:
    """CalendarTools backed by a live WebCalendar mcp.php (HTTP JSON-RPC)."""

    def __init__(
        self, base_url: str, token: str, client: httpx.Client | None = None
    ) -> None:
        self._url = base_url
        self._token = token
        self._client = client or httpx.Client(timeout=30.0)

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        log_event("mcp_request", tool=name)
        start = time.monotonic()
        ok = False
        try:
            result = self._do_call(name, arguments)
            ok = True
            return result
        finally:
            log_event(
                "mcp_result",
                tool=name,
                ok=ok,
                elapsed_ms=round((time.monotonic() - start) * 1000),
            )

    def _do_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Transport faults (timeouts, dropped connections, HTTP errors — e.g.
        # k5n-mcp-hub's injected 504) are wrapped into McpError so callers see
        # one clean failure type instead of assorted httpx exceptions.
        try:
            response = self._client.post(
                self._url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={
                    "X-MCP-Token": self._token,
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            snippet = " ".join(exc.response.text.split())[:200]
            raise McpError(
                f"MCP HTTP {exc.response.status_code} calling {name} "
                f"at {self._url} — check MCP_URL. Response: {snippet}"
            ) from exc
        except httpx.HTTPError as exc:
            raise McpError(f"MCP request failed calling {name}: {exc}") from exc

        # A malformed or non-JSON body (corrupt response, dropped SSE stream)
        # must not crash the caller.
        try:
            data = response.json()
        except ValueError as exc:
            raise McpError(f"MCP returned malformed JSON calling {name}") from exc

        if not isinstance(data, dict):
            raise McpError(f"MCP returned a non-object response calling {name}")
        if data.get("error"):
            error = data["error"]
            message = error.get("message", error) if isinstance(error, dict) else error
            raise McpError(str(message))
        result = data.get("result")
        if not isinstance(result, dict):
            raise McpError(f"MCP response missing a result object calling {name}")
        return result

    @staticmethod
    def _write_result(result: dict[str, Any]) -> WriteResult:
        if "error" in result:
            return WriteResult(success=False, error=str(result["error"]))
        return WriteResult(
            success=bool(result.get("success")),
            event_id=result.get("event_id"),
            cal_type=result.get("cal_type"),
        )

    def list_events(self, start_date: str, end_date: str) -> list[BusyBlock]:
        result = self._call(
            "list_events", {"start_date": start_date, "end_date": end_date}
        )
        return [BusyBlock(**e) for e in result.get("events", [])]

    def search_events(self, keyword: str, limit: int = 50) -> list[BusyBlock]:
        result = self._call("search_events", {"keyword": keyword, "limit": limit})
        return [BusyBlock(**e) for e in result.get("events", [])]

    def get_availability(self, start_date: str, end_date: str) -> AvailabilityResult:
        result = self._call(
            "get_availability", {"start_date": start_date, "end_date": end_date}
        )
        return AvailabilityResult(**result)

    def check_conflicts(self, date: str, time: str, duration: int) -> ConflictResult:
        result = self._call(
            "check_conflicts", {"date": date, "time": time, "duration": duration}
        )
        return ConflictResult(**result)

    def add_event(
        self,
        name: str,
        date: str,
        time: str = "-1",
        description: str = "",
        location: str = "",
        duration: int = 0,
    ) -> WriteResult:
        return self._write_result(
            self._call(
                "add_event",
                {
                    "name": name,
                    "date": date,
                    "time": time,
                    "description": description,
                    "location": location,
                    "duration": duration,
                },
            )
        )

    def add_recurring_event(
        self,
        name: str,
        date: str,
        rrule: str,
        time: str = "-1",
        duration: int = 0,
        description: str = "",
        location: str = "",
    ) -> WriteResult:
        return self._write_result(
            self._call(
                "add_recurring_event",
                {
                    "name": name,
                    "date": date,
                    "rrule": rrule,
                    "time": time,
                    "duration": duration,
                    "description": description,
                    "location": location,
                },
            )
        )

    def update_event(
        self,
        event_id: int,
        *,
        name: str | None = None,
        date: str | None = None,
        time: str | None = None,
        duration: int | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> WriteResult:
        arguments: dict[str, Any] = {"event_id": event_id}
        for field, value in {
            "name": name,
            "date": date,
            "time": time,
            "duration": duration,
            "description": description,
            "location": location,
        }.items():
            if value is not None:
                arguments[field] = value
        return self._write_result(self._call("update_event", arguments))

    def delete_event(self, event_id: int) -> WriteResult:
        return self._write_result(self._call("delete_event", {"event_id": event_id}))
