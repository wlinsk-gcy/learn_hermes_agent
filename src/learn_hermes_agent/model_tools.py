from __future__ import annotations

import json
from typing import Any

from learn_hermes_agent.tools.registry import ToolRegistry, get_default_registry


def get_tool_definitions(registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    target = registry or get_default_registry()
    return target.list_definitions()

def handle_function_call(name: str, arguments_json: str | dict[str, Any] | None = None, *, registry: ToolRegistry | None = None) -> str:
    target = registry or get_default_registry()
    entry = target.get(name)
    arguments = _parse_arguments(arguments_json)

    result = entry.handler(arguments)
    return json.dumps(result, ensure_ascii=False)

def safe_handle_function_call(name: str, arguments_json: str | dict[str, Any] | None = None, *, registry: ToolRegistry | None = None) -> str:
    try:
        return handle_function_call(name, arguments_json, registry=registry)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _parse_arguments(arguments_json: str | dict[str, Any] | None) -> dict[str, Any]:
    if arguments_json is None or arguments_json == "":
        return {}

    if isinstance(arguments_json, dict):
        return arguments_json

    try:
        decoded = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Tool arguments must be valid JSON: {exc.msg}") from exc

    if not isinstance(decoded, dict):
        raise ValueError("Tool arguments must decode to a JSON object.")

    return decoded
