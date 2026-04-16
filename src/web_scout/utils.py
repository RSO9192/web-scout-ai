"""Shared utilities for the web-scout-ai package."""

from __future__ import annotations

import os
from typing import Union

from agents.extensions.models.litellm_model import LitellmModel

# Map of LiteLLM provider prefixes to their environment variable names.
# See https://docs.litellm.ai/docs/providers for the full list.
_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai":       ["OPENAI_API_KEY"],
    "anthropic":    ["ANTHROPIC_API_KEY"],
    "gemini":       ["GEMINI_API_KEY"],
    "google":       ["GEMINI_API_KEY"],
    "vertex_ai":    ["GOOGLE_APPLICATION_CREDENTIALS"],
    "mistral":      ["MISTRAL_API_KEY"],
    "cohere":       ["COHERE_API_KEY"],
    "groq":         ["GROQ_API_KEY"],
    "together_ai":  ["TOGETHERAI_API_KEY", "TOGETHER_API_KEY"],
    "fireworks_ai": ["FIREWORKS_API_KEY"],
    "deepseek":     ["DEEPSEEK_API_KEY"],
    "perplexity":   ["PERPLEXITYAI_API_KEY"],
    "cerebras":     ["CEREBRAS_API_KEY"],
    "sambanova":    ["SAMBANOVA_API_KEY"],
    "azure":        ["AZURE_API_KEY"],
    "bedrock":      ["AWS_ACCESS_KEY_ID"],
}

# Providers that are natively supported by the OpenAI Agents SDK
# (passed as plain model name strings, not wrapped in LitellmModel).
_NATIVE_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")


def _detect_provider(model_name: str) -> str | None:
    """Extract the provider prefix from a LiteLLM model string.

    E.g. ``"gemini/gemini-2.0-flash"`` → ``"gemini"``,
    ``"anthropic/claude-sonnet-4-20250514"`` → ``"anthropic"``.
    """
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return None


def _find_api_key(provider: str) -> str | None:
    """Look up the API key for a provider from environment variables."""
    env_names = _PROVIDER_ENV_KEYS.get(provider, [])
    for name in env_names:
        key = os.getenv(name)
        if key:
            return key
    return None


def get_model(model_name: str) -> Union[str, LitellmModel]:
    """Return a model object suitable for the OpenAI Agents SDK.

    - **Native OpenAI models** (``gpt-*``, ``o1*``, ``o3*``, ``o4*``) are
      returned as plain strings — handled natively by the Agents SDK.
    - **Everything else** is wrapped in ``LitellmModel`` with automatic
      API key detection from standard environment variables.

    Supports all `LiteLLM providers <https://docs.litellm.ai/docs/providers>`_:
    OpenAI, Anthropic, Google (Gemini), Mistral, Cohere, Groq, Together,
    Fireworks, DeepSeek, Azure, Bedrock, and more.

    If the API key cannot be found automatically, set the appropriate
    environment variable for your provider (e.g. ``ANTHROPIC_API_KEY``,
    ``GEMINI_API_KEY``, ``MISTRAL_API_KEY``).
    """
    if any(model_name.startswith(p) for p in _NATIVE_OPENAI_PREFIXES):
        return model_name

    provider = _detect_provider(model_name)
    api_key = _find_api_key(provider) if provider else None

    if api_key:
        return LitellmModel(model=model_name, api_key=api_key)

    # No key found — let LiteLLM try its own env-var detection.
    # This covers providers not in our map, or custom setups.
    return LitellmModel(model=model_name, api_key=None)
