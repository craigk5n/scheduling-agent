"""Provider-agnostic structured output via a validate-and-repair loop.

Rather than rely on provider-native tool calling (which the subscription
backend cannot do), every provider produces JSON text that is validated
against a Pydantic schema; on failure the model is re-prompted with the
validation error, up to a bounded number of retries. This makes provider
differences a measured quantity, not a bug class.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from scheduling_agent.observability import log_event


class StructuredOutputError(RuntimeError):
    """Raised when the model cannot produce schema-valid output within retries."""


def structured_call[T: BaseModel](
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    schema: type[T],
    *,
    method: str | None = None,
    provider: str | None = None,
    max_retries: int = 2,
) -> T:
    """Return an instance of ``schema`` from ``model``.

    When ``method`` is given (e.g. ``"json_schema"``), native structured output
    is attempted first — the provider constrains the model to the schema. On any
    failure (an endpoint that doesn't support it, or a model like the
    subscription CLI that can't), it falls back to a validate-and-repair loop
    over plain text.

    ``provider`` is a human-readable label for logs (e.g. "lmstudio"); it
    defaults to the model's class name, which is the same ``ChatOpenAI`` for
    every OpenAI-compatible backend.

    Raises:
        StructuredOutputError: if no valid instance is produced.
    """
    label = provider or type(model).__name__
    if method is not None:
        native = _native_structured_output(model, messages, schema, method, label)
        if native is not None:
            return native
    return _repair_loop(model, messages, schema, max_retries, label)


def _model_id(model: BaseChatModel) -> str | None:
    """Best-effort model tag/id for logs across provider classes."""
    value = getattr(model, "model_name", None) or getattr(model, "model", None)
    return str(value) if value else None


def _native_structured_output[T: BaseModel](
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    schema: type[T],
    method: str,
    provider: str,
) -> T | None:
    """Try provider-native structured output; return None to signal fallback."""
    model_id = _model_id(model)
    try:
        structured = model.with_structured_output(schema, method=method)
    except (NotImplementedError, ValueError, TypeError) as exc:
        log_event("structured_unsupported", provider=provider, error=str(exc)[:200])
        return None
    log_event(
        "llm_invoke",
        provider=provider,
        model=model_id,
        schema=schema.__name__,
        mode=method,
    )
    start = time.monotonic()
    try:
        result = structured.invoke(list(messages))
    except Exception as exc:  # noqa: BLE001 - any failure falls back to repair
        log_event("structured_fallback", provider=provider, error=str(exc)[:200])
        return None
    log_event(
        "llm_response",
        provider=provider,
        model=model_id,
        mode=method,
        elapsed_ms=round((time.monotonic() - start) * 1000),
    )
    return result if isinstance(result, schema) else None


def _repair_loop[T: BaseModel](
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    schema: type[T],
    max_retries: int,
    provider: str,
) -> T:
    conversation: list[BaseMessage] = [
        *messages,
        HumanMessage(content=_instruction(schema)),
    ]
    last_error: Exception | None = None

    model_id = _model_id(model)
    for attempt in range(max_retries + 1):
        log_event(
            "llm_invoke",
            provider=provider,
            model=model_id,
            schema=schema.__name__,
            attempt=attempt + 1,
        )
        start = time.monotonic()
        response = model.invoke(conversation)
        log_event(
            "llm_response",
            provider=provider,
            model=model_id,
            attempt=attempt + 1,
            elapsed_ms=round((time.monotonic() - start) * 1000),
        )
        text = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        try:
            return schema.model_validate_json(_extract_json(text))
        except (ValidationError, ValueError) as exc:
            last_error = exc
            conversation.append(AIMessage(content=text))
            conversation.append(
                HumanMessage(
                    content=(
                        f"That response was not valid: {exc}. "
                        "Return corrected JSON only, with no prose or code fences."
                    )
                )
            )

    raise StructuredOutputError(
        f"could not obtain schema-valid output after "
        f"{max_retries} retries: {last_error}"
    )


def _instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with ONLY a single JSON object (no prose, no code fences) "
        "matching this JSON schema:\n" + json.dumps(schema.model_json_schema())
    )


def _extract_json(text: str) -> str:
    """Best-effort extraction of the first JSON object from model text."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]
