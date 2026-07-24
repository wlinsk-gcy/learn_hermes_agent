from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """单个 Provider binding 的有界重试策略。"""
    # max_attempts=3 表示首次请求加最多两次重试
    # max_attempts=1 可以关闭重试
    max_attempts: int = 3
    backoff_base_seconds: float = 2.0
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
