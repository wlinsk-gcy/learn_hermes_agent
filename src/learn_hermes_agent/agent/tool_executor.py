from __future__ import annotations

import json
from typing import Any


def _parse_tool_arguments(
        raw_arguments: Any,
) -> tuple[dict[str, Any], str | None]:
    """只允许 JSON object 进入工具分发。非法 JSON、数组、数字和字符串都返回结构化错误，不尝试修复"""
    if raw_arguments is None:
        return {}, None

    if isinstance(raw_arguments, dict):
        return raw_arguments, None

    if isinstance(raw_arguments, str) and not raw_arguments.strip():
        return {}, None

    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        arguments = None

    if isinstance(arguments, dict):
        return arguments, None

    return {}, json.dumps(
        {
            "error": "Invalid tool arguments",
            "message": (
                "Tool arguments must be a valid JSON object; "
                "tool was not executed."
            ),
        },
        ensure_ascii=False,
    )
