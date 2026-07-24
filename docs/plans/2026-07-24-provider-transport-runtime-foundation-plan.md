# Provider Transport / Runtime Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把当前 `ProviderTransport.complete()` 黑盒拆成 Hermes 风格的 Provider Profile、Runtime、Client、API-mode Transport 和 `AIAgent` 请求编排，同时保持现有同步 CLI、tool demo、usage 与 fallback 行为。

**Architecture:** `ProviderProfile` 声明静态 Provider 属性，runtime builder 解析配置并生成 `ProviderBinding(runtime, client)`。`AIAgent` 按 `api_mode` 获取无状态 Transport，依次完成消息转换、请求参数构造、原始 client 调用、响应校验和标准化，并在候选 binding 间执行最小 fallback。

**Tech Stack:** Python 3.11、标准库 `dataclasses` / `abc` / `typing` / `urllib` / `json`、现有 `ChatMessage`、`NormalizedResponse`、CLI 和 config。

---

## 执行约定

- 代码由用户手工抄写；Codex 一次只提供一个小步骤。
- Codex 不直接修改 `src/`，除非用户再次明确授权。
- 文档可由 Codex 直接维护。
- 不新增测试文件。
- 每个 Task 使用 compile、CLI、一次性 Python 脚本或接口不变量验证。
- 不实现 streaming、Anthropic、Codex Responses、Gemini Native、credential pool 或复杂重试。
- primary / fallback 的请求编排必须归 `AIAgent`，不得重新放入 Client 或 Transport。
- 每完成一个 Task 并确认后，再进入下一个 Task。

## Task 1: ProviderProfile 与内置注册表

**Files:**

- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/__init__.py`

**Step 1: 在旧 Protocol 前增加 ProviderProfile**

`providers/base.py` 迁移期间暂时同时包含新 Profile 和旧
`ProviderTransport`。在 import 区增加：

```python
from dataclasses import dataclass
```

在旧 `ProviderTransport` 前增加：

```python
@dataclass(frozen=True)
class ProviderProfile:
    name: str
    api_mode: str
    aliases: tuple[str, ...] = ()
    default_base_url: str | None = None
    api_key_env: str | None = None
    requires_api_key: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Provider profile name must not be empty")
        if not self.api_mode.strip():
            raise ValueError("Provider profile api_mode must not be empty")
        if self.requires_api_key and not self.api_key_env:
            raise ValueError(
                "Provider profile requiring an API key must define api_key_env"
            )
```

不要让 Profile 读取 `os.environ` 或创建 client。

**Step 2: 建立内置 Profile registry**

把 `providers/__init__.py` 替换为：

```python
from __future__ import annotations

from learn_hermes_agent.providers.base import ProviderProfile

_PROVIDER_PROFILES: dict[str, ProviderProfile] = {}


def _normalize_provider_name(name: str) -> str:
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("Provider name must not be empty")
    return normalized


def register_provider_profile(profile: ProviderProfile) -> None:
    keys = (profile.name, *profile.aliases)
    normalized_keys = tuple(
        _normalize_provider_name(key)
        for key in keys
    )

    for key in normalized_keys:
        existing = _PROVIDER_PROFILES.get(key)
        if existing is not None and existing is not profile:
            raise ValueError(
                f"Provider profile name or alias already registered: {key!r}"
            )

    for key in normalized_keys:
        _PROVIDER_PROFILES[key] = profile


def get_provider_profile(name: str) -> ProviderProfile:
    normalized = _normalize_provider_name(name)
    profile = _PROVIDER_PROFILES.get(normalized)
    if profile is None:
        raise ValueError(f"Unsupported model provider: {normalized!r}")
    return profile


register_provider_profile(
    ProviderProfile(
        name="fake",
        api_mode="chat_completions",
        requires_api_key=False,
    )
)
register_provider_profile(
    ProviderProfile(
        name="openai-compatible",
        api_mode="chat_completions",
        aliases=("openai",),
        default_base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    )
)

