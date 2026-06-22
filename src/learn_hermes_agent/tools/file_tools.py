from __future__ import annotations

from pathlib import Path
from typing import Any

from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path
from learn_hermes_agent.agent.tool_context import create_tool_execution_context
from learn_hermes_agent.config import load_config
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

DEFAULT_READ_LIMIT = 500
MAX_READ_LIMIT = 2000
MAX_READ_CHARS = 100_000

BINARY_EXTENSIONS = {
    ".7z",
    ".bin",
    ".db",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".so",
    ".tar",
    ".ttf",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}

READ_FILE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the text file to read.",
        },
        "offset": {
            "type": "integer",
            "description": "1-indexed line number to start reading from.",
            "default": 1,
            "minimum": 1,
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of lines to read.",
            "default": DEFAULT_READ_LIMIT,
            "minimum": 1,
            "maximum": MAX_READ_LIMIT,
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

WRITE_FILE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to write.",
        },
        "content": {
            "type": "string",
            "description": "Complete UTF-8 text content to write.",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


def _normalize_int(
        value: object,
        *,
        name: str,
        default: int,
        minimum: int,
        maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        # 报错
        raise ValueError(f"{name} must be >= {minimum}")
    if value > maximum:
        # 截断
        return maximum
    return value


def _is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def read_file(arguments: dict[str, Any]) -> dict[str, object]:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("read_file requires a non-empty string argument: path")

    offset = _normalize_int(arguments.get("offset"), name="offset", default=1, minimum=1, maximum=1_000_000)
    limit = _normalize_int(arguments.get("limit"), name="limit", default=DEFAULT_READ_LIMIT, minimum=1,
                           maximum=MAX_READ_LIMIT)

    context = create_tool_execution_context(load_config())
    decision = check_read_path(path, context)
    if not decision.allowed:
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    resolved = Path(decision.resolved_path)
    if _is_binary_path(resolved):
        return {
            "error": "read_file refuses to read obvious binary files",
            "path": path,
            "resolved_path": str(resolved),
        }

    if not resolved.exists():
        return {
            "error": "file not found",
            "path": path,
            "resolved_path": str(resolved),
        }

    if not resolved.is_file():
        return {
            "error": "path is not a file",
            "path": path,
            "resolved_path": str(resolved),
        }
    try:
        raw_content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "error": "file is not valid UTF-8 text",
            "path": path,
            "resolved_path": str(resolved),
        }

    lines = raw_content.splitlines()
    start_index = offset - 1
    selected = lines[start_index:start_index + limit]
    numbered = [
        f"{line_number}: {line}"
        for line_number, line in enumerate(selected, start=offset)
    ]
    content = "\n".join(numbered)

    if len(content) > MAX_READ_CHARS:
        return {
            "error": f"read_file result exceeds {MAX_READ_CHARS} characters; use offset and limit",
            "path": path,
            "resolved_path": str(resolved),
            "total_lines": len(lines),
        }

    return {
        "path": path,
        "resolved_path": str(resolved),
        "offset": offset,
        "limit": limit,
        "total_lines": len(lines),
        "returned_lines": len(selected),
        "truncated": start_index + limit < len(lines),
        "content": content,
    }


def write_file(arguments: dict[str, Any]) -> dict[str, object]:
    path = arguments.get("path")
    content = arguments.get("content")

    if not isinstance(path, str) or not path.strip():
        raise ValueError("write_file requires a non-empty string argument: path")
    if not isinstance(content, str):
        raise ValueError("write_file requires a string argument: content")

    # model_tools 以后会在 dispatch 前做统一 preflight，但 handler 内部仍然再调用一次 check_write_path()，
    # 避免有人绕过 handle_function_call() 直接调用 handler 时写入敏感路径。
    context = create_tool_execution_context(load_config())
    decision = check_write_path(path, context)
    if not decision.allowed:
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    resolved = Path(decision.resolved_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    bytes_written = len(content.encode("utf-8"))

    return {
        "path": path,
        "resolved_path": str(resolved),
        "bytes_written": bytes_written,
        "files_modified": [str(resolved)],
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="read_file",
            description="Read a UTF-8 text file with line numbers and pagination.",
            parameters=READ_FILE_PARAMETERS,
            handler=read_file,
        )
    )
