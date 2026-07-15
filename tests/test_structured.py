"""Tests for the provider-agnostic structured-output repair loop."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from scheduling_agent.observability import configure_logging
from scheduling_agent.structured import StructuredOutputError, structured_call


class Foo(BaseModel):
    x: int


def _model(*replies: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))


class _NativeModel(GenericFakeChatModel):
    """A model whose native structured output is scripted, for testing the
    native path and its fallbacks. The repair-loop fallback uses the canned
    text messages passed to the constructor."""

    build_error: bool = False
    invoke_error: bool = False
    wrong_type: bool = False

    def with_structured_output(self, schema: Any = None, **kwargs: Any) -> Any:
        if self.build_error:
            raise NotImplementedError("no native structured output")

        def run(_messages: Any) -> Any:
            if self.invoke_error:
                raise ValueError("endpoint rejected the schema")
            return {"not": "a model"} if self.wrong_type else schema(x=7)

        return RunnableLambda(run)


def _native(
    build_error: bool = False, invoke_error: bool = False, wrong_type: bool = False
) -> _NativeModel:
    # canned fallback text is valid so the repair loop succeeds when used.
    model = _NativeModel(messages=iter([AIMessage(content='{"x": 5}')]))
    model.build_error = build_error
    model.invoke_error = invoke_error
    model.wrong_type = wrong_type
    return model


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


def test_native_structured_output_used_when_method_given() -> None:
    out = structured_call(
        _native(), [HumanMessage(content="x")], Foo, method="json_schema"
    )
    assert out.x == 7  # from native path, not the canned fallback (x=5)


def test_native_unsupported_falls_back_to_repair() -> None:
    out = structured_call(
        _native(build_error=True),
        [HumanMessage(content="x")],
        Foo,
        method="json_schema",
    )
    assert out.x == 5  # native raised on build -> repair loop used the canned text


def test_native_invoke_error_falls_back_to_repair() -> None:
    out = structured_call(
        _native(invoke_error=True),
        [HumanMessage(content="x")],
        Foo,
        method="json_schema",
    )
    assert out.x == 5


def test_native_wrong_type_falls_back_to_repair() -> None:
    out = structured_call(
        _native(wrong_type=True), [HumanMessage(content="x")], Foo, method="json_schema"
    )
    assert out.x == 5


def test_no_method_skips_native_path() -> None:
    # A model whose native path would return x=7 is NOT used when method is None.
    out = structured_call(_native(), [HumanMessage(content="x")], Foo)
    assert out.x == 5  # repair loop over the canned text


def test_provider_label_appears_in_logs() -> None:
    # The configured provider name (not the ChatOpenAI class name) is logged.
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    structured_call(
        _native(),
        [HumanMessage(content="x")],
        Foo,
        method="json_schema",
        provider="lmstudio",
    )
    records = [json.loads(line) for line in buf.getvalue().splitlines() if line]
    invoke = next(r for r in records if r["message"] == "llm_invoke")
    assert invoke["provider"] == "lmstudio"
