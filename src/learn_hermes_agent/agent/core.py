from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage, system_message, tool_message, user_message
from learn_hermes_agent.agent.context_compressor import ContextCompressor
from learn_hermes_agent.agent.iteration_budget import IterationBudget
from learn_hermes_agent.model_tools import safe_handle_function_call
from learn_hermes_agent.providers.base import ProviderTransport
from learn_hermes_agent.tools.registry import ToolRegistry, get_default_registry


class AIAgent:
    # *表示后面的参数必须用关键字传参，不能用位置传参
    def __init__(self, provider: ProviderTransport, *, max_iterations: int = 10,
                 registry: ToolRegistry | None = None, context_compressor: ContextCompressor | None = None) -> None:
        self.provider = provider
        self.max_iterations = max_iterations
        self.registry = registry or get_default_registry()
        self.context_compressor = context_compressor or ContextCompressor()
        self.last_context_compressed = False
        self.iteration_budget = IterationBudget(max_iterations)

    def run_conversation(self, user_input: str, *, history: Sequence[ChatMessage] | None = None, system_prompt: str | None = None) -> list[ChatMessage]:
        self.last_context_compressed = False
        messages: list[ChatMessage] = list(history or [])
        messages.append(user_message(user_input))

        self.iteration_budget = IterationBudget(self.max_iterations)
        while self.iteration_budget.consume():
            before_compression_count = len(messages)
            messages = self.context_compressor.compress(messages, system_prompt=system_prompt)
            if len(messages) < before_compression_count:
                self.last_context_compressed = True
            request_messages = self._build_request_messages(messages,system_prompt=system_prompt)
            assistant_response = self.provider.complete(request_messages)
            self._validate_assistant_message(assistant_response)

            messages.append(assistant_response)

            tool_calls = self._get_tool_calls(assistant_response)
            if not tool_calls:
                return messages

            for tool_call in tool_calls:
                function_name, arguments, tool_call_id = self._parse_tool_call(tool_call)
                result_json = safe_handle_function_call(function_name, arguments, registry=self.registry)
                messages.append(tool_message(name=function_name, content=result_json, tool_call_id=tool_call_id))

        raise RuntimeError(
            f"Exceeded max_iterations={self.iteration_budget.max_total} before receiving a final assistant message."
        )

    def _build_request_messages(self, messages: list[ChatMessage], *, system_prompt: str | None = None) -> list[ChatMessage]:
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
