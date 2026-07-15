"""Load the golden evaluation dataset from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from scheduling_agent.evals.models import EvalCase

DEFAULT_DATASET = Path(__file__).with_name("cases.yaml")


def load_cases(path: Path | str = DEFAULT_DATASET) -> list[EvalCase]:
    """Load and validate eval cases from a YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("eval dataset must be a YAML list of cases")
    return [EvalCase.model_validate(entry) for entry in raw]
