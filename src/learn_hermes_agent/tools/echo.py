from __future__ import annotations

from typing import Any
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

ECHO_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Text to return unchanged."
        }
    },
    "required": ["text"],
    "additionalProperties": False,
}

def echo(arguments: dict[str, Any]) -> dict[str, str]:
    text = arguments.get("text")

    if not isinstance(text, str):
        raise ValueError("echo requires a string argument: text")

    return {"text": text}

def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="echo",
            description="Return the provided text unchanged.",
            parameters=ECHO_PARAMETERS,
            handler=echo,
        )
    )