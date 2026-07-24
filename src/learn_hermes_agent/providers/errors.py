from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from collections.abc import Iterator

_OVERLOADED_PATTERNS = (
    "overloaded",
    "at capacity",
    "over capacity",
)


# 采用与 Hermes 相同的 Enum 和小写成员名。暂时只定义当前可靠性链需要的分类，不复制其他 Provider 专有类型
class ProviderErrorKind(Enum):
    """Provider 请求失败的稳定分类。"""

    auth = "auth"
    billing = "billing"
    rate_limit = "rate_limit"
    upstream_rate_limit = "upstream_rate_limit"
    overloaded = "overloaded"
    server_error = "server_error"
    timeout = "timeout"
    ssl_cert_verification = "ssl_cert_verification"
    context_overflow = "context_overflow"
    model_not_found = "model_not_found"
    format_error = "format_error"
    unknown = "unknown"


# 继承 RuntimeError，现有 fallback 仍能捕获它
class ProviderRequestError(RuntimeError):
    """保留 Provider 请求失败的结构化信息。"""

    def __init__(
            self,
            message: str,
            *,
            status_code: int | None = None,
            response_headers: dict[str, str] | None = None,
            response_body: str | None = None,
    ) -> None:
        super().__init__(message)

        if (
                status_code is not None
                and (
                isinstance(status_code, bool)
                or not isinstance(status_code, int)
        )
        ):
            raise TypeError(
                "status_code must be an integer or None"
            )

        if (
                response_body is not None
                and not isinstance(response_body, str)
        ):
            raise TypeError(
                "response_body must be a string or None"
            )

        self.status_code = status_code
        # Header 名统一小写，因为 HTTP Header 不区分大小写
        self.response_headers = {
            str(name).lower(): str(value)
            for name, value in (
                    response_headers or {}
            ).items()
        }
        self.response_body = response_body


# 使用 frozen=True 是因为它是一份已经完成的分类结果，后续请求编排只能读取，不应再修改
@dataclass(frozen=True)
class ProviderErrorDecision:
    """错误分类器为请求编排层生成的处理决策。"""

    kind: ProviderErrorKind  # 错误属于哪一类
    retryable: bool  # 当前 binding 是否可以再次请求
    should_fallback: bool  # 重试耗尽或不应重试时，是否尝试下一个 binding
    should_rotate_credential: bool = False  # 为以后 CredentialPool 保留；当前不会真正换 Key
    status_code: int | None = None  # 保留 HTTP 状态码
    retry_after_seconds: float | None = None  # 服务端要求等待的秒数


def _iter_error_chain(
        error: BaseException,
) -> Iterator[BaseException]:
    """安全、有限地遍历异常及其原因链。"""
    current: BaseException | None = error
    visited: set[int] = set()

    for _ in range(5):
        if current is None:
            return

        identity = id(current)
        if identity in visited:
            return
        visited.add(identity)

        yield current

        current = (
                current.__cause__
                or current.__context__
        )


def _find_provider_request_error(
        error: BaseException,
) -> ProviderRequestError | None:
    """沿异常链查找结构化 Provider 错误。"""
    for current in _iter_error_chain(error):
        if isinstance(current, ProviderRequestError):
            return current

    return None


def _classification_text(
        error: BaseException,
        provider_error: ProviderRequestError | None,
) -> str:
    """汇总异常链和安全响应正文，供分类匹配。"""
    parts: list[str] = []

    for current in _iter_error_chain(error):
        message = str(current).strip()
        if message:
            parts.append(message)

    if (
            provider_error is not None
            and provider_error.response_body
    ):
        parts.append(provider_error.response_body)

    return " ".join(parts).casefold()


def classify_provider_error(
        error: BaseException,
) -> ProviderErrorDecision:
    """根据结构化状态码生成基础恢复决策。"""
    provider_error = _find_provider_request_error(
        error
    )
    status_code = (
        provider_error.status_code
        if provider_error is not None
        else None
    )

    error_text = _classification_text(
        error,
        provider_error,
    )

    def decision(
            kind: ProviderErrorKind,
            *,
            retryable: bool,
            should_fallback: bool = True,
            should_rotate_credential: bool = False,
    ) -> ProviderErrorDecision:
        return ProviderErrorDecision(
            kind=kind,
            retryable=retryable,
            should_fallback=should_fallback,
            should_rotate_credential=(
                should_rotate_credential
            ),
            status_code=status_code,
        )

    if status_code in {401, 403}:
        return decision(
            ProviderErrorKind.auth,
            retryable=False,
            should_rotate_credential=True,
        )

    if status_code == 402:
        return decision(
            ProviderErrorKind.billing,
            retryable=False,
            should_rotate_credential=True,
        )

    if status_code == 408:
        return decision(
            ProviderErrorKind.timeout,
            retryable=True,
        )

    if status_code == 429:
        if any(
                pattern in error_text
                for pattern in _OVERLOADED_PATTERNS
        ):
            return decision(
                ProviderErrorKind.overloaded,
                retryable=True,
            )

        return decision(
            ProviderErrorKind.rate_limit,
            retryable=True,
            should_rotate_credential=True,
        )

    if status_code in {503, 529}:
        return decision(
            ProviderErrorKind.overloaded,
            retryable=True,
        )

    if (
            status_code is not None
            and 500 <= status_code < 600
    ):
        return decision(
            ProviderErrorKind.server_error,
            retryable=True,
        )

    if (
            status_code is not None
            and 400 <= status_code < 500
    ):
        return decision(
            ProviderErrorKind.format_error,
            retryable=False,
        )

    if any(
            isinstance(
                current,
                (
                        TimeoutError,
                        ConnectionError,
                        OSError,
                ),
            )
            for current in _iter_error_chain(error)
    ):
        return decision(
            ProviderErrorKind.timeout,
            retryable=True,
        )

    return decision(
        ProviderErrorKind.unknown,
        retryable=False,
    )
