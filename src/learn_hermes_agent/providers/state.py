from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderBindingState:
    # 后续请求完全改走同步
    streaming_disabled: bool = False
    # 仍可 streaming，但不再发送 stream_options
    stream_options_disabled: bool = False
    # 连续 stale 次数
    consecutive_stale_streams: int = 0


__all__ = [
    "ProviderBindingState",
]
