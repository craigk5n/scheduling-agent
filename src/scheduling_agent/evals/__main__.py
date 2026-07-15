"""Entry point: ``python -m scheduling_agent.evals``."""

import sys

from scheduling_agent.evals.runner import run_cli

if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_cli(sys.argv[1:]))
