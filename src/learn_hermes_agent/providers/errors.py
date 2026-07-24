from __future__ import annotations

from enum import Enum


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
