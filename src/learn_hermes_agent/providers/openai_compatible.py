from __future__ import annotations

import json
from typing import Any
from urllib import error, request


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

        return request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": (
                    f"Bearer {self._api_key}"
                ),
                "Content-Type": "application/json",
            },
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
            # 读取错误响应的正文。
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
            raise RuntimeError(
                "Provider request failed with HTTP "
                f"{exc.code}: "
                f"{self._shorten(error_body)}"
            ) from exc
        # 捕获网络层错误，比如：域名解析失败，连接被拒绝，base_url 写错等等
        except error.URLError as exc:
            raise RuntimeError(
                f"Provider request failed: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "Provider request timed out"
            ) from exc

        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Provider returned invalid JSON"
            ) from exc

    @staticmethod
    def _shorten(
            text: str,
            *,
            max_length: int = 500,
    ) -> str:
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}..."
