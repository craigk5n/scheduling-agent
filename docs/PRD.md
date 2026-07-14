# PRD: WebCalendar Scheduling Agent

**Status:** Draft v1 (planning phase)
**Date:** 2026-07-14
**Owner:** Craig Knudsen (craigk5n)

## 1. Summary

A Python/LangGraph agent that turns natural-language scheduling requests
("set up a recurring standup with the team, avoiding Fridays") into
validated, RFC 5545-compliant calendar events written to a live
WebCalendar instance via MCP (Model Context Protocol) — with human
approval before every write, an eval suite for RRULE/DST correctness,
and full tracing.

## 2. Goals

This project has two co-equal goals:

1. **A genuinely useful personal tool** — schedule real events on a
   running WebCalendar instance from natural language.
2. **A portfolio-grade AI engineering artifact** — demonstrate the
   skills an AI engineer role probes on: stateful agent graphs,
   checkpointing/persistence, human-in-the-loop approval, multi-step
   tool use over MCP, structured output validation, evaluation
   harnesses, tracing/observability, Docker + CI.

It ties together three existing repos into one coherent story:

| Repo | Role |
|---|---|
| `craigk5n/scheduling-agent` (new) | Python agent, evals, docs, Docker, CI |
| `craigk5n/webcalendar` | Backend; MCP server (`mcp.php`) gains new scheduling tools |
| `craigk5n/k5n-mcp-hub` | MCP proxy; fault injection used to chaos-test the agent |

## 3. Non-Goals (v1)

- **A2A negotiation** (two agents negotiating a meeting time) — Phase 5,
  after v1 ships.
- **Web UI** — CLI chat is the v1 interface; a thin web UI arrives in
  Phase 4.
- **Google Calendar or other backends** — WebCalendar only. The .ics
  output path keeps the door open.
- **Multi-tenant / production hardening of WebCalendar itself** — the
  agent targets a personally operated instance.

## 4. Users & Primary Use Cases

Single primary user (the operator) talking to the agent via CLI:

1. **Create a one-off event:** "Lunch with Dana next Tuesday at noon."
2. **Create a recurring event:** "Team standup every weekday at 9:15
   except Fridays, starting next month."
3. **Schedule around conflicts:** "Find 90 minutes for a review with
   Bob and me this week, afternoons only."
4. **Modify/cancel:** "Move Thursday's dentist appointment to 4pm." /
   "Cancel the Friday standup series."
5. **Query:** "What does my week look like?" (read-only, no approval
   needed)

## 5. Functional Requirements

### Agent behavior
- FR1: Parse NL requests into a structured `ScheduleProposal`
  (Pydantic): title, start, duration, timezone, RRULE (optional),
  participants, location, description.
- FR2: Gather context before proposing — availability and conflict
  checks via MCP tools.
- FR3: **Human-in-the-loop:** every write (create/update/delete) is
  presented as a proposal the user must approve, edit, or reject.
  Implemented as a LangGraph interrupt; the graph checkpoints and
  resumes.
- FR4: All recurrence expressed as RFC 5545 RRULEs, validated with
  `dateutil.rrule` **before** the write is attempted.
- FR5: All datetimes carry explicit IANA timezones; DST transitions
  must be handled correctly (see eval suite).
- FR6: After a write, verify by reading back and confirm to the user.
- FR7: Graceful degradation on MCP failures (timeouts, malformed
  responses, dropped streams) — report clearly, never write partial
  state silently.
- FR8: **Model provider abstraction** — the agent runs unchanged on
  any of three model backends, selected by `MODEL_PROVIDER` config:
  (a) Anthropic API key (`ChatAnthropic`), (b) OpenRouter API key
  (OpenAI-compatible endpoint; enables non-Claude models too),
  (c) Anthropic paid plan (Pro/Max) via the Claude Agent SDK with a
  subscription OAuth token. Path (c) is documented as best-effort for
  structured output (prompt + Pydantic validation rather than
  API-enforced schemas) and subject to plan usage limits.

