from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderProfile:
    """声明 Provider 的静态特征，不读取环境变量、不创建 Client、不发送请求。"""
    name: str
    api_mode: str
    aliases: tuple[str, ...] = ()
    default_base_url: str | None = None
    api_key_env: str | None = None
    requires_api_key: bool = True
    # 表示该 Provider 每次请求都要附带的固定 HTTP Header
    # 使用 tuple 而不是 dict，是为了让 frozen ProviderProfile 保持真正不可变
    # tuple[tuple[str, str], ...] 指“任意数量的 (Header名, Header值)”：
    default_headers: tuple[
        tuple[str, str],
        ...
    ] = ()
    # 表示该 Profile 是否属于本地推理端点
    is_local: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "Provider profile name must not be empty"
            )
        if not self.api_mode.strip():
            raise ValueError(
                "Provider profile api_mode must not be empty"
            )

        if not isinstance(self.requires_api_key, bool):
            raise TypeError(
                "Provider profile requires_api_key "
                "must be a boolean"
            )
        if self.requires_api_key and not self.api_key_env:
            raise ValueError(
                "Provider profile requiring an API key "
                "must define api_key_env"
            )

        if not isinstance(self.is_local, bool):
            raise TypeError(
                "Provider profile is_local "
                "must be a boolean"
            )

        if not isinstance(self.default_headers, tuple):
            raise TypeError(
                "Provider profile default_headers "
                "must be a tuple"
            )

        for header in self.default_headers:
            if (
                    not isinstance(header, tuple)
                    or len(header) != 2
            ):
                raise TypeError(
                    "Each default header must be "
                    "a name-value tuple"
                )

            name, value = header
            if (
                    not isinstance(name, str)
                    or not name.strip()
            ):
                raise ValueError(
                    "Default header name must be "
                    "a non-empty string"
                )

            if not isinstance(value, str):
                raise TypeError(
                    "Default header value must be "
                    "a string"
                )

# from typing import Protocol, Sequence
# from collections.abc import Sequence
# Sequence 表示“只读序列”。
# 作用和 typing.Sequence 类似：表示“一个只读序列接口”。
# 区别是历史原因，from typing的早期python类型系统的常用写法。两个其实一样
# from typing import Any, Protocol
# Protocol 用来定义“接口形状”。

# from learn_hermes_agent.agent.messages import ChatMessage

# from learn_hermes_agent.providers.types import NormalizedResponse


# class ProviderTransport(Protocol):
#     @property
#     def model(self) -> str:
#         ...

    # messages的类型是Sequence[ChatMessage],指：messages可以是list[ChatMessage]，也可以是一个tuple[ChatMessage, ...]，但只能只读，不能改
    # def complete(
    #         self,
            # messages: Sequence[ChatMessage],
            # *,
            # tools: Sequence[dict[str, Any]] | None = None,
    # ) -> NormalizedResponse:
    #     ...

# ProviderTransport配合Protocol看，就是任何 provider 只要有：model: str 和 complete(messages) -> ChatMessage 就可以被当成 ProviderTransport 使用。它不要求显式继承。
