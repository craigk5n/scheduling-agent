"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from scheduling_agent.observability import set_correlation_id


@pytest.fixture(autouse=True)
def _reset_correlation_id() -> Iterator[None]:
    """Keep the correlation-id contextvar from leaking between tests (the CLI
    sets it per turn)."""
    set_correlation_id(None)
    yield
    set_correlation_id(None)