__all__ = [
    "ProviderProfile",
    "get_provider_profile",
    "register_provider_profile",
]
```

别名返回同一个 canonical Profile；`openai` 不创建第二份配置。

**Step 3: 轻量验证**

Run:

```powershell
uv run python -m compileall -q src
```

Expected: 编译成功，旧 CLI 尚未改变。

Run:

```powershell
uv run python -c "from learn_hermes_agent.providers import get_provider_profile; a=get_provider_profile('openai'); b=get_provider_profile('openai-compatible'); assert a is b; assert a.api_mode == 'chat_completions'; assert get_provider_profile('fake').requires_api_key is False; print('provider-profile-ok')"
```

Expected: `provider-profile-ok`。

## Task 2: ProviderClient、ProviderRuntime 与 ProviderBinding

**Files:**

- Create: `src/learn_hermes_agent/providers/client.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`

**Step 1: 定义原始请求 Client 协议**

新建 `providers/client.py`：

```python
from __future__ import annotations

from typing import Any, Protocol


class ProviderClient(Protocol):
    def create(self, **request_kwargs: Any) -> object:
        ...
```

该接口只表达原始请求，不出现 `ChatMessage` 或 `NormalizedResponse`。

**Step 2: 增加不可变 Runtime 与 Binding**

在 `providers/runtime.py` import 区增加：

```python
from dataclasses import dataclass, field

from learn_hermes_agent.providers.client import ProviderClient
```

在 `OPENAI_COMPATIBLE_PROVIDERS` 前增加：

```python
@dataclass(frozen=True)
class ProviderRuntime:
    provider: str
    model: str
    api_mode: str
    timeout_seconds: float
    base_url: str | None = None
    api_key: str | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("Provider runtime provider must not be empty")
        if not self.model.strip():
            raise ValueError("Provider runtime model must not be empty")
        if not self.api_mode.strip():
            raise ValueError("Provider runtime api_mode must not be empty")
        if (
                isinstance(self.timeout_seconds, bool)
                or not isinstance(
                    self.timeout_seconds,
                    (int, float),
                )
                or self.timeout_seconds <= 0
        ):
            raise ValueError(
                "Provider runtime timeout_seconds must be positive"
            )


@dataclass(frozen=True)
class ProviderBinding:
    runtime: ProviderRuntime
    client: ProviderClient
```

Runtime 不包含 fallback index 或 last error；这些是 Agent 调用状态。

**Step 3: 验证数据边界**

Run:

```powershell
uv run python -c "from learn_hermes_agent.providers.runtime import ProviderRuntime; r=ProviderRuntime(provider='fake', model='fake-basic', api_mode='chat_completions', timeout_seconds=60.0); assert 'api_key' not in repr(r); print(r)"
```

Expected: 输出 Runtime，repr 中没有 `api_key`。

Run:

```powershell
uv run python -c "from learn_hermes_agent.providers.client import ProviderClient; assert hasattr(ProviderClient, 'create'); print('provider-client-ok')"
```

Expected: `provider-client-ok`。

## Task 3: Hermes 风格 Transport base 与 registry

**Files:**

- Create: `src/learn_hermes_agent/providers/transports/__init__.py`
- Create: `src/learn_hermes_agent/providers/transports/base.py`

**Step 1: 定义无状态 Transport 抽象基类**

新建 `providers/transports/base.py`：

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.types import NormalizedResponse


class ProviderTransport(ABC):
    @property
    @abstractmethod
    def api_mode(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def convert_messages(
            self,
            messages: Sequence[ChatMessage],
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def convert_tools(
            self,
            tools: Sequence[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [dict(tool) for tool in tools]

    @abstractmethod
    def build_kwargs(
            self,
            *,
            model: str,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def validate_response(self, response: object) -> bool:
        return response is not None

    @abstractmethod
    def normalize_response(
            self,
            response: object,
    ) -> NormalizedResponse:
        raise NotImplementedError

    def map_finish_reason(
            self,
            finish_reason: object,
            *,
            has_tool_calls: bool,
    ) -> str:
        if isinstance(finish_reason, str) and finish_reason:
            return finish_reason
        return "tool_calls" if has_tool_calls else "stop"
```

该类不得保存 model、base URL、API key、timeout 或 client。

**Step 2: 建立 api_mode registry**

新建 `providers/transports/__init__.py`：

