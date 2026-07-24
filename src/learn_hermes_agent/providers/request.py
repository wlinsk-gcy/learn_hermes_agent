from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from learn_hermes_agent.providers.runtime import (
    ProviderBinding,
)
from learn_hermes_agent.providers.streaming import (
    ChatCompletionStreamAccumulator,
    ProviderStreamCallbacks,
    ProviderStreamError,
)

# 每次创建独立的流式 accumulator
def _request_provider_completion_once(
        binding: ProviderBinding,  # 要调用哪个 Provider，以及使用哪个 Client
        request_kwargs: dict[str, Any],  # Transport 已经构造好的 model/messages/tools 等参数
        *,
        callbacks: ProviderStreamCallbacks | None = None,  # 不需要实时增量，走同步请求
) -> object:
    """执行一次 Provider 请求。"""
    if callbacks is None:
        return binding.client.create(
            **request_kwargs
        )
    # 当前累加器只认识 OpenAI Chat Completions chunk 格式。未来 Anthropic 等协议不能误走这里
    if binding.runtime.api_mode != "chat_completions":
        raise ValueError(
            "Streaming is not implemented for api_mode "
            f"{binding.runtime.api_mode!r}"
        )
    # 先复制，避免修改 Transport 创建的原字典；然后要求 Provider 返回流
    stream_kwargs = dict(request_kwargs)
    stream_kwargs["stream"] = True
    # 读取调用方可能已经提供的 streaming 选项
    stream_options = stream_kwargs.get(
        "stream_options"
    )
    if stream_options is None:
        stream_options = {}
    elif not isinstance(stream_options, dict):
        raise ValueError(
            "stream_options must be a JSON object"
        )
    else:
        # 已提供字典：再次复制，避免修改嵌套原对象
        stream_options = dict(stream_options)
    # 要求 Provider 在结束 chunk 中返回 token usage
    stream_options["include_usage"] = True
    stream_kwargs["stream_options"] = stream_options

    raw_stream = binding.client.create(
        **stream_kwargs
    )

    if isinstance(
            raw_stream,
            (dict, str, bytes, bytearray),
    ):
        # 不是 chunk 流，但可迭代，拦截
        raise ProviderStreamError(
            "Provider streaming request returned "
            "a completed response instead of chunks"
        )
    # 拦截完全不可迭代的返回值
    if not isinstance(raw_stream, Iterable):
        raise ProviderStreamError(
            "Provider streaming request did not "
            "return an iterable"
        )

    # 创建本次请求独立的累加器，保存文本、reasoning、tool calls、usage 和 finish reason
    accumulator = ChatCompletionStreamAccumulator(
        callbacks
    )

    try:
        # 逐个读取 chunk 并累加，同时触发实时回调
        for chunk in raw_stream:
            accumulator.add_chunk(chunk)
        # 流结束后重建普通 Chat Completions 响应字典。因为它在 try 中，即使这里发现空流或缺少 finish reason，也会进入下面的异常处理
        return accumulator.build_response()
    except ProviderStreamError:
        # 累加器已经生成了包含 text_emitted 的准确错误，直接原样抛出
        raise

    except Exception as exc:
        #  把网络断开、generator 异常等统一包装，同时保留是否已向用户发送内容。from exc 保存原始异常链
        raise ProviderStreamError(
            f"Provider stream failed: {exc}",
            text_emitted=(
                accumulator.text_emitted
            ),
        ) from exc

    finally:
        # 安全获取可选的 close()；普通 list 等 Iterable 不一定有它
        close = getattr(raw_stream, "close", None)
        if callable(close):
            # 有 close() 就关闭流。关闭失败采用 best-effort，不能覆盖真正的 Provider 响应或原始错误
            try:
                close()
            except Exception:
                pass

# 公共函数以后负责 retry、sleep 和 attempt 预算
# 同步与流式不会各自复制一套重试循环
def request_provider_completion(
        binding: ProviderBinding,
        request_kwargs: dict[str, Any],
        *,
        callbacks: ProviderStreamCallbacks | None = None,
) -> object:
    """执行单个 binding 的 Provider 请求生命周期。"""
    return _request_provider_completion_once(
        binding,
        request_kwargs,
        callbacks=callbacks,
    )


# __all__ 表示该模块对外公开 request_provider_completion
__all__ = [
    "request_provider_completion",
]
