"""Tests for the model provider factory and the subscription adapter."""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from scheduling_agent.providers import (
    DEFAULT_ANTHROPIC_MODEL,
    ClaudeSubscriptionChatModel,
    get_chat_model,
)
from scheduling_agent.settings import Settings

_CRED = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "claude-subscription": "CLAUDE_CODE_OAUTH_TOKEN",
}


def _settings(provider: str, model: str | None = None) -> Settings:
    env = {"MODEL_PROVIDER": provider, _CRED[provider]: "secret"}
    if model:
        env["MODEL_NAME"] = model
    return Settings.from_env(env)


def test_anthropic_provider_builds_chatanthropic() -> None:
    model = get_chat_model(_settings("anthropic"))
    assert isinstance(model, ChatAnthropic)
    assert model.model == DEFAULT_ANTHROPIC_MODEL


def test_openrouter_provider_builds_chatopenai_with_base_url() -> None:
    model = get_chat_model(_settings("openrouter"))
    assert isinstance(model, ChatOpenAI)
    assert "openrouter.ai" in str(model.openai_api_base)


def test_subscription_provider_builds_adapter() -> None:
    model = get_chat_model(_settings("claude-subscription"))
    assert isinstance(model, ClaudeSubscriptionChatModel)
    assert isinstance(model, BaseChatModel)
    assert model._llm_type == "claude-subscription"


def test_model_name_override_is_respected() -> None:
    model = get_chat_model(_settings("anthropic", model="claude-opus-4-8"))
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-opus-4-8"


def test_explicit_model_argument_wins() -> None:
    model = get_chat_model(_settings("anthropic", model="from-env"), model="explicit")
    assert isinstance(model, ChatAnthropic)
    assert model.model == "explicit"


def test_subscription_generate_uses_cli_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ClaudeSubscriptionChatModel,
        "_invoke_cli",
        lambda self, prompt: f"echo:{prompt}",
    )
    model = ClaudeSubscriptionChatModel(oauth_token=SecretStr("tok"))
    result = model.invoke([HumanMessage(content="hello")])
    assert result.content == "echo:hello"
