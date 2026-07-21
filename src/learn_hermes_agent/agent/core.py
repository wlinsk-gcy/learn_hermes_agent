from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage, assistant_message, system_message, user_message
from learn_hermes_agent.agent.context_compressor import ContextCompressor
from learn_hermes_agent.agent.iteration_budget import IterationBudget
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.agent.tool_executor import execute_tool_calls_sequential
from learn_hermes_agent.providers.base import ProviderTransport
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall, Usage
from learn_hermes_agent.tools.registry import ToolRegistry, get_default_registry


class AIAgent:
    # *表示后面的参数必须用关键字传参，不能用位置传参
    def __init__(self, provider: ProviderTransport, *, max_iterations: int = 10,
                 registry: ToolRegistry | None = None, context_compressor: ContextCompressor | None = None) -> None:
        self.provider = provider
        self.max_iterations = max_iterations
        self.registry = registry or get_default_registry()
        self.valid_tool_names: set[str] = set() # 对应 Hermes 的 agent.valid_tool_names
        self.context_compressor = context_compressor or ContextCompressor()
        self.last_context_compressed = False
        self.iteration_budget = IterationBudget(max_iterations)
        self.last_usage: Usage | None = None
        self.last_finish_reason: str | None = None
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_cached_tokens = 0

    def run_conversation(self, user_input: str, *, history: Sequence[ChatMessage] | None = None,
                         system_prompt: str | None = None, tool_context: ToolExecutionContext | None = None,) -> list[ChatMessage]:
        self.last_context_compressed = False
        messages: list[ChatMessage] = list(history or [])
        messages.append(user_message(user_input))

        self.iteration_budget = IterationBudget(self.max_iterations)
        while self.iteration_budget.consume():
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
            normalized_response = self.provider.complete(request_messages, tools=tools)
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
                self,               # AIAgent，提供 registry、valid_tool_names 和解析 helper
                assistant_response, # 包含本轮全部 tool_calls
                messages,           # 执行器向这里追加 role=tool 消息
                tool_context=tool_context,
            )

        raise RuntimeError(
            f"Exceeded max_iterations={self.iteration_budget.max_total} before receiving a final assistant message."
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

        provider_model = self.provider.model
        # 只有fallbackProvider才会有last_provider_model的更新，其他provider都是None
        last_provider_model = getattr(self.provider, "last_provider_model", None)
        last_provider_index = getattr(self.provider, "last_provider_index", None)
        last_provider_error = getattr(self.provider, "last_error", None)

        if not isinstance(last_provider_model, str) or not last_provider_model:
            last_provider_model = provider_model

        fallback_used = isinstance(last_provider_index, int) and last_provider_index > 0

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
                "configured_model": provider_model,
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
