from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall


class FakeProviderClient:
    def __init__(
            self,
            model: str = "fake-basic",
            *,
            scripted_responses: Sequence[object] | None = None,
    ) -> None:
        self._model = model
        self._scripted_responses = list(
            scripted_responses or []
        )
        self._script_index = 0

    def create(self, **request_kwargs: Any) -> object:
        """不再直接返回 NormalizedResponse，而是模拟 Chat Completions 的原始 JSON。后续 fake 与真实 HTTP 响应都会经过相同 Transport"""
        if self._script_index < len(
                self._scripted_responses
        ):
            response = self._scripted_responses[
                self._script_index
            ]
            self._script_index += 1
            return response

        last_user_content = ""
        messages = request_kwargs.get("messages")

        if isinstance(messages, list):
            for message in reversed(messages):
                if (
                        isinstance(message, dict)
                        and message.get("role") == "user"
                ):
                    last_user_content = str(
                        message.get("content") or ""
                    )
                    break

        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            f"{self._model} received: "
                            f"{last_user_content}"
                        ),
                    },
                    "finish_reason": "stop",
                }
            ]
        }



class FakeProviderTransport:
    def __init__(
            self,
            model: str = "fake-basic",
            *,
            scripted_responses: Sequence[NormalizedResponse] | None = None,
    ) -> None:
        self._model = model
        self._scripted_responses = list(scripted_responses or [])
        self._script_index = 0

    @property
    def model(self) -> str:
        return self._model

    def complete(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[dict[str, Any]] | None = None,
    ) -> NormalizedResponse:
        if self._script_index < len(self._scripted_responses):
            response = self._scripted_responses[self._script_index]
            self._script_index += 1
            return response

        last_user_content = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user_content = str(message.get("content") or "")
                break

        return NormalizedResponse(
            content=f"{self.model} received: {last_user_content}",
            tool_calls=None,
            finish_reason="stop",
        )


def tool_demo_provider(model: str = "fake-basic") -> FakeProviderTransport:
    return FakeProviderTransport(
        model=model,
        scripted_responses=[
            NormalizedResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_echo_1",
                        name="echo",
                        arguments='{"text":"hello from tool"}',
                    )
                ],
                finish_reason="tool_calls",
            ),
            NormalizedResponse(
                content="final answer after echo tool",
                tool_calls=None,
                finish_reason="stop",
            ),
        ],
    )
