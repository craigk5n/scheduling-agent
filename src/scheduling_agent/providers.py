"""Model provider factory: one BaseChatModel from the configured provider.

The graph depends only on ``langchain_core``'s ``BaseChatModel``; this factory
selects the backend from settings so provider choice is invisible above it.
Structured output is handled uniformly by the repair loop in ``structured``
(not provider-native tool calling), so even the subscription adapter — which
cannot enforce schemas — participates on equal footing.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - used only for the local `claude` CLI, no shell
import tempfile
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import SecretStr

from scheduling_agent.settings import DEFAULT_BASE_URL, ModelProvider, Settings

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

#: Fallback model tag per local provider (override with MODEL_NAME).
DEFAULT_LOCAL_MODEL: dict[ModelProvider, str] = {
    ModelProvider.OLLAMA: "llama3.1",
    ModelProvider.LMSTUDIO: "local-model",
}

# System prompt for the subscription (claude CLI) path: strip the coding-agent
# persona so it behaves as a plain structured-output completion.
_SUBSCRIPTION_SYSTEM_PROMPT = (
    "You are a structured-output service. Return exactly and only what the user "
    "asks for, which is a single JSON object. No preamble, no explanation, no "
    "code fences, no follow-up questions, and do not use any tools."
)


def get_chat_model(
    settings: Settings, *, model: str | None = None, temperature: float = 0.0
) -> BaseChatModel:
    """Build a chat model for the configured provider.

    Model precedence: explicit ``model`` argument, then ``settings.model``
    (MODEL_NAME), then the provider default.
    """
    key = settings.credential.get_secret_value()
    provider = settings.model_provider

    if provider is ModelProvider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        # kwargs are splatted from an Any-dict: langchain's type stubs disagree
        # with the runtime `model=`/`api_key=` constructor keywords.
        params: dict[str, Any] = {
            "model": model or settings.model or DEFAULT_ANTHROPIC_MODEL,
            "api_key": SecretStr(key),
            "temperature": temperature,
        }
        return ChatAnthropic(**params)

    if provider is ModelProvider.OPENROUTER:
        from langchain_openai import ChatOpenAI

        params = {
            "model": model or settings.model or DEFAULT_OPENROUTER_MODEL,
            "api_key": SecretStr(key),
            "base_url": OPENROUTER_BASE_URL,
            "temperature": temperature,
        }
        return ChatOpenAI(**params)

    if provider in (ModelProvider.OLLAMA, ModelProvider.LMSTUDIO):
        from langchain_openai import ChatOpenAI

        # Local models via their OpenAI-compatible endpoint. We do NOT set a
        # construction-time response_format: ollama accepts `json_object` but
        # LM Studio rejects it (only `json_schema`/`text`). The validate-and-
        # repair loop enforces JSON uniformly instead.
        params = {
            "model": model or settings.model or DEFAULT_LOCAL_MODEL[provider],
            "api_key": SecretStr(key),
            "base_url": settings.base_url or DEFAULT_BASE_URL[provider],
            "temperature": temperature,
        }
        return ChatOpenAI(**params)

    # Only claude-subscription remains.
    return ClaudeSubscriptionChatModel(
        model=model or settings.model or DEFAULT_ANTHROPIC_MODEL,
        oauth_token=settings.credential,
    )


class ClaudeSubscriptionChatModel(BaseChatModel):
    """Best-effort chat model backed by a Claude Pro/Max plan via the CLI.

    Generation shells out to the local ``claude`` CLI in print mode using the
    plan's OAuth token. Structured output is not API-enforced; correctness
    comes from the repair loop. This path is validated in live/manual testing
    (the CLI call itself is not exercised in CI).
    """

    model: str = DEFAULT_ANTHROPIC_MODEL
    oauth_token: SecretStr

    @property
    def _llm_type(self) -> str:
        return "claude-subscription"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self._invoke_cli(_messages_to_prompt(messages))
        message = AIMessage(content=text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _invoke_cli(self, prompt: str) -> str:  # pragma: no cover - needs claude CLI
        claude = shutil.which("claude")
        if claude is None:
            raise RuntimeError(
                "the 'claude' CLI is required for the claude-subscription provider"
            )
        env = {
            **os.environ,
            "CLAUDE_CODE_OAUTH_TOKEN": self.oauth_token.get_secret_value(),
        }
        # `claude -p` is a full Claude Code agent: by default it loads the cwd's
        # project (code, CLAUDE.md) and answers like a coding assistant. Force it
        # to behave as a plain completion: run in an empty directory, replace the
        # system prompt, and drop the dynamic project/env sections.
        with tempfile.TemporaryDirectory() as workdir:
            completed = subprocess.run(  # nosec B603 - resolved path, no shell; token via env
                [
                    claude,
                    "-p",
                    prompt,
                    "--model",
                    self.model,
                    "--output-format",
                    "text",
                    "--system-prompt",
                    _SUBSCRIPTION_SYSTEM_PROMPT,
                    "--exclude-dynamic-system-prompt-sections",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=workdir,
                check=True,
            )
        return completed.stdout.strip()


def _messages_to_prompt(messages: Sequence[BaseMessage]) -> str:
    parts = []
    for message in messages:
        content = message.content
        parts.append(content if isinstance(content, str) else str(content))
    return "\n\n".join(parts)
