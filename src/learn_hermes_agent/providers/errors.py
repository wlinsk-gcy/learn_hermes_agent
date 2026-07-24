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
