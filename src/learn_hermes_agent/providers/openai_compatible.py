from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any
from urllib import error, request

from learn_hermes_agent.providers.errors import (
    ProviderRequestError,
)

# 不保存全部 Header，只保留 retry-after 和 x-ratelimit-*，避免携带 Cookie 或认证信息
_ALLOWED_HTTP_ERROR_HEADERS = frozenset({
    "retry-after",
})
_ALLOWED_HTTP_ERROR_HEADER_PREFIXES = (
    "x-ratelimit-",
)

_MAX_HTTP_ERROR_BODY_CHARS = 500
_REDACTION_MARKER = "[REDACTED]"


class OpenAICompatibleClient:
    def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            timeout_seconds: float = 60.0,
    ) -> None:
        """新 Client 只保存执行 HTTP 请求需要的信息，不保存 model。模型会由 Transport 放进每次请求参数"""
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not api_key:
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        self._url = (
            f"{base_url.rstrip('/')}/chat/completions"
        )
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def create(
            self,
            **request_kwargs: Any,
    ) -> object:
        http_request = self._build_http_request(
            request_kwargs
        )
        # 这里使用 is True，只有明确传入布尔值 True 才启用流式模式，避免 "true"、1 等意外值改变请求行为
        if request_kwargs.get("stream") is True:
            return self._iter_stream_response(
                http_request
            )

        return self._read_json_response(
            http_request
        )

    def _build_http_request(
            self,
            request_kwargs: dict[str, Any],
    ) -> request.Request:
        body = json.dumps(
            request_kwargs
        ).encode("utf-8")

        headers = {
            "Authorization": (
                f"Bearer {self._api_key}"
            ),
            "Content-Type": "application/json",
        }
        # 这里使用 is True，只有明确传入布尔值 True 才启用流式模式，避免 "true"、1 等意外值改变请求行为
        if request_kwargs.get("stream") is True:
            headers["Accept"] = "text/event-stream"

        return request.Request(
            self._url,
            data=body,
            method="POST",
            headers=headers,
        )

    @staticmethod
    def _filter_http_error_headers(
            exc: error.HTTPError,
    ) -> dict[str, str]:
        if exc.headers is None:
            return {}

        selected_headers: dict[str, str] = {}

        for name, value in exc.headers.items():
            normalized_name = str(name).lower()
            allowed = (
                    normalized_name
                    in _ALLOWED_HTTP_ERROR_HEADERS
                    or normalized_name.startswith(
                        _ALLOWED_HTTP_ERROR_HEADER_PREFIXES
                    )
            )
            if allowed:
                selected_headers[normalized_name] = (
                    str(value)
                )

        return selected_headers

    @staticmethod
    def _read_http_error_body(
            exc: error.HTTPError,
    ) -> str:
        """读取并关闭 urllib 的 HTTP 错误响应。"""
        try:
            # read() 拿到的是 bytes
            # .decode("utf-8") 把 bytes 转成字符串
            return exc.read().decode(
                "utf-8",
                errors="replace",
            )
        finally:
            exc.close()

    def _sanitize_http_error_body(
            self,
            text: str,
    ) -> str:
        sanitized = text
        secret = self._api_key
        # 先脱敏、后截断，避免截断恰好切开密钥而泄露前缀
        if secret:
            replacement = (
                _REDACTION_MARKER
                if len(secret)
                   >= len(_REDACTION_MARKER)
                else "*" * len(secret) # 短密钥使用等长 *，脱敏不会扩大正文
            )
            sanitized = sanitized.replace(
                secret,
                replacement,
            )

        if (
                len(sanitized)
                <= _MAX_HTTP_ERROR_BODY_CHARS
        ):
            return sanitized

        return (
                sanitized[
                    :_MAX_HTTP_ERROR_BODY_CHARS - 3
                ]
                + "..."
        )

    def _provider_error_from_http_error(
            self,
            exc: error.HTTPError,
    ) -> ProviderRequestError:
        status_code = exc.code
        response_headers = (
            self._filter_http_error_headers(exc)
        )
        response_body = (
            self._sanitize_http_error_body(
                self._read_http_error_body(exc)
            )
        )

        return ProviderRequestError(
            "Provider request failed with HTTP "
            f"{status_code}",
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
        )

    def _read_json_response(
            self,
            http_request: request.Request,
    ) -> object:
        try:
            # with ... as response 的好处是：请求结束后会自动关闭连接资源
            with request.urlopen(
                    http_request,
                    timeout=self._timeout_seconds,
            ) as response:
                # response.read() 拿到的是 bytes
                # .decode("utf-8") 把 bytes 转成字符串
                response_body = response.read().decode(
                    "utf-8"
                )
        # 捕获 HTTP 状态码错误，比如：404这种，urllib 遇到这些状态码时会抛 HTTPError。
        except error.HTTPError as exc:
            raise (
                self._provider_error_from_http_error(
                    exc
                )
            ) from exc
        # 捕获网络层错误，比如：域名解析失败，连接被拒绝，base_url 写错等等
        except error.URLError as exc:
            raise ProviderRequestError(
                "Provider request failed"
            ) from exc
        except TimeoutError as exc:
            raise ProviderRequestError(
                "Provider request timed out"
            ) from exc
        except ConnectionError as exc:
            raise ProviderRequestError(
                "Provider connection failed"
            ) from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Provider returned invalid JSON"
            ) from exc

    def _iter_stream_response(
            self,
            http_request: request.Request,
    ) -> Iterator[dict[str, Any]]:
        try:
            # 因为函数包含 yield，调用它时不会立即请求网络，开始迭代时才会执行 urlopen()
            # with 保证流结束、报错或生成器被关闭时，HTTP response 都会关闭。
            with request.urlopen(
                    http_request,
                    timeout=self._timeout_seconds,
            ) as response:
                # yield from 逐个转交 _iter_sse_events() 产生的 chunk
                # SSE JSON 解析错误会自然向上传递。
                yield from self._iter_sse_events(
                    response
                )
        # HTTP、网络和超时错误继续统一转换成 RuntimeError。
        except error.HTTPError as exc:
            raise (
                self._provider_error_from_http_error(
                    exc
                )
            ) from exc
        except error.URLError as exc:
            raise ProviderRequestError(
                "Provider request failed"
            ) from exc
        except TimeoutError as exc:
            raise ProviderRequestError(
                "Provider request timed out"
            ) from exc
        except ConnectionError as exc:
            raise ProviderRequestError(
                "Provider connection failed"
            ) from exc

    @staticmethod
    def _iter_sse_events(
            lines: Iterable[bytes],
    ) -> Iterator[dict[str, Any]]:
        """
        SSE 的基本格式是：
        data: {"choices":[...]}

        data: {"choices":[...]}

        data: [DONE]

        空行用于分隔 event，[DONE] 表示流正常结束。
        """
        data_lines: list[str] = []

        for raw_line in lines:
            if not isinstance(
                    raw_line,
                    (bytes, bytearray),
            ):
                raise RuntimeError(
                    "Provider SSE response lines "
                    "must be bytes"
                )

            line = bytes(raw_line).decode(
                "utf-8"
            ).rstrip("\r\n")

            # 遇到了空行。SSE 使用空行表示“一个 event 已结束”。
            if not line:
                if not data_lines:
                    # 如果前面没有收集到任何 data:，这个空行没有意义，直接读取下一行。
                    continue
                # 一个 SSE event 可以包含多个 data: 行，因此把它们拼起来。
                data = "\n".join(data_lines)
                # 清空缓冲区，为下一个 event 做准备。
                data_lines.clear()
                # 把字符串解析成 Python 字典：'{"choices": [...]}' -> {"choices": [...]}
                event = (
                    OpenAICompatibleClient
                    ._decode_sse_data(data)
                )
                if event is None:
                    # 如果读到： data: [DONE] 解析结果就是 None。generator 中的 return 表示整个流结束，不再产生数据
                    return
                """
                例如：
                events = OpenAICompatibleClient._iter_sse_events(lines)
                此时通常还没有处理所有数据。调用：
                first_event = next(events)
                函数开始读取 SSE，执行到：
                yield event
                把当前 event 返回给调用者，然后暂停在这里。
                再次调用：
                second_event = next(events)
                函数从 yield event 后面继续执行，也就是执行：
                continue
                然后继续读取下一行 SSE。
                """
                # yield 的作用是：产出一个结果，同时暂停函数，等待下一次继续执行。
                # 只要函数中出现 yield，它就不再是普通函数，而是 generator（生成器）
                """
                把这个 event 立即交给外面的消费者，例如：
                for chunk in events:
                    accumulator.add_chunk(chunk)
                然后暂停，不会继续读取后面的网络数据。
                """
                yield event
                # 当外部再次请求下一个 chunk 时，从这里恢复，进入下一轮循环。
                continue

            # 以冒号开头的是 SSE comment/keep-alive
            if line.startswith(":"):
                continue

            # event、id、retry 等字段当前不参与
            if not line.startswith("data:"):
                continue

            data_line = line[5:]
            if data_line.startswith(" "):
                data_line = data_line[1:]

            data_lines.append(data_line)

        # 某些兼容端点在 EOF 前不发送最后一个空行
        if data_lines:
            data = "\n".join(data_lines)
            event = (
                OpenAICompatibleClient
                ._decode_sse_data(data)
            )
            if event is not None:
                yield event

    @staticmethod
    def _decode_sse_data(
            data: str,
    ) -> dict[str, Any] | None:
        """解码成：{"choices":[...]}"""
        if data.strip() == "[DONE]":
            return None

        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Provider stream returned "
                "an invalid JSON event"
            ) from exc

        if not isinstance(event, dict):
            raise RuntimeError(
                "Provider stream event must be "
                "a JSON object"
            )

        return event
