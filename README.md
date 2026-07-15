# scheduling-agent

A Python/LangGraph agent that turns natural-language scheduling
requests ("set up a recurring standup with the team, avoiding
Fridays") into validated, RFC 5545-compliant calendar events written
to a live [WebCalendar](https://github.com/craigk5n/webcalendar)
instance via MCP — with human approval before every write, an eval
suite for RRULE/DST correctness, and full tracing.

**Status: the agent core works** (Phases 0–2 complete). The WebCalendar
MCP tools are merged-pending in
[craigk5n/webcalendar#668](https://github.com/craigk5n/webcalendar/pull/668).
Not yet done: verification against a live calendar instance, the eval
suite (Phase 3), and tracing/Docker (Phase 4). See the docs:

- [docs/PRD.md](docs/PRD.md) — goals, requirements, decisions, risks
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, agent
  graph, model provider abstraction, MCP tool surface, eval design
- [docs/SCHEMA_AUDIT.md](docs/SCHEMA_AUDIT.md) — WebCalendar recurrence ↔ RRULE
- [docs/TASKS.md](docs/TASKS.md) — phased task list

## The short version

```
NL request → LangGraph (parse_intent → gather_context → propose →
human_approval [interrupt] → execute → verify → respond) → WebCalendar via MCP
```

- **Orchestration:** LangGraph — stateful graph, SQLite checkpointing,
  a human-in-the-loop interrupt before every write, and a reject→replan
  loop.
- **Models:** pluggable via `MODEL_PROVIDER` — Anthropic API key,
  OpenRouter API key, or an Anthropic Pro/Max plan via the `claude` CLI.
  Structured output goes through one provider-agnostic
  validate-and-repair loop so even the subscription backend participates.
- **Correctness:** every recurrence is built and validated against the
  exact subset WebCalendar can store/expand (a Python twin of the PHP
  validator), with DST-correct expansion previews via `dateutil`.
- **Backend:** WebCalendar's MCP server (`mcp.php`), extended with
  availability, conflict-detection, and recurrence tools.
- **Related repos:** [webcalendar](https://github.com/craigk5n/webcalendar),
  [k5n-mcp-hub](https://github.com/craigk5n/k5n-mcp-hub),
  [php-icalendar-core](https://github.com/craigk5n/php-icalendar-core)

## Usage

```bash
uv sync
cp .env.example .env        # then fill in MODEL_PROVIDER + its key, MCP_URL, MCP_TOKEN
uv run scheduling-agent     # or: uv run python -m scheduling_agent
```

You describe what to schedule; the agent plans, shows you a proposal
(with the recurrence expanded and any conflicts flagged), and **waits
for your approval** before writing anything. Type `quit` to exit.

Everything runs offline in tests via an in-memory calendar and a fake
model, so no API key or live instance is needed to develop.

## Configuration

All credentials come from the environment (`.env`, gitignored); see
[.env.example](.env.example) for the variable **names** (no values).
Live-calendar URLs, tokens, and API keys are never committed.

| Variable | Purpose |
|---|---|
| `MODEL_PROVIDER` | `anthropic` \| `openrouter` \| `claude-subscription` |
| `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` | credential for the selected provider |
| `MODEL_NAME` | optional model override |
| `MCP_URL` / `MCP_TOKEN` | WebCalendar `mcp.php` endpoint + API token |

## Development

```bash
uv sync                    # create the venv (Python 3.12) and install deps
scripts/install-hooks.sh   # one-time: run the gate automatically on git push
```

### Run CI locally

`scripts/ci.sh` is the single source of truth — GitHub Actions runs the
exact same script, so local checks and CI can't drift.

```bash
scripts/ci.sh              # full gate: ruff, format, mypy, bandit, pytest+coverage
scripts/ci.sh test         # a single stage: lint | format | type | security | test
```

Once hooks are installed, the full gate also runs on every `git push`
and blocks it on failure (bypass in a pinch with `git push --no-verify`).

## License

[MIT](LICENSE) © 2026 Craig Knudsen.
