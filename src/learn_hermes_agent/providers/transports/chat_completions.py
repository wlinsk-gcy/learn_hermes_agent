from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
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