```python
from __future__ import annotations

from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)

_TRANSPORT_REGISTRY: dict[
    str,
    type[ProviderTransport],
] = {}


def _normalize_api_mode(api_mode: str) -> str:
    normalized = str(api_mode).strip().lower()
    if not normalized:
        raise ValueError("api_mode must not be empty")
    return normalized


def register_transport(
        api_mode: str,
        transport_cls: type[ProviderTransport],
) -> None:
    normalized = _normalize_api_mode(api_mode)
    existing = _TRANSPORT_REGISTRY.get(normalized)
    if existing is not None and existing is not transport_cls:
        raise ValueError(
            f"Transport already registered for api_mode {normalized!r}"
        )
    _TRANSPORT_REGISTRY[normalized] = transport_cls


def get_transport(api_mode: str) -> ProviderTransport:
    normalized = _normalize_api_mode(api_mode)
    transport_cls = _TRANSPORT_REGISTRY.get(normalized)
    if transport_cls is None:
        raise ValueError(
            f"Unsupported provider api_mode: {normalized!r}"
        )
    return transport_cls()


__all__ = [
    "ProviderTransport",
    "get_transport",
    "register_transport",
]
```

此时 registry 为空是预期状态；Task 4 才注册内置协议。

**Step 3: 验证 registry fail-fast**

Run:

```powershell
uv run python -c "from learn_hermes_agent.providers.transports import get_transport; import sys; exec(\"try:\\n get_transport('missing')\\nexcept ValueError:\\n print('transport-fail-fast-ok')\")"
```

Expected: `transport-fail-fast-ok`。

## Task 4: ChatCompletionsTransport

**Files:**

- Create: `src/learn_hermes_agent/providers/transports/chat_completions.py`
- Modify: `src/learn_hermes_agent/providers/transports/__init__.py`

**Step 1: 实现消息、工具和响应转换**

新建 `providers/transports/chat_completions.py`：

```python
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)
from learn_hermes_agent.providers.types import (
    NormalizedResponse,
    ToolCall,
    Usage,
)


class ChatCompletionsTransport(ProviderTransport):
    @property
    def api_mode(self) -> str:
        return "chat_completions"

    def convert_messages(
            self,
            messages: Sequence[ChatMessage],
    ) -> list[dict[str, Any]]:
        return [
            self._convert_message(message)
            for message in messages
        ]

    def build_kwargs(
            self,
            *,
            model: str,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        if not model:
            raise ValueError("Provider model must not be empty")

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }
        if tools:
            kwargs["tools"] = list(tools)
        return kwargs

    def validate_response(self, response: object) -> bool:
        return isinstance(response, dict)

    def normalize_response(
            self,
            response: object,
    ) -> NormalizedResponse:
        if not isinstance(response, dict):
            raise RuntimeError(
                "Provider response must be a JSON object"
            )

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "Provider response must include at least one choice"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(
                "Provider choice must be a JSON object"
            )

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(
                "Provider choice must include a message object"
            )

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise RuntimeError(
                "Provider message content must be a string or null"
            )

        tool_calls = self._parse_tool_calls(
            message.get("tool_calls")
        )
        finish_reason = self.map_finish_reason(
            first_choice.get("finish_reason"),
            has_tool_calls=bool(tool_calls),
        )

        return NormalizedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._parse_usage(response.get("usage")),
        )

    def _convert_message(
            self,
            message: ChatMessage,
    ) -> dict[str, Any]:
        role = message.get("role")
        if role not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ValueError(
                f"Unsupported message role: {role!r}"
            )

        payload: dict[str, Any] = {"role": role}

        if role == "assistant":
            payload["content"] = message.get("content")
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                payload["tool_calls"] = tool_calls
            return payload

        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id
            ):
                raise ValueError(
                    "Tool message must include tool_call_id"
                )
            payload["content"] = str(
                message.get("content") or ""
            )
            payload["tool_call_id"] = tool_call_id
            return payload

        payload["content"] = str(
            message.get("content") or ""
        )
        return payload

    def _parse_tool_calls(
            self,
            value: object,
    ) -> list[ToolCall] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise RuntimeError(
                "Provider message tool_calls must be a list"
            )

        result: list[ToolCall] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(
                    "Provider message tool_calls must contain objects"
                )

            function = item.get("function")
            if not isinstance(function, dict):
                raise RuntimeError(
                    "Provider tool_call must include a function object"
                )

            name = str(function.get("name") or "").strip()
            if not name:
                raise RuntimeError(
                    "Provider tool_call function must include a name"
                )

            arguments = function.get("arguments", "{}")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(arguments)
            else:
                arguments = str(arguments or "{}")

            raw_id = item.get("id")
            tool_call_id = (
                str(raw_id)
                if raw_id
                else f"call_{index}"
            )
            result.append(
                ToolCall(
                    id=tool_call_id,
                    name=name,
                    arguments=arguments,
                )
            )

        return result

    def _parse_usage(self, value: object) -> Usage | None:
        if not isinstance(value, dict):
            return None

        cached_tokens = 0
        prompt_details = value.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = self._get_non_negative_int(
                prompt_details.get("cached_tokens")
            )

        return Usage(
            prompt_tokens=self._get_non_negative_int(
                value.get("prompt_tokens")
            ),
            completion_tokens=self._get_non_negative_int(
                value.get("completion_tokens")
            ),
            total_tokens=self._get_non_negative_int(
                value.get("total_tokens")
            ),
            cached_tokens=cached_tokens,
        )

    @staticmethod
    def _get_non_negative_int(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
        return 0
```

