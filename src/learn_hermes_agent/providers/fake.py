from __future__ import annotations

from typing import Sequence

from learn_hermes_agent.agent.messages import ChatMessage, assistant_message


class FakeProviderTransport:
    def __init__(self, model: str = "fake-basic") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, messages: Sequence[ChatMessage]) -> ChatMessage:
        last_user_content = ""

        for message in reversed(messages):
            if message.get("role") == "user":
                last_user_content = str(message.get("content") or "")
                break
        return assistant_message(f"{self.model} received: {last_user_content}")
