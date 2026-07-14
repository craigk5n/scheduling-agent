# Architecture: WebCalendar Scheduling Agent

**Status:** Draft v1 (planning phase)
**Date:** 2026-07-14

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────┐
│  scheduling-agent (Python, new repo)                        │
│                                                             │
│  CLI chat (v1)          Web UI (Phase 4)                    │
│       │                      │                              │
│       ▼                      ▼                              │
│  ┌───────────────────────────────────────────┐              │
│  │ LangGraph StateGraph                      │              │
│  │                                           │              │
│  │  parse_intent → gather_context →          │              │
│  │  propose → [HITL interrupt] →             │              │
│  │  execute → verify → respond               │              │
│  │                                           │              │
│  │  SQLite checkpointer (resume across       │              │
│  │  restarts, approval round-trips)          │              │
│  └───────────────────┬───────────────────────┘              │
│                      │ langchain-mcp-adapters (MCP client)  │
└──────────────────────┼──────────────────────────────────────┘
                       │  JSON-RPC over HTTP
                       ▼
        ┌──────────────────────────────┐
        │ k5n-mcp-hub (optional proxy) │  ← fault injection for
        │  POST /mcp                   │    chaos testing
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────────────────────┐
        │ webcalendar/mcp.php          │
        │  existing: list_events,      │
        │   search_events,             │
        │   get_user_info, add_event   │
        │  NEW: get_availability,      │
        │   check_conflicts,           │
        │   add_recurring_event,       │
        │   update_event, delete_event │
        └──────────────┬───────────────┘
                       ▼
              WebCalendar MySQL DB
        (webcal_entry, webcal_entry_user,
         webcal_entry_repeats, ...)
