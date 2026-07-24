from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from learn_hermes_agent.providers import (
    get_provider_profile,
)
from learn_hermes_agent.providers.client import (
    ProviderClient,
)
from learn_hermes_agent.providers.fake import (
    FakeProviderClient,
    tool_demo_client,
)
from learn_hermes_agent.providers.openai_compatible import (
    OpenAICompatibleClient,
)


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


def _is_azure_openai_v1_base_url(
        base_url: str,
) -> bool:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        return False

    hostname = (parsed.hostname or "").lower()
    suffix = ".openai.azure.com"

    if not hostname.endswith(suffix):
        return False

    resource_name = hostname[:-len(suffix)]
    if not resource_name or "." in resource_name:
        return False

    return (
            parsed.scheme.lower() == "https"
            and parsed.username is None
            and parsed.password is None
            and port is None
            and parsed.path.rstrip("/") == "/openai/v1"
            and not parsed.query
            and not parsed.fragment
    )


def _resolve_provider_runtime(
        model_config: dict[str, Any],
) -> ProviderRuntime:
    requested_provider = _get_string(
        model_config,
        "provider",
        "fake",
    ).lower()
    # 按名称/别名取得 ProviderProfile
    profile = get_provider_profile(
        requested_provider
    )
    # 读取配置和环境变量
    model = _get_string(
        model_config,
        "default",
        "fake-basic",
    )

    timeout_seconds = _get_positive_float(
        model_config,
        "timeout_seconds",
        60.0,
    )

    base_url: str | None = None
    api_key: str | None = None

    if (
            profile.default_base_url is not None
            or profile.requires_base_url
    ):
        base_url = _get_string(
            model_config,
            "base_url",
            profile.default_base_url or "",
        )

    if profile.requires_base_url and not base_url:
        raise ValueError(
            "Provider requires model.base_url: "
            f"{profile.name!r}"
        )
    if (
            profile.name == "azure-openai"
            and (
                    base_url is None
                    or not _is_azure_openai_v1_base_url(
                        base_url
                    )
            )
    ):
        raise ValueError(
            "Azure OpenAI v1 base_url must match "
            "'https://<resource>.openai.azure.com/"
            "openai/v1'"
        )

    if profile.requires_api_key:
        default_api_key_env = (
                profile.api_key_env or ""
        )
        api_key_env = _get_string(
            model_config,
            "api_key_env",
            default_api_key_env,
        )
        api_key = os.environ.get(
            api_key_env,
            "",
        )

        if not api_key:
            raise ValueError(
                "Missing API key env var for "
                f"provider {requested_provider!r}: "
                f"{api_key_env}"
            )
    # 生成不可变 ProviderRuntime
    return ProviderRuntime(
        provider=profile.name,
        model=model,
        api_mode=profile.api_mode,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )


def _build_provider_binding(
        model_config: dict[str, Any],
) -> ProviderBinding:
    runtime = _resolve_provider_runtime(
        model_config
    )

    if runtime.provider == "fake":
        client: ProviderClient = FakeProviderClient(
            model=runtime.model
        )

    elif runtime.api_mode == "chat_completions":
        if not runtime.base_url or not runtime.api_key:
            raise ValueError(
                "OpenAI-compatible runtime requires "
                "base_url and api_key"
            )

        client = OpenAICompatibleClient(
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            timeout_seconds=runtime.timeout_seconds,
        )

    else:
        raise ValueError(
            "No client available for provider "
            f"{runtime.provider!r} with api_mode "
            f"{runtime.api_mode!r}"
        )

    return ProviderBinding(
        runtime=runtime,
        client=client,
    )


def build_provider_bindings(
        config: dict[str, Any],
        *,
        tool_demo: bool = False,
) -> list[ProviderBinding]:
    """构建 primary/fallback chain"""
    model_config = _get_model_config(config)

    if tool_demo:
        # tool_demo=True 时强制使用 fake，但保留配置中的模型名称和 timeout
        model = _get_string(
            model_config,
            "default",
            "fake-basic",
        )

        runtime = ProviderRuntime(
            provider="fake",
            model=model,
            api_mode="chat_completions",
            timeout_seconds=_get_positive_float(
                model_config,
                "timeout_seconds",
                60.0,
            ),
        )

        return [
            ProviderBinding(
                runtime=runtime,
                client=tool_demo_client(model=model),
            )
        ]
    """
    这里默认优先级是先取配置中的第一个，例如:
    model:
    provider: fake
    default: primary-model
    fallbacks:
      - provider: fake
        default: fallback-a
      - provider: fake
        default: fallback-b
        
    构建结果是：
    bindings = [
      primary_binding,     # index 0
      fallback_a_binding,  # index 1
      fallback_b_binding,  # index 2
    ]
    后续 AIAgent 按顺序请求：
    
    bindings[0] 失败
        -> bindings[1] 失败
        -> bindings[2]
    
    因此“primary 永远是列表第 0 项”表示：
    
    - 第 0 项是首选 Provider。
    - 第 1 项以后才是 fallback。
    - 不代表 primary 一定成功。
    - last_provider_index > 0 时，就说明本轮使用了 fallback。
    """
    bindings = [
        _build_provider_binding(model_config)
    ]

    fallback_configs = model_config.get(
        "fallbacks"
    )

    if isinstance(fallback_configs, list):
        for fallback_config in fallback_configs:
            if not isinstance(
                    fallback_config,
                    dict,
            ):
                continue

            bindings.append(
                _build_provider_binding(
                    fallback_config
                )
            )

    return bindings


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
