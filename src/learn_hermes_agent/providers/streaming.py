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

    def _stream_error(
            self,
            message: str,
    ) -> ProviderStreamError:
        return ProviderStreamError(
            message,
            text_emitted=self._text_emitted,
        )
