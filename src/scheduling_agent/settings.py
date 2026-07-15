"""Environment-driven application settings.

The agent runs unchanged on three model backends selected by the
``MODEL_PROVIDER`` environment variable. This module reads that choice,
verifies the credential the chosen provider requires, and fails fast
with an actionable message when configuration is missing or invalid.

Credentials are wrapped in :class:`pydantic.SecretStr` so they are not
accidentally exposed through logs, tracebacks, or ``repr()``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, SecretStr


class ModelProvider(StrEnum):
    """Supported model backends (see docs/ARCHITECTURE.md §2a)."""

    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    CLAUDE_SUBSCRIPTION = "claude-subscription"


#: Environment variable holding the credential each provider requires.
REQUIRED_CREDENTIAL: dict[ModelProvider, str] = {
    ModelProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    ModelProvider.OPENROUTER: "OPENROUTER_API_KEY",
    ModelProvider.CLAUDE_SUBSCRIPTION: "CLAUDE_CODE_OAUTH_TOKEN",
}

#: Provider used when ``MODEL_PROVIDER`` is unset or blank.
DEFAULT_PROVIDER: ModelProvider = ModelProvider.ANTHROPIC


class SettingsError(ValueError):
    """Raised when environment configuration is missing or invalid."""


def _parse_provider(raw: str | None) -> ModelProvider:
    if raw is None or not raw.strip():
        return DEFAULT_PROVIDER
    try:
        return ModelProvider(raw.strip().lower())
    except ValueError:
        valid = ", ".join(provider.value for provider in ModelProvider)
        raise SettingsError(
            f"Unknown MODEL_PROVIDER {raw!r}. Valid values: {valid}."
        ) from None


class Settings(BaseModel):
    """Validated runtime configuration for the scheduling agent."""

    # ``model_provider`` intentionally uses the ``model_`` prefix that
    # Pydantic reserves for itself, so that namespace guard is disabled.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    model_provider: ModelProvider
    credential: SecretStr
    #: Optional model override (from MODEL_NAME); falls back to a provider default.
    model: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from an environment mapping (defaults to ``os.environ``).

        Raises:
            SettingsError: if the provider is unknown or the credential the
                selected provider requires is absent or blank.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        provider = _parse_provider(source.get("MODEL_PROVIDER"))
        credential_var = REQUIRED_CREDENTIAL[provider]
        credential = source.get(credential_var, "").strip()
        if not credential:
            raise SettingsError(
                f"Model provider {provider.value!r} requires the "
                f"{credential_var} environment variable to be set."
            )
        model = (source.get("MODEL_NAME") or "").strip() or None
        return cls(
            model_provider=provider,
            credential=SecretStr(credential),
            model=model,
        )
