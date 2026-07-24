from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from learn_hermes_agent.providers.base import ProviderTransport
from learn_hermes_agent.providers.client import ProviderClient
from learn_hermes_agent.providers.fake import FakeProviderTransport, tool_demo_provider
from learn_hermes_agent.providers.fallback import FallbackProviderTransport
from learn_hermes_agent.providers.openai_compatible import OpenAICompatibleProviderTransport


@dataclass(frozen=True)
class ProviderRuntime:
    """一个 Provider 候选项已经解析完成的运行配置"""
    provider: str
    model: str
    api_mode: str
    timeout_seconds: float
    base_url: str | None = None
    api_key: str | None = field(
        default=None,
        repr=False,  # repr=False，避免打印对象时泄漏密钥
    )

    def __post_init__(self) -> None:
        """__post_init__() 是 dataclass 的特殊方法，在自动生成的 __init__() 完成后立即调用。"""
        if not self.provider.strip():
            raise ValueError(
                "Provider runtime provider must not be empty"
            )
        if not self.model.strip():
            raise ValueError(
                "Provider runtime model must not be empty"
            )
        if not self.api_mode.strip():
            raise ValueError(
                "Provider runtime api_mode must not be empty"
            )
        if (
                isinstance(self.timeout_seconds, bool)
                or not isinstance(
                    self.timeout_seconds,
                    (int, float),
                )
                or self.timeout_seconds <= 0
        ):
            raise ValueError(
                "Provider runtime timeout_seconds "
                "must be positive"
            )


@dataclass(frozen=True)
class ProviderBinding:
    """把Provider运行配置与负责原始请求的 Client 配对"""
    runtime: ProviderRuntime
    client: ProviderClient


OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai-compatible", "openai"})


def build_provider_transport(config: dict[str, Any], *, tool_demo: bool = False) -> ProviderTransport:
    model_config = _get_model_config(config)

    if tool_demo:
        model = _get_string(model_config, "default", "fake-basic")
        return tool_demo_provider(model=model)

    primary_provider = _build_single_provider(model_config)
    fallback_configs = model_config.get("fallbacks")

    if not isinstance(fallback_configs, list) or not fallback_configs:
        return primary_provider

    providers: list[ProviderTransport] = [primary_provider]

    for fallback_config in fallback_configs:
        if not isinstance(fallback_config, dict):
            continue
        providers.append(_build_single_provider(fallback_config))

    if len(providers) == 1:
        return primary_provider

    return FallbackProviderTransport(providers)


def _build_single_provider(model_config: dict[str, Any]) -> ProviderTransport:
    model = _get_string(model_config, "default", "fake-basic")
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
