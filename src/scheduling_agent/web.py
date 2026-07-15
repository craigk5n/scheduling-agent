"""A thin FastAPI chat UI over the same LangGraph agent.

Two endpoints mirror the CLI's human-in-the-loop flow:
- ``POST /schedule`` runs a request to the approval interrupt (or to
  completion for queries) and returns the proposal summary + a thread id.
- ``POST /approve`` resumes that thread with the user's decision.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from langgraph.types import Command
from pydantic import BaseModel

from scheduling_agent.observability import new_correlation_id


class ScheduleRequest(BaseModel):
    request: str


class ApproveRequest(BaseModel):
    thread_id: str
    decision: str = "approve"
    feedback: str | None = None


class AgentResponse(BaseModel):
    thread_id: str
    status: str  # "needs_approval" | "done"
    summary: str | None = None
    response: str | None = None


def _to_response(state: dict[str, Any], thread_id: str) -> AgentResponse:
    if "__interrupt__" in state:
        return AgentResponse(
            thread_id=thread_id,
            status="needs_approval",
            summary=state["__interrupt__"][0].value["summary"],
        )
    return AgentResponse(
        thread_id=thread_id, status="done", response=state.get("response", "")
    )


def create_app(agent: Any) -> FastAPI:
    """Build the FastAPI app around a compiled agent graph."""
    app = FastAPI(title="Scheduling Agent")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.post("/schedule", response_model=AgentResponse)
    def schedule(body: ScheduleRequest) -> AgentResponse:
        thread_id = new_correlation_id("web")
        config = {"configurable": {"thread_id": thread_id}}
        state = agent.invoke({"request": body.request}, config)
        return _to_response(state, thread_id)

    @app.post("/approve", response_model=AgentResponse)
    def approve(body: ApproveRequest) -> AgentResponse:
        config = {"configurable": {"thread_id": body.thread_id}}
        resume: dict[str, Any] = {"decision": body.decision}
        if body.feedback:
            resume["feedback"] = body.feedback
        state = agent.invoke(Command(resume=resume), config)
        return _to_response(state, body.thread_id)

    return app


def main() -> None:  # pragma: no cover - reads env, opens sqlite, serves HTTP
    import os
    import sqlite3

    import uvicorn
    from dotenv import load_dotenv
    from langgraph.checkpoint.sqlite import SqliteSaver

    from scheduling_agent.checkpoint import default_serde
    from scheduling_agent.cli import build_cli_agent
    from scheduling_agent.observability import configure_logging
    from scheduling_agent.settings import Settings

    load_dotenv(override=True)
    configure_logging()
    settings = Settings.from_env()
    mcp_url = os.environ.get("MCP_URL", "").strip()
    mcp_token = os.environ.get("MCP_TOKEN", "").strip()
    if not mcp_url or not mcp_token:
        raise SystemExit("MCP_URL and MCP_TOKEN must be set (see .env.example)")
    conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
    agent = build_cli_agent(
        settings,
        mcp_url=mcp_url,
        mcp_token=mcp_token,
        checkpointer=SqliteSaver(conn, serde=default_serde()),
    )
    host = os.environ.get("HOST", "0.0.0.0")  # nosec B104 - container binding
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(agent), host=host, port=port)


_INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Scheduling Agent</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 44rem; margin: 2rem auto; }
  #log { white-space: pre-wrap; border: 1px solid #ccc; padding: 1rem; }
  input, button { font: inherit; padding: .4rem .6rem; }
  #row { display: flex; gap: .5rem; margin-top: .75rem; }
  #msg { flex: 1; }
</style></head>
<body>
<h1>Scheduling Agent</h1>
<div id="log"></div>
<div id="row">
  <input id="msg" placeholder="weekly standup Mon/Wed/Fri at 9:15">
  <button onclick="send()">Send</button>
</div>
<script>
let thread = null;
const log = (t) => {
  document.getElementById('log').textContent += t + "\\n\\n";
};
async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  return r.json();
}
async function send() {
  const box = document.getElementById('msg');
  const text = box.value.trim(); if (!text) return; box.value = '';
  log("you: " + text);
  let res;
  if (thread) {
    res = await post('/approve', {thread_id: thread, decision: text});
    thread = null;
  } else {
    res = await post('/schedule', {request: text});
  }
  if (res.status === 'needs_approval') {
    thread = res.thread_id;
    log(res.summary + "\\n\\n(type 'approve', or feedback to revise)");
  } else {
    log("agent: " + res.response);
  }
}
document.getElementById('msg').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') send();
});
</script>
</body></html>
"""