**Step 2: 注册内置 Transport**

在 `providers/transports/__init__.py` 的 `get_transport()` 后、
`__all__` 前增加：

```python
from learn_hermes_agent.providers.transports.chat_completions import (
    ChatCompletionsTransport,
)

register_transport(
    ChatCompletionsTransport().api_mode,
    ChatCompletionsTransport,
)
```

并在 `__all__` 增加：

```python
"ChatCompletionsTransport",
```

**Step 3: 验证标准化**

Run:

```powershell
@'
from learn_hermes_agent.providers.transports import get_transport

transport = get_transport("chat_completions")
raw = {
    "choices": [{
        "message": {
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "echo",
                    "arguments": {"text": "hello"},
                },
            }],
        },
        "finish_reason": "tool_calls",
    }],
    "usage": {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
        "prompt_tokens_details": {
            "cached_tokens": 1,
        },
    },
}
response = transport.normalize_response(raw)
assert response.tool_calls
assert response.tool_calls[0].arguments == '{"text": "hello"}'
assert response.usage and response.usage.cached_tokens == 1
print("chat-completions-transport-ok")
'@ | uv run python -
```

Expected: `chat-completions-transport-ok`。

## Task 5: 原始 OpenAI-compatible 与 Fake Client

**Files:**

- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`
- Modify: `src/learn_hermes_agent/providers/fake.py`

**Step 1: 增加纯 HTTP Client**

在旧 `OpenAICompatibleProviderTransport` 前增加：

```python
class OpenAICompatibleClient:
    def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            timeout_seconds: float = 60.0,
    ) -> None:
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

    def create(self, **request_kwargs: Any) -> object:
        body = json.dumps(request_kwargs).encode("utf-8")
        http_request = request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(
                    http_request,
                    timeout=self._timeout_seconds,
            ) as response:
                response_body = response.read().decode(
                    "utf-8"
                )
        except error.HTTPError as exc:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
            raise RuntimeError(
                "Provider request failed with HTTP "
                f"{exc.code}: {self._shorten(error_body)}"
            ) from exc
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
```

迁移阶段旧 Provider 类暂时保留，保证 CLI 仍可运行。

**Step 2: 增加返回原始字典的 Fake Client**

在旧 `FakeProviderTransport` 前增加：

```python
class FakeProviderClient:
    def __init__(
            self,
            model: str = "fake-basic",
            *,
            scripted_responses: Sequence[object] | None = None,
    ) -> None:
        self._model = model
        self._scripted_responses = list(
            scripted_responses or []
        )
        self._script_index = 0

    def create(self, **request_kwargs: Any) -> object:
        if self._script_index < len(
                self._scripted_responses
        ):
            response = self._scripted_responses[
                self._script_index
            ]
            self._script_index += 1
            return response

        last_user_content = ""
        messages = request_kwargs.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if (
                        isinstance(message, dict)
                        and message.get("role") == "user"
                ):
                    last_user_content = str(
                        message.get("content") or ""
                    )
                    break

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        f"{self._model} received: "
                        f"{last_user_content}"
                    ),
                },
                "finish_reason": "stop",
            }],
        }


def tool_demo_client(
        model: str = "fake-basic",
) -> FakeProviderClient:
    return FakeProviderClient(
        model=model,
        scripted_responses=[
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_echo_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": (
                                    '{"text":"hello from tool"}'
                                ),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": (
                            "final answer after echo tool"
                        ),
                    },
                    "finish_reason": "stop",
                }],
            },
        ],
    )
```

**Step 3: 验证 Fake 经过真实 Transport**

Run:

```powershell
@'
from learn_hermes_agent.providers.fake import FakeProviderClient
from learn_hermes_agent.providers.transports import get_transport

