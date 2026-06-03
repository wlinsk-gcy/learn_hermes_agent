from __future__ import annotations

from collections.abc import Sequence


from learn_hermes_agent.agent.messages import ChatMessage, assistant_message, user_message
from learn_hermes_agent.providers.base import ProviderTransport


class AIAgent:
    # *表示后面的参数必须用关键字传参，不能用位置传参
    def __init__(self, provider: ProviderTransport, *, max_iterations: int = 10) -> None:
        self.provider = provider
        self.max_iterations = max_iterations

    def run_conversation(self, user_input: str, *, history: Sequence[ChatMessage] | None = None, ) -> list[ChatMessage]:
        messages: list[ChatMessage] = list(history or [])
        messages.append(user_message(user_input))

        assistant_response = self.provider.complete(messages)
        self._validate_assistant_message(assistant_response)

        messages.append(assistant_response)
        return messages

    def _validate_assistant_message(self, message: ChatMessage) -> None:
        if message.get("role") != "assistant":
            raise ValueError("Provider must return an assistant message.")
        if "content" not in message:
            raise ValueError("Assistant message must include content.")
