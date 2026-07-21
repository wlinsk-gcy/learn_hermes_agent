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
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 200
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

PATCH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the UTF-8 text file to patch.",
        },
        "old_string": {
            "type": "string",
            "description": "Exact text to replace. Must appear in the file.",
        },
        "new_string": {
            "type": "string",
            "description": "Replacement text.",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences instead of requiring a unique match.",
            "default": False,  # 默认False，避免一个短字符串替换多处
        },
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}

SEARCH_FILES_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regex pattern to search for in UTF-8 text files.",
        },
        "path": {
            "type": "string",
            "description": "Directory or file to search in.",
            "default": ".",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of matches to return.",
            "default": 50,
            "minimum": 1,
            "maximum": 200,
        },
    },
    "required": ["pattern"],
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


def patch(arguments: dict[str, Any]) -> dict[str, object]:
    """单文件精确字符串替换"""

    path = arguments.get("path")
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    replace_all = arguments.get("replace_all", False)

    if not isinstance(path, str) or not path.strip():
        raise ValueError("patch requires a non-empty string argument: path")
    if not isinstance(old_string, str):
        raise ValueError("patch requires a string argument: old_string")
    if not isinstance(new_string, str):
        raise ValueError("patch requires a string argument: new_string")
    if not isinstance(replace_all, bool):
        raise ValueError("patch requires a boolean argument: replace_all")
    if old_string == "":
        raise ValueError("patch requires a non-empty old_string")

    context = create_tool_execution_context(load_config())
    decision = check_write_path(path, context)
    if not decision.allowed:
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    resolved = Path(decision.resolved_path)
    if _is_binary_path(resolved):
        return {
            "error": "patch refuses to edit obvious binary files",
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
        original = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "error": "file is not valid UTF-8 text",
            "path": path,
            "resolved_path": str(resolved),
        }

    count = original.count(old_string)
    if count == 0:
        return {
            "error": "old_string not found",
            "path": path,
            "resolved_path": str(resolved),
        }

    if count > 1 and not replace_all:
        return {
            "error": "old_string is not unique; pass replace_all=true to replace all occurrences",
            "path": path,
            "resolved_path": str(resolved),
            "occurrences": count,
        }

    if replace_all:
        updated = original.replace(old_string, new_string)
        replacements = count
    else:
        updated = original.replace(old_string, new_string, 1)
        replacements = 1

    resolved.write_text(updated, encoding="utf-8")
    bytes_written = len(updated.encode("utf-8"))

    return {
        "path": path,
        "resolved_path": str(resolved),
        "bytes_written": bytes_written,
        "replacements": replacements,
        "files_modified": [str(resolved)],
    }


def search_files(arguments: dict[str, Any]) -> dict[str, object]:
    pattern = arguments.get("pattern")
    path = arguments.get("path", ".")
    limit = _normalize_int(
        arguments.get("limit"),
        name="limit",
        default=DEFAULT_SEARCH_LIMIT,
        minimum=1,
        maximum=MAX_SEARCH_LIMIT,
    )

    if not isinstance(pattern, str) or not pattern:
        raise ValueError("search_files requires a non-empty string argument: pattern")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("search_files requires a non-empty string argument: path")

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {
            "error": f"invalid regex pattern: {exc}",
            "pattern": pattern,
            "path": path,
        }

    context = create_tool_execution_context(load_config())
    decision = check_read_path(path, context)
    if not decision.allowed:
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    resolved = Path(decision.resolved_path)
    if not resolved.exists():
        return {
            "error": "path not found",
            "path": path,
            "resolved_path": str(resolved),
        }

    if resolved.is_file():
        candidates = [resolved]
    else:
        candidates = [
            candidate
            for candidate in resolved.rglob("*")
            if candidate.is_file()
        ]

    matches: list[dict[str, object]] = []
    searched_files = 0
    skipped_files = 0

    for candidate in sorted(candidates):
        if _is_binary_path(candidate):
            skipped_files += 1
            continue

        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            skipped_files += 1
            continue

        searched_files += 1
        for line_number, line in enumerate(lines, start=1):
            if regex.search(line):
                matches.append(
                    {
                        "path": str(candidate),
                        "line_number": line_number,
                        "line": line,
                    }
                )
                if len(matches) >= limit:
                    return {
                        "pattern": pattern,
                        "path": path,
                        "resolved_path": str(resolved),
                        "matches": matches,
                        "match_count": len(matches),
                        "searched_files": searched_files,
                        "skipped_files": skipped_files,
                        "truncated": True,
                    }

    return {
        "pattern": pattern,
        "path": path,
        "resolved_path": str(resolved),
        "matches": matches,
        "match_count": len(matches),
        "searched_files": searched_files,
        "skipped_files": skipped_files,
        "truncated": False,
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
    registry.register(
        ToolEntry(
            name="patch",
            description="Patch a UTF-8 workspace file by exact string replacement.",
            parameters=PATCH_PARAMETERS,
            handler=patch,
        )
    )
    registry.register(
        ToolEntry(
            name="search_files",
            description="Search UTF-8 workspace files by regex pattern.",
            parameters=SEARCH_FILES_PARAMETERS,
            handler=search_files,
        )
    )
