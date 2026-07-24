from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass

StreamCallback = Callable[[str], None]


class ProviderStreamError(RuntimeError):
    def __init__(
            self,
            message: str,
            *,
            text_emitted: bool = False, # 决定失败后能否安全切换 fallback
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
