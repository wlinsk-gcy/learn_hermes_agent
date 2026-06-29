from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path
from learn_hermes_agent.agent.tool_context import create_tool_execution_context
from learn_hermes_agent.config import load_config
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

DEFAULT_READ_LIMIT = 500
MAX_READ_LIMIT = 2000
MAX_READ_CHARS = 100_000
# 识别read_file工具行号的展示文本正则，判断是不是 数字| 开头，\s*允许空格
READ_FILE_LINE_RE = re.compile(r"^\s*(\d+)\|")

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

def _looks_like_read_file_line_numbered_content(content: str) -> bool:
    """目的：防止把read_file返回给模型看的带行号展示文本，直接写回真实文件；
    所以检测content里面是不是带了行号
    """
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) < 2:
        # 少于两行的都认为是合法的，就不拦截
        return False
    numbered: list[int] = []
    for line in lines:
        # 匹配是不是 数字| 开头
        match = READ_FILE_LINE_RE.match(line)
        if match:
            # 取行号
            numbered.append(int(match.group(1)))

    if len(numbered) < 2:
        # 如果一行都拦截的话，容易误杀了用户主动输入的带行号的信息
        return False
    if len(numbered) / len(lines) < 0.6:
        # 六成的比例同样是为了防止误杀
        # 如果带行号的占了全部非空行的比例小于60%，就认为不像是read_file的输出，也不拦截
        return False
    # 计算行号相邻出现的次数。如果consecutive_pairs < len(numbered) - 1 说明数据是这样的：numbered = [1, 7, 20]，就不是read_file的连续分页。就不拦截
    consecutive_pairs = sum(
        1
        for previous, current in zip(numbered, numbered[1:])
        if current == previous + 1
    )
    return consecutive_pairs >= len(numbered) - 1


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
        # |是展示分隔符，不是文件真实内容，会被re过滤掉
        f"{line_number}|{line}"
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
    # 先做路径判断（较敏感）再检查内容形状
    if _looks_like_read_file_line_numbered_content(content):
        return {
            "error": (
                "refusing to write read_file display text as file content; "
                "remove line-number prefixes before writing"
            ),
            "path": path,
            "resolved_path": decision.resolved_path,
        }

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
    registry.register(
        ToolEntry(
            name="write_file",
            description="Write complete UTF-8 text content to a workspace file.",
            parameters=WRITE_FILE_PARAMETERS,
            handler=write_file,
        )
    )
