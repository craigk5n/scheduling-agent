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

- [x] Model provider abstraction (`get_chat_model()` factory):
      `anthropic` (ChatAnthropic), `openrouter` (ChatOpenAI +
      base_url), `claude-subscription` (adapter behind BaseChatModel)
- [x] Shared structured-output repair loop (validate → re-prompt with
      validation error → bounded retries) used by all providers
- [x] Pydantic models: `ScheduleProposal`, `RecurrenceSpec`,
      `AvailabilityResult`, `Conflict`/`ConflictResult`, `WriteResult`
- [x] RRULE builder + validator (subset enforcement, DST-correct
      expansion preview via dateutil)
- [x] Mock backend: `FakeCalendarTools` (in-memory, mirrors MCP
      semantics) behind a `CalendarTools` protocol
- [x] MCP client wiring: `HttpMcpCalendarTools` (httpx JSON-RPC to
      `mcp.php`) — direct client, since mcp.php's HTTP transport is
      custom JSON-RPC, not standard-MCP (so `langchain-mcp-adapters`
      isn't the right fit). Tested via httpx MockTransport.
- [x] Graph nodes: `parse_intent`, `gather_context`, `propose`,
      `human_approval` (interrupt), `execute`, `verify`, `respond`
- [x] SQLite checkpointer; resume-after-restart demonstrated (a second
      graph over the same SQLite file resumes the interrupted thread)
- [x] Error handling paths: JSON-RPC error → McpError; tool-level error
      → failed WriteResult; invalid recurrence; rejected-proposal replan
- [x] CLI chat interface (REPL with injectable I/O; proposals rendered
      with RRULE expansion + conflict flags); `scheduling-agent` entry point
- [ ] Integration smoke test against **live** WebCalendar (read + gated
      write) — deferred with the rest of live verification
- [x] TDD throughout (RED→GREEN per module); full gate green,
      **115 tests, 100% coverage** (ruff, mypy --strict, bandit, pytest)

**Deviations from the original plan (documented):**
- Direct httpx JSON-RPC client instead of `langchain-mcp-adapters`
  (mcp.php's HTTP path is custom JSON-RPC, not MCP-compliant).
- `propose` is deterministic (build+validate RRULE) rather than a
  second LLM step — keeps the write path predictable and testable.
- **Known v1 limitations:** one-off `create` events are stored untimed
  (the current `add_event` MCP tool takes no time); availability/
  conflicts don't expand recurring occurrences beyond the base date.
  Both are documented and slated for the eval phase / a backend follow-up.

## Phase 3 — Eval suite (starts alongside Phase 2, per project decision)

- [x] Golden dataset format (YAML): NL request + fixtures + reference
      proposal + expected outcome (`src/scheduling_agent/evals/cases.yaml`)
- [x] Seed cases (20 to start, extensible): one-off (with/without
      conflict), recurring, constraint-bearing ("avoid Fridays",
      weekday-only), BYSETPOS, interval, COUNT, UNTIL, update, delete,
      query
- [x] DST-boundary cases: spring-forward + fall-back (local-hour
      preserved across the transition), UNTIL as end-of-day
- [x] Deterministic scorers: RRULE validity + canonical match,
      occurrence-count, forbid/require-weekday predicates, DST local
      hour, conflict expectation — pure, unit-tested (pass good AND
      fail bad output)
- [x] Harness: reference mode (no LLM, CI) + agent mode (real provider,
      opt-in) via a `Proposer` abstraction over the same scorers
- [x] Per-provider report label (`EvalReport.provider`); agent mode
      records the selected provider
- [x] Score report artifact (JSON + markdown); CI step generates it and
      uploads it as `eval-report`
- [x] Regressions fail CI: `test_reference_proposals_all_satisfy_expected`
      + reference-mode CLI exit code both gate on 100% of the dataset
- [ ] Live smoke mode (real WebCalendar) — deferred with live verification
- [ ] Measured baseline against a real provider — needs an API key
      (run `python -m scheduling_agent.evals --mode agent`)

**Phase 3 done (deterministic layer).** Full gate: **137 tests, 100%
coverage.** The only open items need an API key / live instance, which
are deferred by request. What's shippable and CI-gated today: the RRULE/
DST/constraint/conflict scoring machinery over a 20-case golden set.

## Phase 4 — Observability, deployment, story

- [x] LangSmith tracing (auto-enabled by LangChain when LANGSMITH_* set;
      `tracing_enabled()` reports it, startup line shows state)
- [x] Structured JSON logging with per-conversation correlation ids
      (`observability.py`; wired into the CLI per turn)
- [x] Dockerfile + docker-compose (agent + WebCalendar, optional
      k5n-mcp-hub via `--profile chaos`); image build verified
- [x] Chaos testing: k5n-mcp-hub fault modes simulated via MockTransport,
      client wraps all faults into `McpError`, REPL degrades gracefully;
      findings in `docs/CHAOS.md`
- [x] Thin web UI (FastAPI `/schedule` + `/approve` + chat page) reusing
      the same graph; `scheduling-agent-web` entry point
- [x] README finalized: mermaid architecture diagram, eval results,
      design decisions, known limitations
- [ ] Demo transcript/GIF + blog post for k5n.us (optional; needs a live
      run against a real provider/instance)

**Phase 4 done.** Full gate: **156 tests, 100% coverage.** Only the
optional demo/blog and the live-instance verification remain.

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
