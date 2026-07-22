from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.tools.approval import check_command_approval
from learn_hermes_agent.tools.registry import ToolRegistry, get_default_registry

logger = logging.getLogger(__name__)

BeforeDispatch = Callable[
    [
        str, # 工具名称
        dict[str, Any], # 已解析的参数字典
        ToolExecutionContext | None, # 工具执行上下文
    ],
    None,
]


def get_tool_definitions(
        registry: ToolRegistry | None = None,
        *,  # * 表示 tool_names 必须使用关键字传参：
        tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    target = registry or get_default_registry()
    return target.get_definitions(tool_names)


def handle_function_call(name: str, arguments_json: str | dict[str, Any] | None = None, *,
                         registry: ToolRegistry | None = None,
                         context: ToolExecutionContext | None = None,
                         ) -> str:
    target = registry or get_default_registry()
    entry = target.get(name)
    arguments = _parse_arguments(arguments_json)

    preflight_error = _preflight_tool_call(name, arguments, context)
    if preflight_error is not None:
        return json.dumps(preflight_error, ensure_ascii=False)

    result = entry.handler(arguments)
    return json.dumps(result, ensure_ascii=False)


def safe_handle_function_call(name: str, arguments_json: str | dict[str, Any] | None = None, *,
                              registry: ToolRegistry | None = None,
                              context: ToolExecutionContext | None = None, ) -> str:
    try:
        return handle_function_call(name, arguments_json, registry=registry, context=context)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _preflight_tool_call(name: str, arguments: dict[str, Any], context: ToolExecutionContext | None, ) -> dict[
                                                                                                              str, object] | None:
    if context is None:
        return None

    if name == "terminal":
        command = arguments.get("command")
        if not isinstance(command, str):
            return {"error": "terminal requires a string argument: command"}
        decision = check_command_approval(command, context)
        if decision.approved:
            return None
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload
    if name in {"read_file", "search_files"}:
        default_path = "." if name == "search_files" else None
        path = arguments.get("path", default_path)

        if not isinstance(path, str) or not path.strip():
            return {"error": f"{name} requires a non-empty string argument: path"}

        decision = check_read_path(path, context)
        if decision.allowed:
            return None

        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    if name in {"write_file", "patch"}:
        path = arguments.get("path")
        if not isinstance(path, str):
            return {"error": f"{name} requires a string argument: path"}
        decision = check_write_path(path, context)
        if decision.allowed:
            return None
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    return None


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