client = FakeProviderClient(model="fake-check")
transport = get_transport("chat_completions")
messages = transport.convert_messages([
    {"role": "user", "content": "hello"},
])
kwargs = transport.build_kwargs(
    model="fake-check",
    messages=messages,
    tools=None,
)
response = transport.normalize_response(
    client.create(**kwargs)
)
assert response.content == "fake-check received: hello"
print("fake-client-transport-ok")
'@ | uv run python -
```

Expected: `fake-client-transport-ok`。

## Task 6: 构建 ProviderBinding chain

**Files:**

- Modify: `src/learn_hermes_agent/providers/runtime.py`

**Step 1: 增加新路径所需 import**

在 import 区增加：

```python
from learn_hermes_agent.providers import get_provider_profile
from learn_hermes_agent.providers.fake import (
    FakeProviderClient,
    tool_demo_client,
)
from learn_hermes_agent.providers.openai_compatible import (
    OpenAICompatibleClient,
)
```

迁移期间保留旧 Provider import 和 `build_provider_transport()`。

**Step 2: 增加 binding builder**

在旧 `build_provider_transport()` 前增加：

```python
def build_provider_bindings(
        config: dict[str, Any],
        *,
        tool_demo: bool = False,
) -> list[ProviderBinding]:
    model_config = _get_model_config(config)

    if tool_demo:
        model = _get_string(
            model_config,
            "default",
            "fake-basic",
        )
        runtime = ProviderRuntime(
            provider="fake",
            model=model,
            api_mode="chat_completions",
            timeout_seconds=_get_positive_float(
                model_config,
                "timeout_seconds",
                60.0,
            ),
        )
        return [
            ProviderBinding(
                runtime=runtime,
                client=tool_demo_client(model=model),
            )
        ]

    bindings = [_build_provider_binding(model_config)]
    fallback_configs = model_config.get("fallbacks")

    if isinstance(fallback_configs, list):
        for fallback_config in fallback_configs:
            if not isinstance(fallback_config, dict):
                continue
            bindings.append(
                _build_provider_binding(fallback_config)
            )

    return bindings


def _build_provider_binding(
        model_config: dict[str, Any],
) -> ProviderBinding:
    runtime = _resolve_provider_runtime(model_config)

    if runtime.provider == "fake":
        client: ProviderClient = FakeProviderClient(
            model=runtime.model
        )
    elif runtime.api_mode == "chat_completions":
        if not runtime.base_url or not runtime.api_key:
            raise ValueError(
                "OpenAI-compatible runtime requires "
                "base_url and api_key"
            )
        client = OpenAICompatibleClient(
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            timeout_seconds=runtime.timeout_seconds,
        )
    else:
        raise ValueError(
            "No client available for provider "
            f"{runtime.provider!r} with api_mode "
            f"{runtime.api_mode!r}"
        )

    return ProviderBinding(
        runtime=runtime,
        client=client,
    )


def _resolve_provider_runtime(
        model_config: dict[str, Any],
) -> ProviderRuntime:
    requested_provider = _get_string(
        model_config,
        "provider",
        "fake",
    ).lower()
    profile = get_provider_profile(requested_provider)
    model = _get_string(
        model_config,
        "default",
        "fake-basic",
    )
    timeout_seconds = _get_positive_float(
        model_config,
        "timeout_seconds",
        60.0,
    )

    base_url: str | None = None
    api_key: str | None = None

    if profile.default_base_url is not None:
        base_url = _get_string(
            model_config,
            "base_url",
            profile.default_base_url,
        )

    if profile.requires_api_key:
        default_api_key_env = profile.api_key_env or ""
        api_key_env = _get_string(
            model_config,
            "api_key_env",
            default_api_key_env,
        )
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ValueError(
                "Missing API key env var for provider "
                f"{requested_provider!r}: {api_key_env}"
            )

    return ProviderRuntime(
        provider=profile.name,
        model=model,
        api_mode=profile.api_mode,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
```

**Step 3: 验证 primary、fallback 和 alias**

Run:

```powershell
@'
from learn_hermes_agent.providers.runtime import build_provider_bindings

config = {
    "model": {
        "provider": "fake",
        "default": "primary",
        "fallbacks": [{
            "provider": "fake",
            "default": "fallback",
        }],
    },
}
bindings = build_provider_bindings(config)
assert [item.runtime.model for item in bindings] == [
    "primary",
    "fallback",
]
assert all(
    item.runtime.api_mode == "chat_completions"
    for item in bindings
)
print("provider-bindings-ok")
'@ | uv run python -
```

Expected: `provider-bindings-ok`。

Run:

```powershell
uv run python -c "import os; os.environ.pop('MISSING_PROVIDER_KEY', None); from learn_hermes_agent.providers.runtime import build_provider_bindings; config={'model': {'provider': 'openai', 'default': 'x', 'api_key_env': 'MISSING_PROVIDER_KEY'}}; exec(\"try:\\n build_provider_bindings(config)\\nexcept ValueError as exc:\\n assert 'MISSING_PROVIDER_KEY' in str(exc); print('missing-key-ok')\")"
```

Expected: `missing-key-ok`。

## Task 7: 把请求与 fallback 编排迁移到 AIAgent

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

该 Task 是调用链切换点；两个文件必须在同一步完成后再运行 CLI。

**Step 1: 替换 AIAgent Provider import**

删除：

```python
from learn_hermes_agent.providers.base import ProviderTransport
```

增加：

```python
from learn_hermes_agent.providers.runtime import ProviderBinding
from learn_hermes_agent.providers.transports import get_transport
from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)
```

**Step 2: 替换构造参数和 Provider 状态**

把：

```python
provider: ProviderTransport,
```

替换为：

```python
provider_bindings: Sequence[ProviderBinding],
```

把：

```python
self.provider = provider
```

替换为：

```python
if not provider_bindings:
    raise ValueError(
        "AIAgent requires at least one ProviderBinding"
    )

