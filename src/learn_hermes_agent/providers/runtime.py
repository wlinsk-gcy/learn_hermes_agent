from __future__ import annotations

import os
from typing import Any

from learn_hermes_agent.providers.base import ProviderTransport
from learn_hermes_agent.providers.fake import FakeProviderTransport, tool_demo_provider
from learn_hermes_agent.providers.openai_compatible import OpenAICompatibleProviderTransport

OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai-compatible", "openai"})


def build_provider_transport(config: dict[str, Any], *, tool_demo: bool = False) -> ProviderTransport:
    model_config = _get_model_config(config)
    model = _get_string(model_config, "default", "fake-basic")

    if tool_demo:
        return tool_demo_provider(model=model)

    provider = _get_string(model_config, "provider", "fake").lower()

    if provider == "fake":
        return FakeProviderTransport(model=model)

    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        base_url = _get_string(model_config, "base_url", "https://api.openai.com/v1")
        api_key_env = _get_string(model_config, "api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "")

        if not api_key:
            raise ValueError(f"Missing API key env var for provider {provider!r}: {api_key_env}")

        timeout_seconds = _get_positive_float(model_config, "timeout_seconds", 60.0)

        return OpenAICompatibleProviderTransport(
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(f"Unsupported model provider: {provider!r}")


def _get_model_config(config: dict[str, Any]) -> dict[str, Any]:
    model_config = config.get("model")
    if not isinstance(model_config, dict):
        return {}
    return model_config


def _get_string(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key)
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default
    return text


def _get_positive_float(config: dict[str, Any], key: str, default: float) -> float:
    value = config.get(key)

    if isinstance(value, bool):
        return default

    if isinstance(value, (int, float)) and value > 0:
        return float(value)

    return default
