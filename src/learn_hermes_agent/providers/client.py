from __future__ import annotations

from typing import Any, Protocol

"""
定义“原始请求客户端”的最小接口。
它只接收 Transport 构造的请求参数并返回原始响应，不接触 ChatMessage 或 NormalizedResponse。
"""
class ProviderClient(Protocol):
    def create(self, **request_kwargs: Any) -> object:
        ...
