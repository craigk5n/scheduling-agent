# Chaos testing: MCP fault tolerance

The agent reaches WebCalendar over a network hop, so it must survive a
misbehaving MCP layer. [`k5n-mcp-hub`](https://github.com/craigk5n/k5n-mcp-hub)
can deliberately inject faults on its reverse-proxy path; this document maps
each fault to how the agent handles it, and how that handling is tested.

## How to reproduce against a live hub

1. Register the WebCalendar MCP server in k5n-mcp-hub.
2. Point the agent at the hub instead of `mcp.php` directly:
   `MCP_URL=https://<hub>/mcp` (the hub routes via the
   `X-MCP-Target-Server` header).
3. Enable a fault on the hub's **Faults** panel and send a request.

## Fault modes and handling

| k5n-mcp-hub fault | What the client receives | Agent behavior |
|---|---|---|
| **Timeout** | The hub stalls, then returns `504` | `HttpMcpCalendarTools` wraps it as `McpError("MCP HTTP 504 …")` |
| **SSE Interrupt** | `200 text/event-stream` that drops mid-stream (not JSON) | JSON parse fails → `McpError("MCP returned malformed JSON …")` |
| **Malformed JSON** | `200` with a corrupt body (`{bad json`) | `McpError("MCP returned malformed JSON …")` |
| **Invalid Method Error** | `200` JSON-RPC error `-32601` | `McpError("Method not found")` (the server message) |
| Transport drop / connect failure | httpx `RequestError` | `McpError("MCP request failed …")` |
| Non-object / missing `result` | `200` with a list or no result | `McpError("MCP returned a non-object …" / "… missing a result object")` |

The design rule: **every transport- or protocol-level fault surfaces as a
single `McpError` type**, never a raw `httpx` exception or a crash. Callers
(the graph, the CLI) then handle one well-defined failure.

## Graceful degradation

- **Client** (`HttpMcpCalendarTools._call`): wraps httpx errors, HTTP status
  errors, malformed/`non-JSON bodies, and malformed JSON-RPC envelopes into
  `McpError`.
- **REPL** (`run_repl`): a fault during a turn is logged (`event=error`,
  with the conversation's correlation id) and reported to the user; the loop
  continues to the next turn rather than crashing.
- **Writes**: `add_recurring_event` rolls back its base event if the
  recurrence write fails (server side), and a tool-level `{error}` result
  becomes a failed `WriteResult` — so a fault never leaves a half-written
  event.

## Tests

`tests/test_chaos.py` simulates each fault with `httpx.MockTransport`
(timeout, HTTP 504, malformed JSON, dropped event-stream, JSON-RPC error,
non-object/missing result) and asserts a clean `McpError`, plus a REPL test
that injects a tool fault mid-conversation and asserts the loop survives and
reports it. These run in CI — no live hub required.
