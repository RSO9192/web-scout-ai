"""Shared utilities for the web-scout-ai package."""

import os
from typing import Union

from agents.extensions.models.litellm_model import LitellmModel

# Map of LiteLLM provider prefixes to their environment variable names.
# See https://docs.litellm.ai/docs/providers for the full list.
_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "gemini": ["GEMINI_API_KEY"],
    "google": ["GEMINI_API_KEY"],
    "vertex_ai": ["GOOGLE_APPLICATION_CREDENTIALS"],
    "mistral": ["MISTRAL_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "together_ai": ["TOGETHERAI_API_KEY", "TOGETHER_API_KEY"],
    "fireworks_ai": ["FIREWORKS_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "perplexity": ["PERPLEXITYAI_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
    "sambanova": ["SAMBANOVA_API_KEY"],
    "azure": ["AZURE_API_KEY"],
    "bedrock": ["AWS_ACCESS_KEY_ID"],
    "bedrock_mantle": ["BEDROCK_MANTLE_API_KEY", "AWS_BEARER_TOKEN_BEDROCK"],
}

# Providers that are natively supported by the OpenAI Agents SDK
# (passed as plain model name strings, not wrapped in LitellmModel).
_NATIVE_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")

# OpenAI models on Bedrock Mantle share LiteLLM's route and are only in us-east-1.
_BEDROCK_MANTLE_OPENAI_PREFIX = "bedrock_mantle/openai.gpt-"
_BEDROCK_MANTLE_OPENAI_REGION = "us-east-1"


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
        if key and not (key.startswith("${") and key.endswith("}")):
            return key
    return None


def _prepare_bedrock_mantle_openai(model_name: str) -> None:
    """Put OpenAI Mantle models on LiteLLM's normal us-east-1 route.

    GPT-5.6 and GPT-6 Luna share that route. The bundled price map lists
    GPT-5.6 and not GPT-6; a missing entry would send GPT-6 to ``/v1`` and
    reject ``reasoning_effort``. Registering the GPT-5.6 capability template
    keeps both models on ``/openai/v1``.
    """
    if not model_name.startswith(_BEDROCK_MANTLE_OPENAI_PREFIX):
        return
    os.environ["BEDROCK_MANTLE_REGION"] = _BEDROCK_MANTLE_OPENAI_REGION
    import litellm

    if model_name in litellm.model_cost:
        return
    template = litellm.model_cost.get("bedrock_mantle/openai.gpt-5.6-luna")
    if not template:
        return
    litellm.register_model({model_name: dict(template)})


def get_litellm_base_url(model_name: str) -> str | None:
    """Return a base URL only when LiteLLM cannot build the provider route.

    OpenAI models on Bedrock Mantle stay on LiteLLM's own URL builder. Both
    GPT-5.6 and GPT-6 are served from ``us-east-1``.
    """
    _prepare_bedrock_mantle_openai(model_name)
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
    base_url = get_litellm_base_url(model_name)

    if api_key:
        return LitellmModel(model=model_name, base_url=base_url, api_key=api_key)

    # No key found — let LiteLLM try its own env-var detection.
    # This covers providers not in our map, or custom setups.
    return LitellmModel(model=model_name, base_url=base_url, api_key=None)
