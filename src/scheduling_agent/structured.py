"""Provider-agnostic structured output via a validate-and-repair loop.

Rather than rely on provider-native tool calling (which the subscription
backend cannot do), every provider produces JSON text that is validated
against a Pydantic schema; on failure the model is re-prompted with the
validation error, up to a bounded number of retries. This makes provider
differences a measured quantity, not a bug class.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError


class StructuredOutputError(RuntimeError):
    """Raised when the model cannot produce schema-valid output within retries."""


def structured_call[T: BaseModel](
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    schema: type[T],
    *,
    max_retries: int = 2,
) -> T:
    """Invoke ``model`` and return an instance of ``schema``, repairing on failure.

    Raises:
        StructuredOutputError: if no valid instance is produced within
            ``max_retries`` repair attempts.
    """
    conversation: list[BaseMessage] = [
        *messages,
        HumanMessage(content=_instruction(schema)),
    ]
    last_error: Exception | None = None

    for _ in range(max_retries + 1):
        response = model.invoke(conversation)
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
