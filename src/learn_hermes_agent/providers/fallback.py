from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.base import ProviderTransport
from learn_hermes_agent.providers.types import NormalizedResponse


class FallbackProviderTransport:
    def __init__(self, providers: Sequence[ProviderTransport]) -> None:
        if not providers:
            raise ValueError("FallbackProviderTransport requires at least one provider")

        self._providers = list(providers)
        # 最近一次调用成功的供应商
        self.last_provider_model: str | None = None
        # fallback时如果model是一样的，会区分不出来，所以用index区分
        self.last_provider_index: int | None = None
        # 最近一次调用失败的异常信息
        self.last_error: str | None = None

    @property
    def model(self) -> str:
        return self._providers[0].model

    def complete(self, messages: Sequence[ChatMessage], *, tools: Sequence[dict[str, Any]] | None = None) -> NormalizedResponse:
        errors: list[str] = []
        # 清空上一轮的状态：
        self.last_provider_model = None
        self.last_provider_index = None
        self.last_error = None

        for index, provider in enumerate(self._providers):
            try:
                response = provider.complete(messages, tools=tools)
            except (RuntimeError, ValueError) as exc:
                error_text = f"{provider.model}: {exc}"
                errors.append(error_text)
                self.last_error = error_text
                continue

            self.last_provider_model = provider.model
            self.last_provider_index = index
            self.last_error = None
            return response

        joined_errors = "; ".join(errors)
        raise RuntimeError(f"All provider fallbacks failed: {joined_errors}")