### MCP tools (new, in webcalendar/mcp.php)
- FR9: `get_availability(start_date, end_date, users?)` — free/busy
  blocks, multi-user when participants are given.
- FR10: `check_conflicts(date, time, duration, users?)` — overlap
  detection for a proposed slot.
- FR11: `add_recurring_event(...)` — accepts an RRULE; writes
  `webcal_entry` + `webcal_entry_repeats`.
- FR12: `update_event(event_id, ...)` and `delete_event(event_id)` —
  with ownership checks; write-gated by `is_mcp_write_enabled()`.

### Evaluation (built alongside the agent, not after)
- FR13: Golden dataset (~40–60 cases to start): NL request → expected
  schedule semantics.
- FR14: Deterministic checks: RRULE parses and expands to expected
  occurrences; DST boundary cases (spring-forward, fall-back,
  cross-timezone meetings); constraint satisfaction (e.g. "avoid
  Fridays"); conflict avoidance.
- FR15: Evals run in CI against a **mock MCP server** (no DB); a smoke
  subset runs locally against the real instance.
- FR16: Each eval run produces a score report artifact.

### Observability & delivery
- FR17: LangSmith tracing on every agent run (OTel export optional).
- FR18: Dockerfile + compose (agent alongside WebCalendar); GitHub
  Actions CI: lint (ruff), types (mypy), tests (pytest), evals.
- FR19: Chaos-test writeup: route agent MCP traffic through
  k5n-mcp-hub and exercise its fault injection (timeout, malformed
  JSON, SSE interrupt) against the agent's error handling.

## 6. Success Criteria

- The operator uses it for real scheduling on the live WebCalendar
  instance.
- Eval suite: 100% RRULE validity on golden set; explicit pass rates
  reported for DST and constraint cases; CI green.
- A reader of the README can follow the architecture, run the demo via
  Docker, and see traces of a full plan→approve→write→verify loop.
- No write ever occurs without explicit human approval.

## 7. Key Decisions (locked 2026-07-14)

| Decision | Choice | Alternatives considered |
|---|---|---|
| Purpose | Useful tool + portfolio piece, equally | either alone |
| Backend | WebCalendar via MCP; extend `mcp.php` in v1 | mock-first; .ics-only; Google Calendar |
| Framework | LangGraph + Claude + MCP (`langchain-mcp-adapters`) | Claude Agent SDK (simpler, weaker graph/HITL signal) |
| Model access | Provider abstraction: Anthropic API key, OpenRouter API key, or Anthropic Pro/Max plan via Claude Agent SDK | single hardcoded provider |
| v1 scope | Single agent + eval suite from day 1 | A2A in v1 (deferred to Phase 5) |
| Interface | CLI chat in v1; thin web UI in Phase 4 | web-first |
| Test env | Existing running WebCalendar instance with DB access | Docker-only |

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| WebCalendar recurrence schema (`webcal_entry_repeats`) may not map 1:1 to full RRULE grammar | Medium | Audit schema early (Phase 1, first task); constrain agent to the supported RRULE subset and validate against it |
| LLM produces plausible-but-wrong RRULEs | High | Deterministic validation + expansion checks before write; eval suite targets exactly this |
| Writes against a live personal calendar | Medium | HITL approval on every write; separate test user/token; `is_mcp_write_enabled()` gate |
| MCP HTTP path in `mcp.php` is custom JSON-RPC (not the SDK transport) | Low | Integration tests against the real endpoint early in Phase 2 |
| Subscription (Pro/Max) provider path lacks API-enforced structured output and has plan usage limits | Medium | Treat as best-effort provider; Pydantic validation catches schema drift; eval suite runs per-provider so degradation is measured, not guessed |
| Live-calendar credentials leak into the repo | High | `.env` only, `.gitignore` from first commit, `.env.example` documents names not values; secret scan before any push |
| Scope creep (web UI, A2A) | Medium | Phases are gated; v1 = Phases 1–3 complete |
