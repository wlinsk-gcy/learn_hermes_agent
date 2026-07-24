from __future__ import annotations

from collections.abc import Sequence
from typing import Any


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

    def create(
            self,
            **request_kwargs: Any,
    ) -> object:
        return self._next_response(
            request_kwargs
        )

    def _next_response(
            self,
            request_kwargs: dict[str, Any],
    ) -> object:
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


def tool_demo_client(
        model: str = "fake-basic",
) -> FakeProviderClient:
    """
    依次模拟：
    1. Assistant 请求调用 echo。
    2. 工具执行后返回最终回答。
    """
    return FakeProviderClient(
        model=model,
        scripted_responses=[
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_echo_1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": (
                                            '{"text":'
                                            '"hello from tool"}'
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "final answer after echo tool"
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ],
    )
