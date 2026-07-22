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
    re.IGNORECASE, # 忽略大小写，因此 NOHUP 也能匹配
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
