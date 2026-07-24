from __future__ import annotations

# ABC 是 Abstract Base Class（抽象基类） 配合 @abstractmethod 使用，规定子类必须实现哪些方法
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.types import NormalizedResponse

# 定义接口标准
class ProviderTransport(ABC):
    @property
    @abstractmethod
    def api_mode(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def convert_messages(
            self,
            messages: Sequence[ChatMessage],
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def convert_tools(
            self,
            tools: Sequence[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [dict(tool) for tool in tools]

    @abstractmethod
    def build_kwargs(
            self,
            *,
            model: str,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def validate_response(self, response: object) -> bool:
        return response is not None

    @abstractmethod
    def normalize_response(
            self,
            response: object,
    ) -> NormalizedResponse:
        raise NotImplementedError

    def map_finish_reason(
            self,
            finish_reason: object,
            *,
            has_tool_calls: bool,
    ) -> str:
        if isinstance(finish_reason, str) and finish_reason:
            return finish_reason
        return "tool_calls" if has_tool_calls else "stop"
