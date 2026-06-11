from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib import error, request

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall, Usage


class OpenAICompatibleProviderTransport:
    def __init__(
            self,
            *,
            model: str,
            base_url: str,
            api_key: str,
            timeout_seconds: float = 60.0,
    ) -> None:
        if not model:
            raise ValueError("model must not be empty")
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not api_key:
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        return self._model

    def complete(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[dict[str, Any]] | None = None,
    ) -> NormalizedResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._serialize_message(message) for message in messages],
        }
        if tools:
            payload["tools"] = list(tools)

        body = json.dumps(payload).encode("utf-8")

        http_request = request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            # with ... as response 的好处是：请求结束后会自动关闭连接资源
            with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                # response.read() 拿到的是 bytes
                # .decode("utf-8") 把 bytes 转成字符串
                response_body = response.read().decode("utf-8")
        # 捕获 HTTP 状态码错误，比如：404这种，urllib 遇到这些状态码时会抛 HTTPError。
        except error.HTTPError as exc:
            # 读取错误响应的正文。
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Provider request failed with HTTP {exc.code}: {self._shorten(error_body)}"
            ) from exc
        # 捕获网络层错误，比如：域名解析失败，连接被拒绝，base_url 写错等等
        except error.URLError as exc:
            raise RuntimeError(f"Provider request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Provider request timed out") from exc

        try:
            response_payload = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Provider returned invalid JSON") from exc

        return self._parse_response(response_payload)

    def _serialize_message(self, message: ChatMessage) -> dict[str, Any]:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported message role: {role!r}")

        payload: dict[str, Any] = {"role": role}

        if role == "assistant":
            payload["content"] = message.get("content")
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                payload["tool_calls"] = tool_calls
            return payload

        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ValueError("Tool message must include tool_call_id")

            payload["content"] = str(message.get("content") or "")
            payload["tool_call_id"] = tool_call_id
            # 这里不把本地的name发给provider，只发content和tool_call_id。
            # 为了贴近 Chat Completions 的 tool message 结构，避免某些 OpenAI-compatible 服务因为多余字段拒绝请求。
            return payload

        payload["content"] = str(message.get("content") or "")
        return payload

    def _parse_response(self, payload: object) -> NormalizedResponse:
        if not isinstance(payload, dict):
            raise RuntimeError("Provider response must be a JSON object")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Provider response must include at least one choice")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError("Provider choice must be a JSON object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Provider choice must include a message object")

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise RuntimeError("Provider message content must be a string or null")

        tool_calls = self._parse_tool_calls(message.get("tool_calls"))

        finish_reason = first_choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            finish_reason = "tool_calls" if tool_calls else "stop"

        return NormalizedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._parse_usage(payload.get("usage")),
        )

    def _parse_tool_calls(self, value: object) -> list[ToolCall] | None:
        if value is None:
            return None

        if not isinstance(value, list):
            raise RuntimeError("Provider message tool_calls must be a list")

        result: list[ToolCall] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                raise RuntimeError("Provider message tool_calls must contain objects")

            function = item.get("function")
            if not isinstance(function, dict):
                raise RuntimeError("Provider tool_call must include a function object")

            name = str(function.get("name") or "").strip()
            if not name:
                raise RuntimeError("Provider tool_call function must include a name")

            arguments = function.get("arguments", "{}")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(arguments)
            else:
                arguments = str(arguments or "{}")

            raw_id = item.get("id")
            tool_call_id = str(raw_id) if raw_id else f"call_{index}"

            result.append(ToolCall(id=tool_call_id, name=name, arguments=arguments))

        return result

    def _parse_usage(self, usage: object) -> Usage | None:
        if not isinstance(usage, dict):
            return None

        return Usage(
            prompt_tokens=self._get_non_negative_int(usage.get("prompt_tokens")),
            completion_tokens=self._get_non_negative_int(usage.get("completion_tokens")),
            total_tokens=self._get_non_negative_int(usage.get("total_tokens")),
        )

    def _get_non_negative_int(self, value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
        return 0

    def _shorten(self, text: str, *, max_length: int = 500) -> str:
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}..."
