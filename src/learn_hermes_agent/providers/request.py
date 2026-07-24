from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from learn_hermes_agent.providers.runtime import (
    ProviderBinding,
)
from learn_hermes_agent.providers.streaming import (
    ChatCompletionStreamAccumulator,
    ProviderStreamCallbacks,
    ProviderStreamError,
)
from learn_hermes_agent.providers.errors import (
    ProviderRequestError,
    classify_provider_error,
)
from learn_hermes_agent.providers.retry import (
    RetryPolicy,
    retry_delay,
)
from learn_hermes_agent.providers.state import (
    ProviderBindingState,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 每次创建独立的流式 accumulator
def _request_provider_completion_once(
        binding: ProviderBinding,  # 要调用哪个 Provider，以及使用哪个 Client
        request_kwargs: dict[str, Any],  # Transport 已经构造好的 model/messages/tools 等参数
        *,
        callbacks: ProviderStreamCallbacks | None = None,  # 不需要实时增量，走同步请求
        stream_options_disabled: bool = False,
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
    if stream_options_disabled:
        stream_kwargs.pop(
            "stream_options",
            None,
        )
    else:
        stream_options = stream_kwargs.get(
            "stream_options"
        )
        if stream_options is None:
            stream_options = {}
        elif not isinstance(
                stream_options,
                dict,
        ):
            raise ValueError(
                "stream_options must be a JSON object"
            )
        else:
            stream_options = dict(
                stream_options
            )

        stream_options["include_usage"] = True
        stream_kwargs["stream_options"] = (
            stream_options
        )

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

    except ProviderRequestError as exc:
        if not accumulator.text_emitted:
            # 用户尚未看到输出，保留结构化错误，
            # 交给外层 lifecycle 判断是否重试。
            raise

        # 已经产生可见输出，不能重放完整请求。
        raise ProviderStreamError(
            f"Provider stream failed after output: {exc}",
            text_emitted=True,
        ) from exc

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
        state: ProviderBindingState,
        callbacks: ProviderStreamCallbacks | None = None,
        retry_policy: RetryPolicy | None = None,  # 可选重试策略
        sleep_fn: Callable[[float], None] = time.sleep,  # 执行计算出的等待时间的函数
        random_fn: Callable[[], float] = random.random,  # jitter 使用的随机数来源
        now_fn: Callable[[], datetime] = _utc_now,  # 获取当前 UTC 时间的函数，用于解析 HTTP-date
) -> object:
    """执行单个 binding 的 Provider 请求生命周期。"""
    if not isinstance(state, ProviderBindingState):
        raise TypeError(
            "state must be a ProviderBindingState"
        )
    effective_request_kwargs = request_kwargs
    effective_callbacks = callbacks

    if state.streaming_disabled:
        effective_request_kwargs = dict(
            request_kwargs
        )
        effective_request_kwargs.pop(
            "stream",
            None,
        )
        effective_request_kwargs.pop(
            "stream_options",
            None,
        )
        effective_callbacks = None
    if retry_policy is None:
        policy = RetryPolicy()
    elif not isinstance(retry_policy, RetryPolicy):
        raise TypeError(
            "retry_policy must be a RetryPolicy or None"
        )
    else:
        policy = retry_policy

    # 执行有界请求循环，默认执行1次重试2次
    for attempt_number in range(
            1,
            policy.max_attempts + 1,
    ):
        try:
            return _request_provider_completion_once(
                binding,
                effective_request_kwargs,
                callbacks=effective_callbacks,
                stream_options_disabled=(
                    state.stream_options_disabled
                ),
            )
        # 只捕获结构化 Provider 错误
        except ProviderRequestError as exc:
            # 把错误交给分类器，得到处理决策
            decision = classify_provider_error(
                exc,
                provider=binding.runtime.provider,  # 传入 Provider 名称是为了正确识别 OpenRouter 上游 429
            )

            """
            满足以下任意条件就停止当前 binding 的重试
            1. 错误本身不可重试；
            2. 已经用完最大请求次数。
            例如：
            401 auth                 → retryable=False，立即停止
            OpenRouter upstream 429  → retryable=False，立即停止
            503 overloaded           → retryable=True，可以继续
            第 3 次请求仍然失败       → 已达到 max_attempts，停止
            """
            if (
                    not decision.retryable
                    or attempt_number
                    >= policy.max_attempts
            ):
                raise
            # 从结构化错误中读取服务端的 Retry-After Header
            retry_after = exc.response_headers.get(
                "retry-after"
            )
            # 只有存在 Retry-After 时才获取当前时间，因为 HTTP-date 解析可能需要它
            now = (
                now_fn()
                if retry_after is not None
                else None
            )
            # 计算最终应该等待多少秒
            delay = retry_delay(
                attempt_number,  # 当前失败的 attempt 编号, 首次请求失败时是 1，因此本地退避基础值约为 2 秒
                policy=policy,  # 基础等待、最大上限等策略
                retry_after=retry_after,  # 把服务端 Header 交给延迟选择器, 有效时优先使用；无效或缺失时使用本地指数退避
                random_fn=random_fn,  # 把可注入的随机源继续传给 jitter 计算, 如果使用了有效的 Retry-After，随机函数不会被调用
                now=now,  # 传入 HTTP-date 计算使用的基准时间
            )
            sleep_fn(delay)

    raise AssertionError(
        "Provider retry lifecycle exhausted "
        "without a result or error"
    )


# __all__ 表示该模块对外公开 request_provider_completion
__all__ = [
    "request_provider_completion",
]