```

Model: Claude via a pluggable provider layer (see §2a). Tracing:
LangSmith (OTel export optional).

## 2a. Model Provider Abstraction

The graph nodes depend only on a LangChain `BaseChatModel`; a single
factory (`get_chat_model()`) selects the backend from
`MODEL_PROVIDER` config. Three supported providers:

| Provider | Auth | Implementation | Notes |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `langchain-anthropic` `ChatAnthropic` | Reference path: full tool-calling + API-enforced structured output |
| `openrouter` | `OPENROUTER_API_KEY` | `langchain-openai` `ChatOpenAI` with `base_url=https://openrouter.ai/api/v1` | OpenAI-compatible; model ids like `anthropic/claude-*`; also enables non-Claude models for eval comparison |
| `claude-subscription` | Claude Pro/Max plan OAuth token (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`) | Thin adapter wrapping the **Claude Agent SDK** behind the `BaseChatModel` interface | Best-effort: structured output enforced by prompting + Pydantic validation, not the API; subject to plan usage limits; requires Claude Code runtime installed |

Design rules:

- Provider choice must be invisible above the factory: same graph,
  same prompts, same Pydantic validation on every path.
- Because the weakest provider (subscription) can't force schemas,
  **all** providers go through the same output-repair loop (validate →
  on failure, re-prompt with the validation error, bounded retries).
  This makes provider differences a measured quantity, not a bug class.
- The eval suite records the provider per run, so pass rates are
  comparable across providers (and across models via OpenRouter).

## 2. Agent Graph Design

LangGraph `StateGraph` with a typed state (Pydantic). Nodes:

| Node | Responsibility | Tools called |
|---|---|---|
| `parse_intent` | Classify request (create / create-recurring / update / delete / query) and extract entities into a draft `ScheduleProposal` | none (LLM structured output) |
| `gather_context` | Fetch what the proposal needs: availability windows, existing events, conflicts | `get_availability`, `list_events`, `check_conflicts`, `search_events` |
| `propose` | Produce final `ScheduleProposal`; RRULE built and **validated with `dateutil.rrule`** (parse + expand first N occurrences); conflicts re-checked | none (LLM + deterministic validation) |
| `human_approval` | **LangGraph interrupt.** Render proposal (human-readable expansion of the RRULE, conflicts flagged); user approves / edits / rejects. Graph checkpoints here. | none |
| `execute` | Perform the write | `add_event`, `add_recurring_event`, `update_event`, `delete_event` |
| `verify` | Read back the written event(s), compare to proposal | `list_events` |
| `respond` | Confirm to user with what was written and where | none |

Edges: query-type requests route `parse_intent → gather_context → respond`
(no approval needed for reads). Rejected proposals loop back to
`propose` with the user's feedback. MCP failures route to an error
handler that reports and offers retry — never a silent partial write.

**Why this shape:** the interrupt-before-write node is the core safety
property (FR3) and the checkpointing story: the graph state is
persisted (SQLite checkpointer) at the interrupt, so approval can
happen minutes later or after a process restart.

## 3. Data Contracts

All LLM output is constrained to Pydantic models; nothing free-form
crosses a node boundary.

- **`ScheduleProposal`** — `title`, `start` (aware datetime),
  `duration_minutes`, `timezone` (IANA), `rrule` (str | None),
  `participants` (list), `location`, `description`, `action`
  (create/update/delete), `target_event_id` (for update/delete).
- **`AvailabilityWindow`**, **`Conflict`** — typed wrappers over MCP
  tool responses.
- **`WriteResult`** — event id(s), verification status.

RRULE policy: agent emits a documented **subset** of RFC 5545 RRULE
matched to what `webcal_entry_repeats` can represent and correctly
expand. The subset is enforced by validation, not convention. The
exact boundary is fixed by the Phase 1 schema audit
([docs/SCHEMA_AUDIT.md](SCHEMA_AUDIT.md)):

- **FREQ** ∈ {DAILY, WEEKLY, MONTHLY, YEARLY} — no sub-daily.
- **Parts:** INTERVAL, COUNT, UNTIL (COUNT/UNTIL mutually exclusive),
  BYMONTH, BYMONTHDAY, BYDAY (with offsets), BYSETPOS, BYWEEKNO, WKST.
- **EXDATE/RDATE** via `webcal_entry_repeats_not`.
- **Excluded:** BYHOUR/BYMINUTE/BYSECOND (ignored by WebCalendar);
  BYYEARDAY (write/expand OK but not export-safe — avoid in v1);
  multiple RRULEs per event (schema allows only one).
- **Caveats** the validator/tool must honor: UNTIL stored local (DST
  care), `COUNT=999` is an infinite sentinel, `BY*` column width
  bounds. Full detail + evidence in the audit doc.

Timezones: every datetime is timezone-aware (`zoneinfo`). WebCalendar
stores GMT and converts to the user's TZ (as `mcp.php` already does for
reads); the agent always sends what the tool contract specifies and
never does implicit-local arithmetic.

## 4. MCP Layer (webcalendar changes)

New tools follow the existing patterns in `mcp.php`:
`#[McpTool]` attributes, dispatch through `mcp_dispatch_request()`
(the existing unit-test seam in `includes/functions.php`), writes gated
by `is_mcp_write_enabled()`, per-user token auth and rate limiting
unchanged.

| Tool | Type | Notes |
|---|---|---|
| `get_availability(start_date, end_date, users?)` | read | Free/busy derived from `webcal_entry` + `webcal_entry_user`; respects existing permission model for other users' calendars |
| `check_conflicts(date, time, duration, users?)` | read | Overlap test for a proposed slot, including expansion of existing recurring events |
| `add_recurring_event(name, date, time, duration, rrule, ...)` | write | Parses/validates RRULE subset server-side too (defense in depth); writes `webcal_entry` + `webcal_entry_repeats` |
| `update_event(event_id, ...)` | write | Ownership check: only events the token's user participates in |
| `delete_event(event_id)` | write | Same ownership check; soft-delete semantics if WebCalendar has them |

Server-side validation is intentionally duplicated with the agent-side
validation: the MCP server must not trust its client.

## 5. Evaluation Architecture

Two harness modes, one dataset format:

1. **CI mode (default):** agent runs against a **mock MCP server**
   (Python, same JSON-RPC surface) with fixture calendars. No DB, no
   PHP. Every golden case runs on every push.
2. **Live smoke mode (local):** small subset runs against the real
   `mcp.php` + DB with a dedicated test user/token.

Golden case = NL request + fixture calendar state + expected outcome
(structured): expected action, expected RRULE (or semantic properties:
"never expands to a Friday", "first occurrence is 2026-08-03 09:15
America/New_York"), expected conflict behavior.

Scoring is deterministic-first: RRULE parse + occurrence expansion
comparison via `dateutil`, DST assertions on known transition dates,
constraint predicates over expansions. LLM-as-judge only for NL
response quality, and only as a secondary metric. Report artifact
(JSON + markdown summary) per run, uploaded from CI.

## 6. Observability

- LangSmith tracing on every graph run: per-node latencies, tool
  call payloads, token usage.
- Structured logging (JSON) in the agent; correlation id per
  conversation thread.
- Chaos testing: point the agent's MCP base URL at k5n-mcp-hub's
  `/mcp` proxy with `X-MCP-Target-Server`, enable each fault (timeout,
  malformed JSON, SSE interrupt, invalid method) and assert the agent
  reports cleanly and never half-writes. Results documented in
  `docs/CHAOS.md` (Phase 4).

## 7. Deployment

- `Dockerfile` for the agent; `docker-compose.yml` wiring agent +
  WebCalendar (+ optionally k5n-mcp-hub) for a one-command demo.
- GitHub Actions: ruff, mypy, pytest, eval suite (CI mode), Docker
  build. Secrets (Anthropic key, MCP token) via repo secrets / env —
  never committed.

## 8. Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Orchestration | LangGraph |
| Model access | Provider abstraction (§2a): langchain-anthropic, langchain-openai→OpenRouter, Claude Agent SDK adapter |
| MCP client | langchain-mcp-adapters |
| Validation | Pydantic v2 |
| Recurrence | python-dateutil (rrule) |
| Timezones | zoneinfo (stdlib) |
| Persistence | SQLite (LangGraph checkpointer) |
| Tracing | LangSmith (OTel optional) |
| Testing | pytest; PHP side uses existing `mcp_dispatch_request()` seam |
| Lint/type | ruff, mypy |
| Packaging | uv, pyproject.toml |
| Delivery | Docker, docker-compose, GitHub Actions |
