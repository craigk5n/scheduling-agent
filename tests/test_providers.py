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
    structured_output_method,
)
from scheduling_agent.settings import ModelProvider, Settings

_CRED = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "claude-subscription": "CLAUDE_CODE_OAUTH_TOKEN",
}


def _settings(provider: str, model: str | None = None) -> Settings:
    env = {"MODEL_PROVIDER": provider}
    if provider in _CRED:  # local providers need no credential
        env[_CRED[provider]] = "secret"
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


def test_ollama_provider_builds_local_chatopenai() -> None:
    model = get_chat_model(Settings.from_env({"MODEL_PROVIDER": "ollama"}))
    assert isinstance(model, ChatOpenAI)
    assert "11434" in str(model.openai_api_base)
    assert model.model_name == "llama3.1"  # default local model tag


def test_lmstudio_provider_builds_local_chatopenai() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "lmstudio", "MODEL_NAME": "qwen2.5-7b"}
    )
    model = get_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert "1234" in str(model.openai_api_base)
    assert model.model_name == "qwen2.5-7b"


def test_ollama_base_url_override() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://gpu-box:11434/v1"}
    )
    model = get_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert "gpu-box:11434" in str(model.openai_api_base)


def test_local_provider_needs_no_credential() -> None:
    # No API key set, yet it builds fine.
    settings = Settings.from_env({"MODEL_PROVIDER": "ollama"})
    assert settings.model_provider is ModelProvider.OLLAMA
    assert get_chat_model(settings) is not None


def test_structured_output_method_per_provider() -> None:
    assert structured_output_method(_settings("ollama")) == "json_schema"
    assert structured_output_method(_settings("lmstudio")) == "json_schema"
    assert structured_output_method(_settings("openrouter")) == "json_schema"
    # Anthropic and the subscription CLI use the repair loop (no native method).
    assert structured_output_method(_settings("anthropic")) is None
    assert structured_output_method(_settings("claude-subscription")) is None


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
