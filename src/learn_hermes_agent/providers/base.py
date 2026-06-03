from __future__ import annotations

# from typing import Protocol, Sequence
from collections.abc import Sequence
# Sequence 表示“只读序列”。
# 作用和 typing.Sequence 类似：表示“一个只读序列接口”。
# 区别是历史原因，from typing的早期python类型系统的常用写法。两个其实一样
from typing import Protocol
# Protocol 用来定义“接口形状”。


from learn_hermes_agent.agent.messages import ChatMessage


class ProviderTransport(Protocol):
    @property
    def model(self) -> str:
        ...

    # messages的类型是Sequence[ChatMessage],指：messages可以是list[ChatMessage]，也可以是一个tuple[ChatMessage, ...]，但只能只读，不能改
    def complete(self, messages: Sequence[ChatMessage]) -> ChatMessage:
        ...

# ProviderTransport配合Protocol看，就是任何 provider 只要有：model: str 和 complete(messages) -> ChatMessage 就可以被当成 ProviderTransport 使用。它不要求显式继承。
