from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from learn_hermes_agent.config import load_config
from learn_hermes_agent.tools.approval import classify_command
from learn_hermes_agent.tools.environments.local import (
    LocalEnvironment,
    find_bash,
)
from learn_hermes_agent.tools.registry import (
    ToolEntry,
    ToolRegistry,
)

_STATIC_SENSITIVE_ENV_NAMES = {
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
}
# 用于识别明显会让进程脱离前台控制的 shell 包装命令
# - nohup：忽略挂断信号，常用于让程序持续运行。
# - disown：把任务从当前 shell 的任务管理中移除。
# - setsid：创建新 session，让进程脱离当前进程组。
_BACKGROUND_WRAPPER_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:nohup|disown|setsid)\b",
    re.IGNORECASE,  # 忽略大小写，因此 NOHUP 也能匹配
)

# 本批 schema 只开放 command、timeout、workdir，不出现 background、PTY 或交互输入参数
TERMINAL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "Bash command to execute in the local "
                "foreground environment."
            ),
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
            "description": (
                "Maximum seconds to wait for the command."
            ),
        },
        "workdir": {
            "type": "string",
            "description": (
                "Working directory for this command."
            ),
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


def _error_result(message: str) -> dict[str, object]:
    return {
        "output": "",
        "exit_code": -1,
        "error": message,
    }


def _strip_quoted_content(command: str) -> str:
    """
    不执行命令，只把引号内字符替换为空格，同时保留字符串长度和引号外的 shell 操作符。
    例如：

    echo "sleep 10 &"   -> echo
    sleep 10 &          -> sleep 10 &

    这样后续检测不会把普通字符串中的 &、nohup 误判成后台操作。
    """
    result: list[str] = []
    quote: str | None = None
    escaped = False

    for char in command:
        if escaped:
            # 这样 echo \& 不会被误判为后台执行。
            result.append(
                " "
                if quote or char in {";", "&", "|"}
                else char
            )
            escaped = False
            continue

        if char == "\\" and quote != "'":
            escaped = True
            # 这样 echo \& 不会被误判为后台执行。
            result.append(
                " "
                if quote or char in {";", "&", "|"}
                else char
            )
            continue

        if quote is not None:
            if char == quote:
                quote = None
            result.append(" ")
            continue

        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            continue

        result.append(char)

    return "".join(result)


def _contains_lone_background_ampersand(
        command: str,
) -> bool:
    """
    专门判断命令里是否存在表示后台执行的单独 &
    例如：

    sleep 10 &

    返回：

    True

    它会忽略不是后台符号的组合：

    cmd && next       # 逻辑 AND
    cmd &> output     # stdout/stderr 重定向
    cmd 2>&1          # 文件描述符重定向

    引号内容会先被 _strip_quoted_content() 隐藏：

    echo "A & B"

    因此返回 False。
    """
    unquoted = _strip_quoted_content(command)

    for index, char in enumerate(unquoted):
        if char != "&":
            continue

        previous = (
            unquoted[index - 1]
            if index > 0
            else ""
        )
        following = (
            unquoted[index + 1]
            if index + 1 < len(unquoted)
            else ""
        )
        # 这样就不会误判：
        # cmd |& grep error   # stdout/stderr 管道
        # case ... ;&         # case fall-through
        if previous in {"&", "<", ">", "|", ";"}:
            continue
        if following in {"&", ">"}:
            continue

        return True

    return False


def _foreground_error(command: str) -> str | None:
    """
    统一的前台策略入口，依次检查：
    1. nohup、disown、setsid 等脱离前台的包装命令。
    2. 单独的后台执行符号 &。
    返回值: None 表示允许继续执行
    返回字符串：Shell backgrounding with '&' is not supported... 表示拒绝，并提供错误原因。
    """
    unquoted = _strip_quoted_content(command)

    if _BACKGROUND_WRAPPER_RE.search(unquoted):
        return (
            "Background wrappers are not supported "
            "in this terminal batch."
        )

    if _contains_lone_background_ampersand(command):
        return (
            "Shell backgrounding with '&' is not supported "
            "in this terminal batch."
        )

    return None


def _provider_secret_env_names(
        config: dict[str, Any],
) -> set[str]:
    """
    除了三个常见 API key，还动态收集当前 Provider 和所有 fallback Provider 使用的环境变量名，传给LocalEnvironment 清理。
    """
    names = set(_STATIC_SENSITIVE_ENV_NAMES)

    model = config.get("model")
    if not isinstance(model, dict):
        return names

    api_key_env = model.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env:
        names.add(api_key_env)

    fallbacks = model.get("fallbacks")
    if isinstance(fallbacks, list):
        for fallback in fallbacks:
            if not isinstance(fallback, dict):
                continue

            fallback_env = fallback.get("api_key_env")
            if (
                    isinstance(fallback_env, str)
                    and fallback_env
            ):
                names.add(fallback_env)

    return names
