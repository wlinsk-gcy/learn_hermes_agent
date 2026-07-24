from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage, assistant_message, system_message, user_message
from learn_hermes_agent.agent.context_compressor import ContextCompressor
from learn_hermes_agent.agent.iteration_budget import IterationBudget
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.agent.tool_executor import execute_tool_calls_sequential
from learn_hermes_agent.providers.runtime import (
    ProviderBinding,
)
from learn_hermes_agent.providers.transports import (
    get_transport,
)
from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall, Usage
from learn_hermes_agent.tools.registry import ToolRegistry, get_default_registry
from learn_hermes_agent.tools.checkpoint_manager import CheckpointManager
from learn_hermes_agent.providers.request import (
    request_provider_completion,
)
from learn_hermes_agent.providers.streaming import (
    ProviderStreamCallbacks,
    ProviderStreamError,
)
from learn_hermes_agent.providers.retry import (
    RetryPolicy,
)
from learn_hermes_agent.providers.state import (
    ProviderBindingState,
)

"""
feat: Providers 流式请求生命周期 Task 6: AIAgent 流式编排与 fallback 边界 Step 1：接入 AIAgent
最新调用链：
AIAgent
    → request_provider_completion
    → Client
    → streaming accumulator
    → 原始完整响应
    → Transport normalize
"""


