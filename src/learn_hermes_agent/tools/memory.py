from __future__ import annotations

from typing import Any

from learn_hermes_agent.agent.memory_store import MemoryStore, MemoryTarget
from learn_hermes_agent.config import get_memory_dir_path
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

MEMORY_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "read", "replace", "remove"],
            "description": "Memory action to perform.",
        },
        "target": {
            "type": "string",
            "enum": ["memory", "user"],
            "description": "Memory file to operate on.",
        },
        "content": {
            "type": "string",
            "description": "New memory content for add or replace.",
        },
        "old_text": {
            "type": "string",
            "description": "Existing memory text or unique substring for replace or remove.",
        },
    },
    "required": ["action", "target"],
    "additionalProperties": False,
}


def memory(arguments: dict[str, Any]) -> dict[str, object]:
    action = arguments.get("action")
    target = arguments.get("target")

    if action not in {"add", "read", "replace", "remove"}:
        raise ValueError("memory action must be one of: add, read, replace, remove")
    if target not in {"memory", "user"}:
        raise ValueError("memory target must be one of: memory, user")

    store = MemoryStore(get_memory_dir_path())
    typed_target = target  # type: MemoryTarget
    if action == "read":
        return store.read(typed_target)

    if action == "add":
        return store.add(typed_target, arguments.get("content"))

    if action == "replace":
        old_text = arguments.get("old_text")
        if not isinstance(old_text, str):
            raise ValueError("replace requires a string argument: old_text")
        return store.replace(typed_target, old_text, arguments.get("content"))

    old_text = arguments.get("old_text")
    if not isinstance(old_text, str):
        raise ValueError("remove requires a string argument: old_text")
    return store.remove(typed_target, old_text)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="memory",
            description="Read and update persistent user or project memory.",
            parameters=MEMORY_PARAMETERS,
            handler=memory,
            toolset="memory",
        )
    )