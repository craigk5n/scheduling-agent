"""Tests for the environment-driven settings loader.

The loader selects a model provider and verifies that the credential
required by that provider is present, failing fast with a clear message
otherwise. Environment is injected as a plain mapping so the tests stay
pure (no monkeypatching of os.environ).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scheduling_agent.settings import (
    DEFAULT_PROVIDER,
    ModelProvider,
    Settings,
    SettingsError,
)


def test_anthropic_provider_with_key_loads() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
    )
    assert settings.model_provider is ModelProvider.ANTHROPIC
    assert settings.credential.get_secret_value() == "sk-ant-xxx"


def test_openrouter_provider_with_key_loads() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "or-xxx"}
    )
    assert settings.model_provider is ModelProvider.OPENROUTER
    assert settings.credential.get_secret_value() == "or-xxx"


def test_claude_subscription_provider_with_token_loads() -> None:
    settings = Settings.from_env(
        {
            "MODEL_PROVIDER": "claude-subscription",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-xxx",
        }
    )
    assert settings.model_provider is ModelProvider.CLAUDE_SUBSCRIPTION
    assert settings.credential.get_secret_value() == "oauth-xxx"


def test_missing_provider_defaults_to_anthropic() -> None:
    settings = Settings.from_env({"ANTHROPIC_API_KEY": "sk-ant-xxx"})
    assert settings.model_provider is DEFAULT_PROVIDER
    assert DEFAULT_PROVIDER is ModelProvider.ANTHROPIC


def test_blank_provider_defaults_to_anthropic() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "   ", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
    )
    assert settings.model_provider is ModelProvider.ANTHROPIC


def test_provider_value_is_case_insensitive_and_trimmed() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "  Anthropic  ", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
    )
    assert settings.model_provider is ModelProvider.ANTHROPIC


def test_unknown_provider_raises_with_valid_values_listed() -> None:
    with pytest.raises(SettingsError) as exc:
        Settings.from_env({"MODEL_PROVIDER": "bard", "ANTHROPIC_API_KEY": "x"})
    message = str(exc.value)
    assert "bard" in message
    assert "anthropic" in message
    assert "openrouter" in message
    assert "claude-subscription" in message


def test_missing_credential_raises_naming_the_env_var() -> None:
    with pytest.raises(SettingsError) as exc:
        Settings.from_env({"MODEL_PROVIDER": "openrouter"})
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_wrong_credential_for_selected_provider_raises() -> None:
    # ANTHROPIC_API_KEY is present but the selected provider needs the
    # OpenRouter key; the loader must check the RIGHT credential.
    with pytest.raises(SettingsError) as exc:
        Settings.from_env(
            {"MODEL_PROVIDER": "openrouter", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
        )
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_whitespace_only_credential_is_treated_as_missing() -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"MODEL_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "   "})


def test_settings_is_immutable() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-xxx"}
    )
    with pytest.raises(ValidationError):
        settings.model_provider = ModelProvider.OPENROUTER


def test_credential_is_not_exposed_in_repr() -> None:
    settings = Settings.from_env(
        {"MODEL_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "super-secret-value"}
    )
    assert "super-secret-value" not in repr(settings)