class AIAgent:
    # *表示后面的参数必须用关键字传参，不能用位置传参
    def __init__(
            self,
            provider_bindings: Sequence[ProviderBinding],
            *,
            max_iterations: int = 10,
            retry_policy: RetryPolicy | None = None,
            registry: ToolRegistry | None = None,
            context_compressor: ContextCompressor | None = None,
            checkpoints_enabled: bool = False,
            checkpoint_max_snapshots: int = 20,
    ) -> None:
        if not provider_bindings:
            raise ValueError(
                "AIAgent requires at least one "
                "ProviderBinding"
            )
        # 按 primary/fallback 顺序保存候选 Provider
        self.provider_bindings = list(
            provider_bindings
        )
        self.provider_binding_states: list[
            ProviderBindingState
        ] = [
            ProviderBindingState()
            for _ in self.provider_bindings
        ]
        if retry_policy is None:
            self.retry_policy = RetryPolicy()
        elif not isinstance(retry_policy, RetryPolicy):
            raise TypeError(
                "retry_policy must be a RetryPolicy or None"
            )
        else:
            self.retry_policy = retry_policy
        # 按 api_mode 复用无状态 Transport
        self._transport_cache: dict[
            str,
            ProviderTransport,
        ] = {}
        #  last_provider_*: 保存最近一次请求的可观察状态
        self.last_provider_model: str | None = None
        self.last_provider_index: int | None = None
        self.last_provider_error: str | None = None
        self.max_iterations = max_iterations
        self.registry = registry or get_default_registry()
        self._checkpoint_mgr = CheckpointManager(
            enabled=checkpoints_enabled,
            max_snapshots=checkpoint_max_snapshots,
        )
        self.valid_tool_names: set[str] = set()  # 对应 Hermes 的 agent.valid_tool_names
        self.context_compressor = context_compressor or ContextCompressor()
        self.last_context_compressed = False
        self.iteration_budget = IterationBudget(max_iterations)
        self.last_usage: Usage | None = None
        self.last_finish_reason: str | None = None
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_cached_tokens = 0

    def run_conversation(
            self,
            user_input: str,
            *,
            history: Sequence[ChatMessage] | None = None,
            system_prompt: str | None = None,
            tool_context: ToolExecutionContext | None = None,
            stream_callback: Callable[[str], None] | None = None,
    ) -> list[ChatMessage]:
        self.last_context_compressed = False
        messages: list[ChatMessage] = list(history or [])
        messages.append(user_message(user_input))

        stream_callbacks = (
            ProviderStreamCallbacks(
                on_text_delta=stream_callback,
            )
            if stream_callback is not None
            else None
        )
        self.iteration_budget = IterationBudget(self.max_iterations)
        while self.iteration_budget.consume():
            self._checkpoint_mgr.new_turn()
            before_compression_count = len(messages)
            messages = self.context_compressor.compress(messages, system_prompt=system_prompt)
            if len(messages) < before_compression_count:
                self.last_context_compressed = True
            request_messages = self._build_request_messages(messages, system_prompt=system_prompt)
            tools = self.registry.get_definitions()
            #  从实际 definitions 生成快照 -- 这里不能直接从self.registry.names里拿，因为默认的可能包含了被check_fn拒绝的工具
            self.valid_tool_names = {
                definition["function"]["name"]
                for definition in tools
            }
            # AIAgent -> Transport 构造请求 -> Client 执行 I/O -> Transport 标准化响应
            normalized_response = (
                self._complete_with_fallback(
                    request_messages,
                    tools=tools,
                    callbacks=stream_callbacks,
                )
            )
            self._record_provider_response(normalized_response)
            assistant_response = self._assistant_message_from_response(normalized_response)
            self._validate_assistant_message(assistant_response)

            messages.append(assistant_response)

            tool_calls = self._get_tool_calls(assistant_response)
            if not tool_calls:
                return messages
            # 这里删除 tool calls的for循环，目的不是删除工具执行，而是把工具执行编排从 AIAgent 移到独立的执行器模块。
            # 1. 同时关闭工具范围缺口，旧代码只要 Registry 中存在该工具，就可能执行，即使它没有出现在本轮发给模型的 tools 中
            # 2. 其次：AIAgent 应该关注：构造消息 -> 调用 Provider -> 判断是否有 tool calls
            # 执行器关注：范围检查 -> 参数解析 -> 执行 -> 生成 tool result
            # 3. 建立后续统一接入点： 未来 checkpoint 应在工具真正执行之前处理。如果执行逻辑散落在 AIAgent 中，checkpoint、terminal、并发和中断逻辑都会继续堆进去。
            # hermes也是这种结构
            execute_tool_calls_sequential(
                self,  # AIAgent，提供 registry、valid_tool_names 和解析 helper
                assistant_response,  # 包含本轮全部 tool_calls
                messages,  # 执行器向这里追加 role=tool 消息
                tool_context=tool_context,
            )

        raise RuntimeError(
            f"Exceeded max_iterations={self.iteration_budget.max_total} before receiving a final assistant message."
        )

    def _get_transport(
            self,
            api_mode: str,
    ) -> ProviderTransport:
        """
        第一次请求 chat_completions
            -> registry 创建 Transport
            -> 放入 Agent cache

        后续请求 chat_completions
            -> 直接复用同一个无状态 Transport

        缓存键是 API 协议，不是 Provider 名称。因此 openai 和未来其他兼容 Provider 可以共用同一个 ChatCompletionsTransport
        """
        transport = self._transport_cache.get(
            api_mode
        )

        if transport is None:
            transport = get_transport(api_mode)
            self._transport_cache[api_mode] = transport

        return transport

    def _complete_with_fallback(
            self,
            messages: Sequence[ChatMessage],
            *,
            tools: Sequence[dict[str, Any]] | None = None,
            callbacks: ProviderStreamCallbacks | None = None,
    ) -> NormalizedResponse:
        """
        完整拥有调用顺序：

        选择 Binding
            -> 获取 Transport
            -> 转换消息和工具
            -> 构造请求参数
            -> Client 发出原始请求
            -> Transport 校验并标准化
            -> 失败则尝试下一个 Binding

        只捕获 RuntimeError 和 ValueError，不会把 TypeError、AssertionError 等代码错误伪装成 fallback

        """
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

                converted_tools = (
                    transport.convert_tools(tools)
                )

                request_kwargs = (
                    transport.build_kwargs(
                        model=runtime.model,
                        messages=converted_messages,
                        tools=converted_tools,
                    )
                )

                raw_response = (
                    request_provider_completion(
                        binding,
                        request_kwargs,
                        callbacks=callbacks,  # 不传 stream_callback 时，callbacks=None，仍然走原同步路径
                        retry_policy=self.retry_policy,
                    )
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
            # ProviderStreamError 继承自 RuntimeError，因此必须放在普通 RuntimeError 捕获之前
            except ProviderStreamError as exc:
                error_text = (
                    f"{runtime.model}: {exc}"
                )
                self.last_provider_error = error_text
                # callback 已成功收到内容后失败：立即停止，不能把 fallback 的回答接在半段文本后面
                if exc.text_emitted:
                    raise
                # 首个 callback 前失败：用户还没看到 partial text，可以切换 fallback
                errors.append(error_text)
                continue

            except (RuntimeError, ValueError) as exc:
                error_text = (
                    f"{runtime.model}: {exc}"
                )
                errors.append(error_text)
                self.last_provider_error = error_text
                continue

            self.last_provider_model = runtime.model
            self.last_provider_index = index
            self.last_provider_error = None
            return response

        joined_errors = "; ".join(errors)
        raise RuntimeError(
            "All provider fallbacks failed: "
            f"{joined_errors}"
        )

    def _record_provider_response(self, response: NormalizedResponse) -> None:
        self.last_finish_reason = response.finish_reason
        self.last_usage = response.usage

        if response.usage is None:
            return

        self.session_prompt_tokens += response.usage.prompt_tokens
        self.session_completion_tokens += response.usage.completion_tokens
        self.session_cached_tokens += response.usage.cached_tokens

        total_tokens = response.usage.total_tokens
        if total_tokens <= 0:
            total_tokens = response.usage.prompt_tokens + response.usage.completion_tokens

        self.session_total_tokens += total_tokens

    def usage_snapshot(self) -> dict[str, object]:
        usage: Usage | None = self.last_usage
        last_usage_payload: dict[str, int] | None = None

        if usage is not None:
            last_usage_payload = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cached_tokens": usage.cached_tokens,
            }
        #  现在 usage 状态直接来自 AIAgent，不再通过 getattr() 探测旧 Fallback Provider 对象
        configured_model = (
            self.provider_bindings[0].runtime.model
        )

        last_provider_model = (
                self.last_provider_model
                or configured_model
        )

        last_provider_index = (
            self.last_provider_index
        )

        last_provider_error = (
            self.last_provider_error
        )

        fallback_used = (
                isinstance(last_provider_index, int)
                and last_provider_index > 0
        )

        return {
            "last_finish_reason": self.last_finish_reason,
            "last_usage": last_usage_payload,
            "session_usage": {
                "prompt_tokens": self.session_prompt_tokens,
                "completion_tokens": self.session_completion_tokens,
                "total_tokens": self.session_total_tokens,
                "cached_tokens": self.session_cached_tokens,
            },
            "provider": {
                "configured_model": configured_model,
                "last_model": last_provider_model,
                "last_provider_index": last_provider_index,
                "fallback_used": fallback_used,
                "last_error": last_provider_error,
            },
        }

    def _assistant_message_from_response(self, response: NormalizedResponse) -> ChatMessage:
        tool_calls = self._tool_calls_to_message_dicts(response.tool_calls)
        return assistant_message(response.content, tool_calls=tool_calls or None)

    def _tool_calls_to_message_dicts(self, tool_calls: list[ToolCall] | None) -> list[dict[str, Any]]:
        if not tool_calls:
            return []

        result: list[dict[str, Any]] = []
        for index, tool_call in enumerate(tool_calls, start=1):
            tool_call_id = tool_call.id or f"call_{index}"
            result.append(
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                }
            )

        return result

    def _build_request_messages(self, messages: list[ChatMessage], *, system_prompt: str | None = None) -> list[
        ChatMessage]:
        """这个helper只影响发给Provider的messages，不影响respond to user 的messages"""
        if not system_prompt:
            return list(messages)

        return [system_message(system_prompt), *messages]

    def _validate_assistant_message(self, message: ChatMessage) -> None:
        if message.get("role") != "assistant":
            raise ValueError("Provider must return an assistant message.")

        has_content = message.get("content") is not None
        has_tool_calls = bool(self._get_tool_calls(message))

        if not has_content and not has_tool_calls:
            raise ValueError("Assistant message must include content or tool_calls.")

    def _get_tool_calls(self, message: ChatMessage) -> list[object]:
        tool_calls: object = message.get("tool_calls")

        if tool_calls is None:
            return []

        if not isinstance(tool_calls, list):
            raise ValueError("Assistant message tool_calls must be a list.")

        return tool_calls

    def _parse_tool_call(self, tool_call: object) -> tuple[str, Any, str]:
        if not isinstance(tool_call, dict):
            raise ValueError("Tool call must be a dictionary.")

        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id:
            raise ValueError("Tool call must include an id.")

        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise ValueError("Tool call must include a function object.")

        function_name = str(function.get("name") or "")
        arguments = function.get("arguments", "{}")

        return function_name, arguments, tool_call_id
