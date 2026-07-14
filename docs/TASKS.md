# Task List: WebCalendar Scheduling Agent

**Status:** Draft v1 (planning phase)
**Date:** 2026-07-14

v1 = Phases 0–3 complete. Phase 4 rounds out the portfolio story.
Phase 5 is explicitly post-v1.

## Phase 0 — Project scaffolding (`scheduling-agent`)

- [x] Init git repo, `pyproject.toml` (uv), Python 3.12 (pinned via
      `.python-version`; venv confirmed 3.12.13)
- [x] Tooling: ruff, mypy (strict), pytest + pytest-cov, bandit configured
- [x] GitHub repo + Actions CI (ruff, ruff-format, mypy, bandit, pytest
      with 80% coverage gate)
- [x] `README.md` with one-liner, architecture sketch, dev section
- [x] `.env.example` (MODEL_PROVIDER, ANTHROPIC_API_KEY,
      OPENROUTER_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, MCP_URL, MCP_TOKEN,
      LANGSMITH_*) — names only, never values
- [x] `.gitignore` covering `.env`, checkpoints DB, and eval artifacts
      **in the first commit** (live-calendar credentials must never be
      committable)
- [x] First TDD unit: settings loader (`scheduling_agent.settings`) —
      provider selection + required-credential validation, SecretStr
      wrapping, 12 tests, 100% coverage

## Phase 1 — MCP surface extension (PHP, `webcalendar` repo)

- [x] **Schema audit first:** document what `webcal_entry_repeats` can
      represent; fix the supported RRULE subset (feeds ARCHITECTURE §3)
      → see [docs/SCHEMA_AUDIT.md](SCHEMA_AUDIT.md)
- [x] Audit existing recurrence read/write code paths in WebCalendar to
      reuse, not reimplement (xcal.php builder/parser/insert, RptEvent)
- [x] `get_availability(start_date, end_date)` — busy blocks, GMT frame,
      self/authenticated user (multi-user deferred to A2A phase)
- [x] `check_conflicts(date, time, duration)` — overlap detection, GMT
- [x] `add_recurring_event(...)` with server-side RRULE validation
      (`mcp_validate_rrule` + `mcp_rrule_to_repeat_columns`), write-gated
- [x] `update_event(event_id, ...)` with ownership checks, write-gated
- [x] `delete_event(event_id)` with ownership checks, write-gated
- [x] PHP unit tests via `mcp_dispatch_request()` seam (stub tools object)
      + HTTP integration tests (installer schema) for the write tools
- [x] Write-gate regression test (MCP_WRITE_ACCESS off → writes refused)
- [ ] Manual verification against the **live** instance with a dedicated
      test user + token (only tested against installer-built SQLite so far)
- [x] TDD throughout: tests written before each tool/helper (RED→GREEN)
- ~~must expand existing recurring events in availability/conflicts~~ —
      **v1 limitation:** recurring occurrences beyond the base date are
      not yet reflected in get_availability/check_conflicts; certify via
      the Phase 3 eval suite, then enhance.

**Branch:** `feature/mcp-scheduling-tools` in the `webcalendar` repo
(6 commits). Full MCP suite green: 152 tests across 12 files. PR to be
opened by the maintainer.

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
