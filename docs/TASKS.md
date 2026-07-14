# Task List: WebCalendar Scheduling Agent

**Status:** Draft v1 (planning phase)
**Date:** 2026-07-14

v1 = Phases 0–3 complete. Phase 4 rounds out the portfolio story.
Phase 5 is explicitly post-v1.

## Phase 0 — Project scaffolding (`scheduling-agent`)

- [ ] Init git repo, `pyproject.toml` (uv), Python 3.12
- [ ] Tooling: ruff, mypy, pytest configured; pre-commit optional
- [ ] GitHub repo + Actions skeleton (lint, type, test)
- [ ] `README.md` stub with the one-liner and architecture sketch
- [ ] `.env.example` (MODEL_PROVIDER, ANTHROPIC_API_KEY,
      OPENROUTER_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, MCP_URL, MCP_TOKEN,
      LANGSMITH_*) — names only, never values
- [ ] `.gitignore` covering `.env`, checkpoints DB, and eval artifacts
      **in the first commit** (live-calendar credentials must never be
      committable)

## Phase 1 — MCP surface extension (PHP, `webcalendar` repo)

- [ ] **Schema audit first:** document what `webcal_entry_repeats` can
      represent; fix the supported RRULE subset (feeds ARCHITECTURE §3)
- [ ] Audit existing recurrence read/write code paths in WebCalendar to
      reuse, not reimplement
- [ ] `get_availability(start_date, end_date, users?)`
- [ ] `check_conflicts(date, time, duration, users?)` (must expand
      existing recurring events)
- [ ] `add_recurring_event(...)` with server-side RRULE validation
- [ ] `update_event(event_id, ...)` with ownership checks
- [ ] `delete_event(event_id)` with ownership checks
- [ ] PHP unit tests via `mcp_dispatch_request()` seam for all new tools
- [ ] Manual verification against the live instance with a dedicated
      test user + token
- [ ] TDD throughout: tests written before each tool implementation

## Phase 2 — Agent core (Python, LangGraph)

- [ ] Model provider abstraction (`get_chat_model()` factory):
      `anthropic` (ChatAnthropic), `openrouter` (ChatOpenAI +
      base_url), `claude-subscription` (Claude Agent SDK adapter
      behind BaseChatModel)
- [ ] Shared structured-output repair loop (validate → re-prompt with
      validation error → bounded retries) used by all providers
- [ ] Pydantic models: `ScheduleProposal`, `AvailabilityWindow`,
      `Conflict`, `WriteResult`
- [ ] RRULE builder + validator (subset enforcement, expansion preview)
- [ ] Mock MCP server (JSON-RPC surface matching `mcp.php`) — built
      early because Phase 3 evals and all unit tests depend on it
- [ ] MCP client wiring (`langchain-mcp-adapters`) against mock, then live
- [ ] Graph nodes: `parse_intent`, `gather_context`, `propose`,
      `human_approval` (interrupt), `execute`, `verify`, `respond`
- [ ] SQLite checkpointer; resume-after-restart demonstrated
- [ ] Error handling paths: MCP timeout / malformed response / rejected
      proposal loop
- [ ] CLI chat interface (REPL; render proposals with human-readable
      RRULE expansion and conflict flags)
- [ ] Integration smoke test against live WebCalendar (read + gated write)
- [ ] TDD throughout; code review + security review before merge
      (auth-adjacent code paths: token handling, write gating)

## Phase 3 — Eval suite (starts alongside Phase 2, per project decision)

- [ ] Golden dataset format (YAML/JSON): NL request + fixture calendar
      + expected outcome
- [ ] Seed ~40–60 cases: one-off, recurring, constraint-bearing
      ("avoid Fridays"), update, delete, query
- [ ] DST-boundary cases: spring-forward, fall-back, cross-timezone
      meetings, UNTIL across a transition
- [ ] Deterministic scorers: RRULE validity, occurrence-expansion
      equality, constraint predicates, conflict avoidance
- [ ] Harness: CI mode (mock MCP) + live smoke mode (real instance)
- [ ] Per-provider eval runs (anthropic / openrouter / subscription)
      with provider recorded in the report, so pass rates are
      comparable across model backends
- [ ] Score report artifact (JSON + markdown), wired into CI
- [ ] Baseline run recorded; regressions fail CI

## Phase 4 — Observability, deployment, story

- [ ] LangSmith tracing enabled end-to-end; sample traces linked in README
- [ ] Structured JSON logging with per-conversation correlation ids
- [ ] Dockerfile + docker-compose (agent + WebCalendar [+ k5n-mcp-hub])
- [ ] Chaos testing via k5n-mcp-hub fault injection; findings in
      `docs/CHAOS.md`
- [ ] Thin web UI (FastAPI + minimal chat page) reusing the same graph
- [ ] README finalized: architecture diagram, demo transcript/GIF,
      eval results table, design decisions
- [ ] Blog post draft for k5n.us (optional, follows the ilibgo pattern)

## Phase 5 — A2A negotiation (post-v1)

- [ ] Two agent instances with separate calendars/tokens negotiate a
      meeting time over A2A protocol using real iCalendar data
- [ ] Scope properly when v1 ships — not planned in detail yet

## Working agreements

- Planning docs live in `docs/` and are updated when decisions change
- TDD (tests first) for both Python and PHP work; 80%+ coverage target
- Every write path is human-approved and `is_mcp_write_enabled()`-gated
- No secrets in code; `.env` + repo secrets only
- Conventional commits (`feat:`, `fix:`, ...)
