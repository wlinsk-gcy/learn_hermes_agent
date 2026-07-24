from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

StreamCallback = Callable[[str], None]


class ProviderStreamError(RuntimeError):
    def __init__(
            self,
            message: str,
            *,
            text_emitted: bool = False,  # 决定失败后能否安全切换 fallback
    ) -> None:
        super().__init__(message)
        self.text_emitted = text_emitted


@dataclass(frozen=True)
class ProviderStreamCallbacks:
    """ProviderStreamCallbacks 统一三个流事件出口"""
    on_text_delta: StreamCallback | None = None
    on_reasoning_delta: StreamCallback | None = None
    on_tool_call_started: StreamCallback | None = None

    def emit_text_delta(self, text: str) -> bool:
        return self._emit(self.on_text_delta, text)

    def emit_reasoning_delta(self, text: str) -> bool:
        return self._emit(self.on_reasoning_delta, text)

    def emit_tool_call_started(
            self,
            tool_name: str,
    ) -> bool:
        return self._emit(
            self.on_tool_call_started,
            tool_name,
        )

    @staticmethod
    def _emit(
            callback: StreamCallback | None,
            value: str,
    ) -> bool:
        """_emit() 隔离 UI 回调错误，避免显示层异常破坏 Provider 请求"""
        if callback is None or not value:
            return False

        try:
            callback(value)
        except Exception:
            return False
        # 返回值表示回调是否成功收到事件
        return True