self.provider_bindings = list(provider_bindings)
self._transport_cache: dict[str, ProviderTransport] = {}
self.last_provider_model: str | None = None
self.last_provider_index: int | None = None
self.last_provider_error: str | None = None
```

**Step 3: 替换每轮 Provider 调用**

把：

```python
normalized_response = self.provider.complete(
    request_messages,
    tools=tools,
)
```

替换为：

```python
normalized_response = self._complete_with_fallback(
    request_messages,
    tools=tools,
)
```

**Step 4: 增加 Agent-owned 请求编排**

在 `_record_provider_response()` 前增加：

```python
def _get_transport(
        self,
        api_mode: str,
) -> ProviderTransport:
    transport = self._transport_cache.get(api_mode)
    if transport is None:
        transport = get_transport(api_mode)
        self._transport_cache[api_mode] = transport
    return transport

def _complete_with_fallback(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
) -> NormalizedResponse:
    errors: list[str] = []
    self.last_provider_model = None
    self.last_provider_index = None
    self.last_provider_error = None

    for index, binding in enumerate(
            self.provider_bindings
    ):
        runtime = binding.runtime
        try:
            transport = self._get_transport(
                runtime.api_mode
            )
            converted_messages = (
                transport.convert_messages(messages)
            )
            converted_tools = transport.convert_tools(
                tools
            )
            request_kwargs = transport.build_kwargs(
                model=runtime.model,
                messages=converted_messages,
                tools=converted_tools,
            )
            raw_response = binding.client.create(
                **request_kwargs
            )
            if not transport.validate_response(
                    raw_response
            ):
                raise RuntimeError(
                    "Provider response failed validation"
                )
            response = transport.normalize_response(
                raw_response
            )
        except (RuntimeError, ValueError) as exc:
            error_text = f"{runtime.model}: {exc}"
            errors.append(error_text)
            self.last_provider_error = error_text
            continue

        self.last_provider_model = runtime.model
        self.last_provider_index = index
        self.last_provider_error = None
        return response

    joined_errors = "; ".join(errors)
    raise RuntimeError(
        f"All provider fallbacks failed: {joined_errors}"
    )
