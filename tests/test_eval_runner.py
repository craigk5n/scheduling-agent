"""Tests for the eval runner, report rendering, agent proposer, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from scheduling_agent.evals.dataset import load_cases
from scheduling_agent.evals.models import (
    EvalCase,
    ExpectedOutcome,
    FixtureEvent,
)
from scheduling_agent.evals.runner import (
    make_agent_proposer,
    reference_proposer,
    render_markdown,
    run_cli,
    run_evals,
    write_report,
)
from scheduling_agent.models import ScheduleAction, ScheduleProposal


def test_run_evals_reference_all_pass() -> None:
    report = run_evals(load_cases(), reference_proposer)
    assert report.total >= 20
    assert report.passed == report.total
    assert report.pass_rate == 1.0


def test_run_evals_records_proposer_failure() -> None:
    cases = load_cases()[:1]
    report = run_evals(cases, lambda _case: None, provider="none")
    assert report.passed == 0
    assert report.cases[0].checks[0].name == "proposer"


def test_run_evals_handles_proposer_exception() -> None:
    def boom(_case: EvalCase) -> ScheduleProposal:
        raise RuntimeError("kaboom")

    report = run_evals(load_cases()[:1], boom)
    assert report.pass_rate == 0.0
    assert "kaboom" in report.cases[0].checks[0].detail


def test_render_markdown_has_summary_and_rows() -> None:
    report = run_evals(load_cases(), reference_proposer)
    md = render_markdown(report)
    assert "Eval report" in md
    assert "passed (100%)" in md
    assert "standup-mwf" in md


def test_make_agent_proposer_reads_planned_proposal() -> None:
    proposal_json = {
        "action": "create_recurring",
        "title": "Team Standup",
        "timezone": "America/New_York",
        "start": "2026-08-03T09:15:00-04:00",
        "duration_minutes": 15,
        "recurrence": {"freq": "WEEKLY", "by_day": ["MO", "WE", "FR"]},
    }
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(proposal_json))])
    )
    case = EvalCase(
        id="agent-case",
        request="standup mwf",
        timezone="America/New_York",
        today="2026-07-15",
        reference=load_cases()[2].reference,
        expected=ExpectedOutcome(action=ScheduleAction.CREATE_RECURRING),
    )
    proposal = make_agent_proposer(model)(case)
    assert proposal is not None
    assert proposal.action is ScheduleAction.CREATE_RECURRING
    assert proposal.title == "Team Standup"


def test_make_agent_proposer_seeds_fixtures() -> None:
    proposal_json = {
        "action": "create",
        "title": "Lunch",
        "timezone": "America/New_York",
        "start": "2026-08-04T12:00:00-04:00",
        "duration_minutes": 60,
    }
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content=json.dumps(proposal_json))])
    )
    case = EvalCase(
        id="seeded",
        request="lunch tuesday",
        timezone="America/New_York",
        today="2026-07-15",
        fixtures=[
            FixtureEvent(name="Busy", date="20260804", time="160000", duration=60),
            FixtureEvent(name="Holiday", date="20260804"),  # untimed
        ],
        reference=load_cases()[0].reference,
        expected=ExpectedOutcome(action=ScheduleAction.CREATE),
    )
    proposal = make_agent_proposer(model)(case)
    assert proposal is not None and proposal.action is ScheduleAction.CREATE


def test_write_report_writes_json_and_markdown(tmp_path: Path) -> None:
    report = run_evals(load_cases()[:2], reference_proposer)
    out = write_report(report, tmp_path / "reports")
    data = json.loads((out / "report.json").read_text())
    assert data["provider"] == "reference"
    assert (out / "report.md").read_text().startswith("# Eval report")


def test_run_cli_reference_mode_writes_report_and_succeeds(tmp_path: Path) -> None:
    code = run_cli(["--mode", "reference", "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "report.json").exists()
