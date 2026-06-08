from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage, user_message

_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class CompressionConfig:
    enabled: bool = True
    context_length: int = 2000
    threshold: float = 0.5
    protect_first_n: int = 2
    protect_last_n: int = 6

    @property
    def threshold_tokens(self) -> int:
        return max(1, int(self.context_length * self.threshold))


class ContextCompressor:
    def __init__(self, config: CompressionConfig | None = None):
        self.config = config or CompressionConfig()

    def should_compress(self, messages: list[ChatMessage], *, system_prompt: str | None = None):
        if not self.config.enabled:
            return False

        return estimate_request_tokens_rough(messages, system_prompt=system_prompt) >= self.config.threshold_tokens

    def compress(self, messages: list[ChatMessage], *, system_prompt: str | None = None) -> list[ChatMessage]:
        if not self.should_compress(messages, system_prompt=system_prompt):
            return list(messages)
        if len(messages) <= self.config.protect_first_n + self.config.protect_last_n + 1:
            return list(messages)

        head_end = min(self.config.protect_first_n, len(messages))
        tail_start = max(head_end, len(messages) - self.config.protect_last_n)

        # 避免以后 protect_last_n=0 时越界
        while len(messages) > tail_start > head_end and messages[tail_start].get("role") == "tool":
            tail_start -= 1

        head = list(messages[:head_end])
        middle = list(messages[head_end:tail_start])
        tail = list(messages[tail_start:])

        if not middle:
            return list(messages)

        summary = user_message(_build_summary_message(middle))
        return [*head, summary, *tail]


def estimate_request_tokens_rough(messages: list[ChatMessage], *, system_prompt: str | None = None) -> int:
    total = estimate_text_tokens_rough(system_prompt or "")

    for message in messages:
        total += estimate_message_tokens_rough(message)

    return total


def estimate_message_tokens_rough(message: ChatMessage) -> int:
    # 固定的per-message固定开销，例如json的括号。
    # 真实 tokenizer 会把 message 边界、role、结构标记等也算进 token。我们现在没有接真实 tokenizer，所以用：total = 4
    total = 4
    total += estimate_text_tokens_rough(str(message.get("role") or ""))

    content = message.get("content")
    total += estimate_text_tokens_rough(_content_to_text(content))

    name = message.get("name")
    if name:
        total += estimate_text_tokens_rough(name)

    tool_call_id = message.get("tool_call_id")
    if tool_call_id:
        total += estimate_text_tokens_rough(tool_call_id)

    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        total += estimate_text_tokens_rough(json.dumps(tool_calls, ensure_ascii=False))

    return total


def estimate_text_tokens_rough(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def _build_summary_message(messages: list[ChatMessage]) -> str:
    lines = [
        "[Context compression summary]",
        f"Compressed {len(messages)} earlier messages to keep the request within context budget.",
        "",
        "Compressed message outline:",
    ]
    for index, message in enumerate(messages, start=1):
        role = message.get("role", "unknown")
        preview = _message_preview(message)
        lines.append(f"{index}. {role}: {preview}")

    return "\n".join(lines)


def _message_preview(message: ChatMessage, *, limit: int = 160) -> str:
    """暴力截断"""
    content = _content_to_text(message.get("content"))
    if not content and message.get("tool_calls"):
        content = json.dumps(message["tool_calls"], ensure_ascii=False)

    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact

    return compact[: limit - 3] + "..."
