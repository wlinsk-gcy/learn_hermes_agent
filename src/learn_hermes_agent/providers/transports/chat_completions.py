from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)
from learn_hermes_agent.providers.types import (
    NormalizedResponse,
    ToolCall,
)


class ChatCompletionsTransport(ProviderTransport):
    @property
    def api_mode(self) -> str:
        return "chat_completions"

    def convert_messages(
            self,
            messages: Sequence[ChatMessage],
    ) -> list[dict[str, Any]]:
        return [
            self._convert_message(message)
            for message in messages
        ]

    def build_kwargs(
            self,
            *,
            model: str,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        if not model:
            raise ValueError("Provider model must not be empty")

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }

        if tools:
            kwargs["tools"] = list(tools)

        return kwargs

    def validate_response(self, response: object) -> bool:
        return isinstance(response, dict)

    def normalize_response(
            self,
            response: object,
    ) -> NormalizedResponse:
        if not isinstance(response, dict):
            raise RuntimeError(
                "Provider response must be a JSON object"
            )

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "Provider response must include "
                "at least one choice"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(
                "Provider choice must be a JSON object"
            )

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(
                "Provider choice must include "
                "a message object"
            )

        content = message.get("content")
        if content is not None and not isinstance(
                content,
                str,
        ):
            raise RuntimeError(
                "Provider message content must be "
                "a string or null"
            )

        tool_calls = self._parse_tool_calls(
            message.get("tool_calls")
        )
        finish_reason = self.map_finish_reason(
            first_choice.get("finish_reason"),
            has_tool_calls=bool(tool_calls),
        )

        return NormalizedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._parse_usage(
                response.get("usage")
            ),
        )

    def _convert_message(
            self,
            message: ChatMessage,
    ) -> dict[str, Any]:
        role = message.get("role")
        if role not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ValueError(
                f"Unsupported message role: {role!r}"
            )

        payload: dict[str, Any] = {"role": role}

        if role == "assistant":
            payload["content"] = message.get("content")
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                payload["tool_calls"] = tool_calls
            return payload

        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id
            ):
                raise ValueError(
                    "Tool message must include tool_call_id"
                )

            payload["content"] = str(
                message.get("content") or ""
            )
            payload["tool_call_id"] = tool_call_id
            return payload

        payload["content"] = str(
            message.get("content") or ""
        )
        return payload

    def _parse_tool_calls(
            self,
            value: object,
    ) -> list[ToolCall] | None:
        """把 Provider 的原始 tool call 转成统一 ToolCall。如果某些兼容服务返回字典形式的 arguments，这里会将其转换成 JSON 字符串"""
        if value is None:
            return None

        if not isinstance(value, list):
            raise RuntimeError(
                "Provider message tool_calls must be a list"
            )

        result: list[ToolCall] = []

        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(
                    "Provider message tool_calls "
                    "must contain objects"
                )

            function = item.get("function")
            if not isinstance(function, dict):
                raise RuntimeError(
                    "Provider tool_call must include "
                    "a function object"
                )

            name = str(
                function.get("name") or ""
            ).strip()
            if not name:
                raise RuntimeError(
                    "Provider tool_call function "
                    "must include a name"
                )

            arguments = function.get("arguments", "{}")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(arguments)
            else:
                arguments = str(arguments or "{}")

            raw_id = item.get("id")
            tool_call_id = (
                str(raw_id)
                if raw_id
                else f"call_{index}"
            )

            result.append(
                ToolCall(
                    id=tool_call_id,
                    name=name,
                    arguments=arguments,
                )
            )

        return result