class ChatCompletionStreamAccumulator:
    """暂时只保存状态，不消费 chunk。_stream_error() 确保以后无论在哪一步失败，都携带“是否已向回调发送内容”的状态"""

    def __init__(
            self,
            callbacks: ProviderStreamCallbacks | None = None,
    ) -> None:
        self._callbacks = (
                callbacks
                or ProviderStreamCallbacks()
        )
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._finish_reason: str | None = None
        self._model: str | None = None
        self._role = "assistant"
        self._usage: dict[str, Any] | None = None
        self._saw_chunk = False
        self._text_emitted = False
        self._tool_calls: dict[
            int,
            dict[str, Any],
        ] = {}
        self._tool_call_started: set[int] = set()

    @property
    def content(self) -> str | None:
        content = "".join(self._content_parts)
        return content or None

    @property
    def reasoning(self) -> str | None:
        reasoning = "".join(
            self._reasoning_parts
        )
        return reasoning or None

    @property
    def usage(self) -> dict[str, Any] | None:
        if self._usage is None:
            return None
        return dict(self._usage)

    @property
    def finish_reason(self) -> str | None:
        return self._finish_reason

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def role(self) -> str:
        return self._role

    @property
    def saw_chunk(self) -> bool:
        return self._saw_chunk

    @property
    def text_emitted(self) -> bool:
        return self._text_emitted

    def add_chunk(self, chunk: object) -> None:
        if not isinstance(chunk, dict):
            raise self._stream_error(
                "Provider stream chunk must be a JSON object"
            )

        self._saw_chunk = True

        raw_model = chunk.get("model")
        if raw_model is not None:
            if not isinstance(raw_model, str):
                raise self._stream_error(
                    "Provider stream model must be a string"
                )
            self._model = raw_model

        raw_usage = chunk.get("usage")
        if raw_usage is not None:
            if not isinstance(raw_usage, dict):
                raise self._stream_error(
                    "Provider stream usage must be a JSON object"
                )
            self._usage = dict(raw_usage)

        choices = chunk.get("choices")
        if choices is None:
            if raw_usage is not None:
                return
            raise self._stream_error(
                "Provider stream chunk must include choices"
            )

        if not isinstance(choices, list):
            raise self._stream_error(
                "Provider stream choices must be a list"
            )

        # usage-only chunk 通常使用 choices=[]
        if not choices:
            return

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise self._stream_error(
                "Provider stream choice must be a JSON object"
            )

        finish_reason = first_choice.get(
            "finish_reason"
        )
        if finish_reason is not None:
            if not isinstance(finish_reason, str):
                raise self._stream_error(
                    "Provider stream finish_reason "
                    "must be a string or null"
                )
            self._finish_reason = finish_reason

        delta = first_choice.get("delta")
        if delta is None:
            return

        if not isinstance(delta, dict):
            raise self._stream_error(
                "Provider stream delta must be a JSON object"
            )

        raw_role = delta.get("role")
        if raw_role is not None:
            if (
                    not isinstance(raw_role, str)
                    or not raw_role
            ):
                raise self._stream_error(
                    "Provider stream role must be "
                    "a non-empty string"
                )
            self._role = raw_role

        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise self._stream_error(
                    "Provider stream content must be "
                    "a string or null"
                )

            if content:
                self._content_parts.append(content)
                if self._callbacks.emit_text_delta(
                        content
                ):
                    self._text_emitted = True

        reasoning = delta.get("reasoning_content")
        if reasoning is None:
            reasoning = delta.get("reasoning")

        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise self._stream_error(
                    "Provider stream reasoning must be "
                    "a string or null"
                )

            if reasoning:
                # reasoning 回调成功时也把 text_emitted 设为 True。
                # 因为 reasoning 也可能已经显示给用户，此后切换 fallback 同样会造成输出混杂
                self._reasoning_parts.append(reasoning)
                if self._callbacks.emit_reasoning_delta(
                        reasoning
                ):
                    self._text_emitted = True

        tool_calls = delta.get("tool_calls")
        if tool_calls is not None:
            self._add_tool_call_deltas(tool_calls)

    @property
    def tool_calls(self) -> list[dict[str, Any]] | None:
        if not self._tool_calls:
            return None

        result: list[dict[str, Any]] = []

        for index in sorted(self._tool_calls):
            entry = self._tool_calls[index]
            tool_call = {
                "id": entry["id"],
                "type": entry["type"],
                "function": dict(entry["function"]),
            }

            if entry.get("extra_content") is not None:
                tool_call["extra_content"] = entry[
                    "extra_content"
                ]

            result.append(tool_call)

        return result

    def _add_tool_call_deltas(
            self,
            value: object,
    ) -> None:
        if not isinstance(value, list):
            raise self._stream_error(
                "Provider stream tool_calls must be a list"
            )

        for item in value:
            if not isinstance(item, dict):
                raise self._stream_error(
                    "Provider stream tool_calls "
                    "must contain JSON objects"
                )

            raw_index = item.get("index", 0)
            if (
                    isinstance(raw_index, bool)
                    or not isinstance(raw_index, int)
                    or raw_index < 0
            ):
                raise self._stream_error(
                    "Provider stream tool_call index "
                    "must be a non-negative integer"
                )

            entry = self._tool_calls.setdefault(
                raw_index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                    "extra_content": None,
                },
            )

            raw_id = item.get("id")
            if raw_id is not None:
                if (
                        isinstance(raw_id, bool)
                        or not isinstance(raw_id, (str, int))
                ):
                    raise self._stream_error(
                        "Provider stream tool_call id "
                        "must be a string or integer"
                    )

                tool_call_id = str(raw_id)
                if tool_call_id:
                    entry["id"] = tool_call_id

            raw_type = item.get("type")
            if raw_type is not None:
                if not isinstance(raw_type, str):
                    raise self._stream_error(
                        "Provider stream tool_call type "
                        "must be a string"
                    )
                if raw_type:
                    entry["type"] = raw_type

            function = item.get("function")
            if function is not None:
                if not isinstance(function, dict):
                    raise self._stream_error(
                        "Provider stream tool_call function "
                        "must be a JSON object"
                    )

                name = function.get("name")
                if name is not None:
                    if not isinstance(name, str):
                        raise self._stream_error(
                            "Provider stream tool name "
                            "must be a string"
                        )

                    if name:
                        # 工具名称是完整标识符，不能使用 +=
                        # 因为名称通常由 Provider 重复发送完整值
                        entry["function"]["name"] = name

                        if raw_index not in self._tool_call_started:
                            self._tool_call_started.add(
                                raw_index
                            )
                            self._callbacks.emit_tool_call_started(
                                name
                            )

                arguments = function.get("arguments")
                if arguments is not None:
                    if not isinstance(arguments, str):
                        raise self._stream_error(
                            "Provider stream tool arguments "
                            "must be a string"
                        )
                    # 参数才是真正需要跨 chunk 拼接的字符串
                    entry["function"][
                        "arguments"
                    ] += arguments

            if item.get("extra_content") is not None:
                entry["extra_content"] = item[
                    "extra_content"
                ]

    def build_response(self) -> dict[str, Any]:
        """
        不会返回 NormalizedResponse，而是重建普通 Chat Completions 原始字典.
        stream chunks
            → accumulator
            → 完整原始 response
            → ChatCompletionsTransport
            → NormalizedResponse

        空流与缺少 finish_reason 的残缺流会直接报错，避免把截断内容误认为完整回答
        """
        if not self._saw_chunk:
            raise self._stream_error(
                "Provider returned an empty stream"
            )

        if self._finish_reason is None:
            raise self._stream_error(
                "Provider stream ended without "
                "a finish_reason"
            )

        message: dict[str, Any] = {
            "role": self._role,
            "content": self.content,
        }

        tool_calls = self.tool_calls
        if tool_calls:
            message["tool_calls"] = tool_calls

        if self.reasoning is not None:
            message["reasoning_content"] = (
                self.reasoning
            )

        response: dict[str, Any] = {
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": (
                        self._finish_reason
                    ),
                }
            ]
        }

        if self._model is not None:
            response["model"] = self._model

        if self._usage is not None:
            response["usage"] = dict(
                self._usage
            )

        return response

    def _stream_error(
            self,
            message: str,
    ) -> ProviderStreamError:
        return ProviderStreamError(
            message,
            text_emitted=self._text_emitted,
        )
