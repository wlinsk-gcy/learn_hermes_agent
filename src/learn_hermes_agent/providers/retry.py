from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class RetryPolicy:
    """单个 Provider binding 的有界重试策略。"""
    # max_attempts=3 表示首次请求加最多两次重试
    # max_attempts=1 可以关闭重试
    max_attempts: int = 3
    # 第一次重试前基础等待约 2 秒
    backoff_base_seconds: float = 2.0
    # 无论指数增长或 Retry-After 多大，最终最多等待 60 秒
    backoff_cap_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
                isinstance(self.max_attempts, bool)
                or not isinstance(self.max_attempts, int)
        ):
            raise TypeError(
                "max_attempts must be an integer"
            )

        if self.max_attempts < 1:
            raise ValueError(
                "max_attempts must be at least 1"
            )

        for name, value in (
                (
                        "backoff_base_seconds",
                        self.backoff_base_seconds,
                ),
                (
                        "backoff_cap_seconds",
                        self.backoff_cap_seconds,
                ),
        ):
            if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
            ):
                raise TypeError(
                    f"{name} must be a number"
                )

            if value < 0 or not math.isfinite(value):
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )


def parse_retry_after(
        value: str | None,  # 接收 Header 的原始字符串，也允许没有该 Header 时传入 None
        *,
        now: datetime | None = None,  # 计算 HTTP-date 距离当前时间还有多少秒。允许注入固定时间，方便验证
) -> float | None:  # 解析成功返回秒数，解析失败返回 None
    """将 Retry-After 的数字或 HTTP-date 形式解析为秒数。"""
    if not isinstance(value, str):
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    try:
        seconds = float(raw_value)
    except ValueError:
        pass
    else:
        # 拒绝不合法的等待时间, math.isfinite() 用来拒绝："nan" "inf" "-inf" 这些值虽然能被 float() 解析，但不能用于安全的等待计算。
        if seconds < 0 or not math.isfinite(seconds):
            return None
        return seconds

    try:
        # 运行到这里，说明输入不是数字。使用标准库解析 HTTP 日期："Wed, 21 Oct 2015 07:28:00 GMT" 得到一个 datetime 对象，表示服务端要求可以重新请求的绝对时间。
        retry_at = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        # - TypeError：输入类型不符合解析器要求。
        # - ValueError：日期格式不合法。
        # - OverflowError：日期数值超出系统可表示范围。
        return None

    # 检查解析出来的日期是否缺少时区， 某些旧式 HTTP 日期格式可能只包含日期和时间，不明确写出时区
    if retry_at.tzinfo is None:
        # 如果没有时区，就按照 HTTP 日期约定把它解释为 UTC。
        # 注意：replace() 不改变时分秒，只补充时区信息。
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    if now is None:
        reference_time = datetime.now(timezone.utc)  # 如果调用方没有提供 now，就获取当前 UTC 时间
    else:
        # now 是开发者注入的依赖，不是来自服务端的不可信 Header
        # 因此类型错误时直接抛出异常，而不是安静返回 None。
        if not isinstance(now, datetime):
            raise TypeError(
                "now must be a datetime or None"
            )
        reference_time = now
    # 检查基准时间是否缺少时区
    if reference_time.tzinfo is None:
        # 如果是 naive datetime，同样把它解释为 UTC，避免报错：can't subtract offset-naive and offset-aware datetimes
        reference_time = reference_time.replace(
            tzinfo=timezone.utc
        )
    # 计算目标时间与当前时间之差，然后转换成浮点秒数，确保结果不会小于零
    return max(
        0.0,
        (retry_at - reference_time).total_seconds(),
    )


def jittered_backoff(
        retry_number: int,
        *,
        policy: RetryPolicy,
        random_fn: Callable[[], float] = random.random,
) -> float:
    """
    计算带 jitter 且受 cap 限制的指数退避时间。
    例如等待：2 → 4 → 8 → 16
    """
    if (
            isinstance(retry_number, bool)
            or not isinstance(retry_number, int)
    ):
        raise TypeError(
            "retry_number must be an integer"
        )

    if retry_number < 1:
        raise ValueError(
            "retry_number must be at least 1"
        )

    if not isinstance(policy, RetryPolicy):
        raise TypeError(
            "policy must be a RetryPolicy"
        )

    if (
            policy.backoff_base_seconds == 0
            or policy.backoff_cap_seconds == 0
    ):
        return 0.0

    try:
        # math.ldexp(base, n) 等价于 base * 2**n，同时能更安全地处理极大的重试次数
        nominal_delay = math.ldexp(
            float(policy.backoff_base_seconds),
            retry_number - 1,
        )
    except OverflowError:
        nominal_delay = float(
            policy.backoff_cap_seconds
        )

    nominal_delay = min(
        nominal_delay,
        float(policy.backoff_cap_seconds),
    )

    random_value = random_fn()
    if (
            isinstance(random_value, bool)
            or not isinstance(random_value, (int, float))
    ):
        raise TypeError(
            "random_fn must return a number"
        )

    if (
            not math.isfinite(random_value)
            or not 0.0 <= random_value <= 1.0
    ):
        raise ValueError(
            "random_fn must return a value between 0 and 1"
        )

    jitter = nominal_delay * 0.5 * random_value

    return min(
        nominal_delay + jitter,
        float(policy.backoff_cap_seconds),
    )


def retry_delay(
        retry_number: int,
        *,
        policy: RetryPolicy,
        retry_after: str | None = None,
        random_fn: Callable[[], float] = random.random,
        now: datetime | None = None,
) -> float:
    """
    选择 Retry-After 或本地 jitter backoff。
    决策顺序：

    有效 Retry-After
    → 优先使用
    → 但不能超过 backoff_cap_seconds

    没有或无法解析 Retry-After
    → 使用本地 jittered_backoff

    此处故意不调用 sleep()，因为等待行为属于 request lifecycle。
    """
    if not isinstance(policy, RetryPolicy):
        raise TypeError(
            "policy must be a RetryPolicy"
        )

    retry_after_seconds = parse_retry_after(
        retry_after,
        now=now,
    )

    if retry_after_seconds is not None:
        return min(
            retry_after_seconds,
            float(policy.backoff_cap_seconds),
        )

    return jittered_backoff(
        retry_number,
        policy=policy,
        random_fn=random_fn,
    )