```

`TypeError` 和 `AssertionError` 不在捕获列表中，避免把编程错误伪装成 Provider
fallback。

**Step 5: 更新 usage_snapshot**

把依赖 `self.provider` 和 `getattr()` 的区段替换为：

```python
configured_model = (
    self.provider_bindings[0].runtime.model
)
last_provider_model = (
    self.last_provider_model or configured_model
)
last_provider_index = self.last_provider_index
last_provider_error = self.last_provider_error
fallback_used = (
    isinstance(last_provider_index, int)
    and last_provider_index > 0
)
```

返回字典中的字段保持原名，只把：

```python
"configured_model": provider_model,
```

改为：

```python
"configured_model": configured_model,
```

**Step 6: 切换 CLI 构造链**

`cli/main.py` 把 import：

```python
from learn_hermes_agent.providers.runtime import build_provider_transport
```

替换为：

```python
from learn_hermes_agent.providers.runtime import (
    build_provider_bindings,
)
```

把 `build_agent()` 中：

```python
provider = build_provider_transport(
    config,
    tool_demo=tool_demo,
)
```

替换为：

```python
provider_bindings = build_provider_bindings(
    config,
    tool_demo=tool_demo,
)
```

并把：

```python
provider=provider,
```

替换为：

```python
provider_bindings=provider_bindings,
```

**Step 7: 编译和 CLI 回归**

Run:

```powershell
uv run python -m compileall -q src
```

Expected: 退出码 0。

Run:

```powershell
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent chat "provider boundary"
uv run learn-hermes-agent chat --tool-demo --show-messages "use echo"
```

Expected:

- 普通 fake chat 返回 `fake-basic received: provider boundary`。
- tool demo 保持 `user -> assistant(tool_calls) -> tool -> assistant`。
- tools schema 不变。

**Step 8: 阶段提交**

```powershell
git add src/learn_hermes_agent/providers src/learn_hermes_agent/agent/core.py src/learn_hermes_agent/cli/main.py
git commit -m "refactor: align provider runtime transport boundary"
```

## Task 8: 删除旧 complete() 与 FallbackProviderTransport

**Files:**

- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`
- Modify: `src/learn_hermes_agent/providers/fake.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Delete: `src/learn_hermes_agent/providers/fallback.py`

**Step 1: 清理 Provider base**

从 `providers/base.py` 删除旧 `ProviderTransport` Protocol 及其专用 import：

```python
from collections.abc import Sequence
from typing import Any, Protocol
from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.providers.types import NormalizedResponse
```

最终该文件只保留 `ProviderProfile` 及其必要 import。

**Step 2: 清理 OpenAI-compatible 文件**

删除旧 `OpenAICompatibleProviderTransport` 整个类，只保留 Task 5 新增的
`OpenAICompatibleClient`。最终该文件不得导入：

```python
ChatMessage
NormalizedResponse
ToolCall
Usage
Sequence
```

**Step 3: 清理 Fake 文件**

删除旧：

```python
FakeProviderTransport
tool_demo_provider
```

只保留：

```python
FakeProviderClient
tool_demo_client
```

并删除 `NormalizedResponse`、`ToolCall` 和 `ChatMessage` import。

**Step 4: 清理 runtime 的旧工厂**

删除：

```python
OPENAI_COMPATIBLE_PROVIDERS
build_provider_transport
_build_single_provider
```

删除旧 Provider 类 import。保留：

```python
ProviderRuntime
ProviderBinding
build_provider_bindings
_build_provider_binding
_resolve_provider_runtime
_get_model_config
_get_string
_get_positive_float
```

**Step 5: 删除旧 fallback 文件**

删除：

```text
src/learn_hermes_agent/providers/fallback.py
```

fallback 状态和循环现在唯一存在于 `AIAgent`。

**Step 6: 检查旧接口完全消失**

Run:

```powershell
rg -n "ProviderTransport\\(Protocol\\)|\\.complete\\(|FakeProviderTransport|OpenAICompatibleProviderTransport|FallbackProviderTransport|build_provider_transport" src
```

Expected: 无匹配。

Run:

```powershell
rg -n "api_key|base_url|timeout_seconds|urlopen" src/learn_hermes_agent/providers/transports
```

Expected: 无匹配。

Run:

```powershell
rg -n "ChatMessage|NormalizedResponse|ToolCall|Usage" src/learn_hermes_agent/providers/openai_compatible.py
```

Expected: 无匹配。

**Step 7: 提交旧路径清理**

```powershell
git add src/learn_hermes_agent/providers
git commit -m "refactor: remove legacy provider complete path"
```

## Task 9: Provider 主链完整验证

**Files:**

- No source changes unless validation exposes a defect.
- Do not create test files.

**Step 1: 编译**

Run:

```powershell
uv run python -m compileall -q src
```

Expected: 退出码 0。

**Step 2: 验证 fallback 所有权和状态**

Run:

```powershell
@'
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.providers.fake import FakeProviderClient
from learn_hermes_agent.providers.runtime import (
    ProviderBinding,
    ProviderRuntime,
)


class FailingClient:
    def create(self, **request_kwargs):
        raise RuntimeError("primary unavailable")


