from __future__ import annotations

from collections.abc import Iterator, Sequence
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
        response = self._next_response(
            request_kwargs
        )

        if request_kwargs.get("stream") is True:
            return self._stream_response(response)

        return response

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

    def _stream_response(
            self,
            response: object,
    ) -> Iterator[object]:
        """Fake 的文本会拆成两个 chunk；tool calls 使用标准 delta 形状；最后单独发送 finish reason，usage 则使用空 choices chunk。"""
        if not isinstance(response, dict):
            raise RuntimeError(
                "Fake streaming response must be "
                "a JSON object"
            )

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "Fake streaming response must "
                "include a choice"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(
                "Fake streaming choice must be "
                "a JSON object"
            )

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(
                "Fake streaming choice must "
                "include a message"
            )

        model = str(
            response.get("model")
            or self._model
        )
        role = str(
            message.get("role")
            or "assistant"
        )

        def make_chunk(
                delta: dict[str, Any],
                finish_reason: object = None,
        ) -> dict[str, Any]:
            return {
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish_reason,
                    }
                ],
            }

        emitted_delta = False

        reasoning = message.get(
            "reasoning_content"
        )
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise RuntimeError(
                    "Fake reasoning must be a string"
                )

            if reasoning:
                yield make_chunk(
                    {
                        "role": role,
                        "reasoning_content": reasoning,
                    }
                )
                emitted_delta = True

        content = message.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise RuntimeError(
                    "Fake content must be a string or null"
                )

            if content:
                midpoint = max(1, len(content) // 2)

                for part in (
                        content[:midpoint],
                        content[midpoint:],
                ):
                    if not part:
                        continue

                    delta: dict[str, Any] = {
                        "content": part,
                    }
                    if not emitted_delta:
                        delta["role"] = role

                    yield make_chunk(delta)
                    emitted_delta = True

        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls is not None:
            if not isinstance(raw_tool_calls, list):
                raise RuntimeError(
                    "Fake tool_calls must be a list"
                )

            tool_call_deltas: list[
                dict[str, Any]
            ] = []

            for index, tool_call in enumerate(
                    raw_tool_calls
            ):
                if not isinstance(tool_call, dict):
                    raise RuntimeError(
                        "Fake tool_call must be "
                        "a JSON object"
                    )

                function = tool_call.get("function")
                if not isinstance(function, dict):
                    raise RuntimeError(
                        "Fake tool_call must include "
                        "a function"
                    )

                tool_call_delta: dict[str, Any] = {
                    "index": index,
                    "id": tool_call.get("id"),
                    "type": (
                            tool_call.get("type")
                            or "function"
                    ),
                    "function": dict(function),
                }

                if tool_call.get(
                        "extra_content"
                ) is not None:
                    tool_call_delta["extra_content"] = (
                        tool_call["extra_content"]
                    )

                tool_call_deltas.append(
                    tool_call_delta
                )

            if tool_call_deltas:
                delta = {
                    "tool_calls": tool_call_deltas,
                }
                if not emitted_delta:
                    delta["role"] = role

                yield make_chunk(delta)
                emitted_delta = True

        if not emitted_delta:
            yield make_chunk({"role": role})

        yield make_chunk(
            {},
            first_choice.get("finish_reason"),
        )

        usage = response.get("usage")
        if usage is not None:
            yield {
                "model": model,
                "choices": [],
                "usage": usage,
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
