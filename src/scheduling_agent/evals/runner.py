"""Run the eval dataset through a proposer, score it, and emit a report.

Two proposers:
- ``reference_proposer`` returns each case's ground-truth proposal — used in
  CI to validate the scoring machinery with no LLM.
- ``make_agent_proposer`` runs the real agent graph (any provider) and reads
  the proposal it planned — used for opt-in measurement of model quality.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from scheduling_agent.calendar import FakeCalendarTools
from scheduling_agent.evals.dataset import load_cases
from scheduling_agent.evals.models import (
    CaseResult,
    CheckResult,
    EvalCase,
    EvalReport,
    FixtureEvent,
)
from scheduling_agent.evals.scorers import score_case
from scheduling_agent.graph import build_agent
from scheduling_agent.models import ScheduleProposal

Proposer = Callable[[EvalCase], ScheduleProposal | None]


def reference_proposer(case: EvalCase) -> ScheduleProposal:
    """Return the case's ground-truth proposal."""
    return case.reference


def _seed(tools: FakeCalendarTools, fixtures: list[FixtureEvent]) -> None:
    for fixture in fixtures:
        if fixture.time == "-1":
            tools.add_event(fixture.name, fixture.date, duration=fixture.duration)
        else:
            tools.add_recurring_event(
                fixture.name,
                fixture.date,
                fixture.rrule or "FREQ=DAILY",
                time=fixture.time,
                duration=fixture.duration,
            )


def make_agent_proposer(model: BaseChatModel) -> Proposer:
    """A proposer that runs the real agent graph and returns its planned
    proposal (read from checkpointed state, before the approval interrupt)."""

    def proposer(case: EvalCase) -> ScheduleProposal | None:
        tools = FakeCalendarTools()
        _seed(tools, case.fixtures)
        agent = build_agent(model, tools, MemorySaver())
        config = {"configurable": {"thread_id": case.id}}
        agent.invoke({"request": case.request}, config)
        proposal = agent.get_state(config).values.get("proposal")
        return proposal if isinstance(proposal, ScheduleProposal) else None

    return proposer


def run_evals(
    cases: list[EvalCase], proposer: Proposer, *, provider: str = "reference"
) -> EvalReport:
    """Score every case through ``proposer`` and aggregate a report."""
    results: list[CaseResult] = []
    for case in cases:
        try:
            proposal = proposer(case)
        except Exception as exc:  # noqa: BLE001 - record any proposer failure
            proposal = None
            detail = str(exc)
        else:
            detail = "no proposal produced"
        if proposal is None:
            results.append(
                CaseResult(
                    id=case.id,
                    passed=False,
                    checks=[CheckResult(name="proposer", passed=False, detail=detail)],
                )
            )
        else:
            results.append(score_case(proposal, case))

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return EvalReport(
        provider=provider,
        total=total,
        passed=passed,
        pass_rate=(passed / total if total else 0.0),
        cases=results,
    )


def render_markdown(report: EvalReport) -> str:
    """Render a report as a markdown summary table."""
    lines = [
        f"# Eval report — provider: `{report.provider}`",
        "",
        f"**{report.passed}/{report.total} passed ({report.pass_rate:.0%})**",
        "",
        "| case | result | failing checks |",
        "|------|--------|----------------|",
    ]
    for result in report.cases:
        mark = "✅" if result.passed else "❌"
        failing = ", ".join(c.name for c in result.checks if not c.passed) or "—"
        lines.append(f"| {result.id} | {mark} | {failing} |")
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, out_dir: Path | str) -> Path:
    """Write report.json and report.md into ``out_dir``; return the directory."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (directory / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return directory


def run_cli(argv: list[str] | None = None) -> int:
    """CLI: score the dataset and write a report. Returns non-zero if any
    case fails (so reference mode gates CI)."""
    parser = argparse.ArgumentParser(prog="scheduling-agent-evals")
    parser.add_argument("--mode", choices=["reference", "agent"], default="reference")
    parser.add_argument("--out", default="eval-reports")
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args(argv)

    cases = load_cases(args.dataset) if args.dataset else load_cases()

    if args.mode == "reference":
        report = run_evals(cases, reference_proposer, provider="reference")
    else:  # pragma: no cover - needs a provider API key + network
        from dotenv import load_dotenv

        from scheduling_agent.providers import get_chat_model
        from scheduling_agent.settings import Settings

        load_dotenv()
        settings = Settings.from_env()
        report = run_evals(
            cases,
            make_agent_proposer(get_chat_model(settings)),
            provider=settings.model_provider.value,
        )

    write_report(report, args.out)
    print(render_markdown(report))
    return 0 if report.pass_rate == 1.0 else 1
