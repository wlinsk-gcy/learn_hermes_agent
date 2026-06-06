from __future__ import annotations

from collections.abc import Sequence

from learn_hermes_agent.agent.messages import ChatMessage, assistant_message


class FakeProviderTransport:
    def __init__(self, model: str = "fake-basic", *, scripted_responses: Sequence[ChatMessage] | None = None) -> None:
        self._model = model
        self._scripted_responses = list(scripted_responses or [])
        self._script_index = 0

    @property
    def model(self) -> str:
        return self._model

    def complete(self, messages: Sequence[ChatMessage]) -> ChatMessage:
        if self._script_index < len(self._scripted_responses):
            response = self._scripted_responses[self._script_index]
            self._script_index += 1
            return response

        last_user_content = ""

        for message in reversed(messages):
            if message.get("role") == "user":
                last_user_content = str(message.get("content") or "")
                break
        return assistant_message(f"{self.model} received: {last_user_content}")



def tool_demo_provider(model: str = "fake-basic") -> FakeProviderTransport:
    return FakeProviderTransport(
        model=model,
        scripted_responses=[
            assistant_message(
                None,
                tool_calls=[
                    {
                        "id": "call_echo_1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"text":"hello from tool"}',
                        },
                    }
                ]
            ),
            assistant_message("final answer after echo tool"),
        ]
    )