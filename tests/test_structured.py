"""Tests for the provider-agnostic structured-output repair loop."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from scheduling_agent.structured import StructuredOutputError, structured_call


class Foo(BaseModel):
    x: int


def _model(*replies: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))


def test_structured_success_first_try() -> None:
    out = structured_call(_model('{"x": 5}'), [HumanMessage(content="x")], Foo)
    assert out.x == 5


def test_structured_extracts_json_from_markdown_fence() -> None:
    out = structured_call(
        _model('```json\n{"x": 9}\n```'), [HumanMessage(content="x")], Foo
    )
    assert out.x == 9


def test_structured_repairs_after_bad_output() -> None:
    model = _model("not json at all", '{"x": 7}')
    out = structured_call(model, [HumanMessage(content="x")], Foo, max_retries=2)
    assert out.x == 7


def test_structured_gives_up_after_retries() -> None:
    model = _model("bad", "still bad", "nope")
    with pytest.raises(StructuredOutputError):
        structured_call(model, [HumanMessage(content="x")], Foo, max_retries=2)
