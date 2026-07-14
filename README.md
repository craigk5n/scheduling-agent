# scheduling-agent

A Python/LangGraph agent that turns natural-language scheduling
requests ("set up a recurring standup with the team, avoiding
Fridays") into validated, RFC 5545-compliant calendar events written
to a live [WebCalendar](https://github.com/craigk5n/webcalendar)
instance via MCP — with human approval before every write, an eval
suite for RRULE/DST correctness, and full tracing.

**Status: planning.** No code yet — see the planning docs:

- [docs/PRD.md](docs/PRD.md) — goals, requirements, decisions, risks
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, agent
  graph, model provider abstraction, MCP tool surface, eval design
- [docs/TASKS.md](docs/TASKS.md) — phased task list

## The short version

```
NL request → LangGraph (parse → gather context → propose →
human approval [interrupt] → execute → verify) → WebCalendar via MCP
```

- **Orchestration:** LangGraph (stateful graph, SQLite checkpointing,
  human-in-the-loop interrupt before any write)
- **Models:** pluggable — Anthropic API key, OpenRouter API key, or an
  Anthropic Pro/Max plan via the Claude Agent SDK
- **Backend:** WebCalendar's MCP server (`mcp.php`), extended with
  availability, conflict-detection, and recurrence tools
- **Evals from day 1:** golden dataset scoring RRULE validity, DST
  boundary handling, and constraint satisfaction — run in CI against a
  mock MCP server
- **Related repos:** [webcalendar](https://github.com/craigk5n/webcalendar),
  [k5n-mcp-hub](https://github.com/craigk5n/k5n-mcp-hub) (chaos-testing
  the agent via MCP fault injection),
  [php-icalendar-core](https://github.com/craigk5n/php-icalendar-core)

## Configuration

All credentials come from the environment (`.env`, gitignored). A
`.env.example` documenting variable **names only** will land in
Phase 0. Live-calendar URLs, tokens, and API keys are never committed.

## License

MIT (to be added with the first code commit).
