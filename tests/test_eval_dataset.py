"""Dataset loading + reference-consistency (catches regressions in CI)."""

from __future__ import annotations

from scheduling_agent.evals.dataset import load_cases
from scheduling_agent.evals.scorers import score_case
from scheduling_agent.models import ScheduleAction


def test_dataset_loads_with_unique_ids() -> None:
    cases = load_cases()
    assert len(cases) >= 20
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"


def test_reference_proposals_all_satisfy_expected() -> None:
    # Every ground-truth reference proposal must pass its own scorer checks.
    # This keeps the dataset internally consistent and, in CI, fails on any
    # regression in the RRULE/DST/conflict scoring machinery.
    failures = []
    for case in load_cases():
        result = score_case(case.reference, case)
        if not result.passed:
            failing = [f"{c.name}({c.detail})" for c in result.checks if not c.passed]
            failures.append(f"{case.id}: {', '.join(failing)}")
    assert not failures, "reference cases failed:\n" + "\n".join(failures)


def test_dataset_covers_all_actions() -> None:
    actions = {c.expected.action for c in load_cases()}
    assert {
        ScheduleAction.CREATE,
        ScheduleAction.CREATE_RECURRING,
        ScheduleAction.UPDATE,
        ScheduleAction.DELETE,
        ScheduleAction.QUERY,
    } <= actions