def runtime(model):
    return ProviderRuntime(
        provider="fake",
        model=model,
        api_mode="chat_completions",
        timeout_seconds=60.0,
    )


agent = AIAgent(
    provider_bindings=[
        ProviderBinding(
            runtime=runtime("primary"),
            client=FailingClient(),
        ),
        ProviderBinding(
            runtime=runtime("fallback"),
            client=FakeProviderClient("fallback"),
        ),
    ],
)
messages = agent.run_conversation("hello")
snapshot = agent.usage_snapshot()
assert messages[-1]["content"] == (
    "fallback received: hello"
)
assert snapshot["provider"]["configured_model"] == "primary"
assert snapshot["provider"]["last_model"] == "fallback"
assert snapshot["provider"]["last_provider_index"] == 1
assert snapshot["provider"]["fallback_used"] is True
print("agent-owned-fallback-ok")
'@ | uv run python -
```

Expected: `agent-owned-fallback-ok`。

**Step 3: 验证全部失败聚合**

Run:

```powershell
@'
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.providers.runtime import ProviderBinding, ProviderRuntime


class FailingClient:
    def __init__(self, message):
        self.message = message

    def create(self, **request_kwargs):
        raise RuntimeError(self.message)


def binding(model, message):
    return ProviderBinding(
        runtime=ProviderRuntime(
            provider="fake",
            model=model,
            api_mode="chat_completions",
            timeout_seconds=60.0,
        ),
        client=FailingClient(message),
    )


agent = AIAgent(
    provider_bindings=[
        binding("first", "one"),
        binding("second", "two"),
    ],
)
try:
    agent.run_conversation("hello")
except RuntimeError as exc:
    text = str(exc)
    assert "first: one" in text
    assert "second: two" in text
    print("aggregate-provider-errors-ok")
else:
    raise AssertionError("expected provider failure")
'@ | uv run python -
```

Expected: `aggregate-provider-errors-ok`。

**Step 4: CLI 与 schema 回归**

Run:

```powershell
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent chat "provider-final"
uv run learn-hermes-agent chat --tool-demo --show-messages "use echo"
```

Expected:

- doctor 和 tools 正常。
- fake chat 正常。
- tool demo 的 tool call id 配对正常。
- terminal/file/checkpoint/tool registry schema 没有变化。

**Step 5: 静态边界不变量**

Run:

```powershell
rg -n "urlopen|Authorization|api_key|base_url|timeout_seconds" src/learn_hermes_agent/providers/transports
rg -n "ChatMessage|NormalizedResponse|ToolCall|Usage" src/learn_hermes_agent/providers/openai_compatible.py
rg -n "build_provider_transport|FallbackProviderTransport|ProviderTransport\\(Protocol\\)" src
```

Expected: 三条命令均无匹配。

Run:

```powershell
git diff --check
```

Expected: 无 whitespace error。

## Task 10: 文档收口

**Files:**

- Modify: `AGENTS.md`
- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`

**Step 1: 更新当前状态**

记录：

- Provider Transport / Runtime Foundation 已完成。
- `AIAgent` 已拥有同步请求和最小 fallback 编排。
- 当前唯一 API mode 是 `chat_completions`。
- Transport 不拥有 client、凭据、网络、streaming 或 retry。
- fake 与 OpenAI-compatible 都经过统一标准化。

**Step 2: 记录明确未完成项**

保持下列项目为未完成：

- streaming / interrupt。
- Anthropic Messages。
- Codex Responses。
- Gemini Native client facade。
- Provider 插件发现。
- credential pool / rotation。
- 单 Provider retry、健康状态和完整 failover 状态机。

**Step 3: 指定下一批设计门**

下一批开始前重新对齐 Hermes 最新 HEAD，并在以下方向中按依赖顺序设计：

```text
同步 Transport / Runtime foundation
  -> streaming request lifecycle
  -> Anthropic Messages
  -> Codex Responses
  -> Gemini Native facade
  -> credential / retry / failover hardening
```

不得直接把 streaming 放进 `ProviderTransport`；它仍由 Agent/request helper 管理。

**Step 4: 最终提交**

```powershell
git add AGENTS.md docs/00-overview.md docs/02-roadmap.md docs/04-progress-handoff.md docs/plans/2026-07-24-provider-transport-runtime-foundation-design.md docs/plans/2026-07-24-provider-transport-runtime-foundation-plan.md
git commit -m "docs: close provider transport runtime foundation"
```
