from __future__ import annotations

import atexit
import re
import threading
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
from learn_hermes_agent.agent.tool_context import (
    ToolExecutionContext,
    get_current_tool_execution_context,
)
from learn_hermes_agent.agent.runtime_cwd import (
    record_session_cwd,
)

_STATIC_SENSITIVE_ENV_NAMES = {
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
}
# 保存 runtime_key -> LocalEnvironment
_active_environments: dict[
    str,
    LocalEnvironment,
] = {}
# 保护 environment 映射
_env_lock = threading.Lock()
# 保证相同 key 只有一个线程负责创建
_creation_locks: dict[
    str,
    threading.Lock,
] = {}
# 保护创建锁映射自身
_creation_locks_lock = threading.Lock()


def _get_creation_lock(
        runtime_key: str,
) -> threading.Lock:
    """
    获取按 key 创建锁
    效果：

    session-a 多次获取 → 同一个 Lock
    session-b 获取     → 另一个 Lock

    _creation_locks_lock 只保护“查找或创建 Lock”这个短操作，不包围耗时的 login bootstrap。
    """
    with _creation_locks_lock:
        lock = _creation_locks.get(
            runtime_key
        )

        if lock is None:
            lock = threading.Lock()
            _creation_locks[
                runtime_key
            ] = lock

        return lock


def _get_or_create_environment(
        *,
        runtime_key: str,
        bash_path: str,
        cwd: Path,
        sensitive_env_names: set[str],
) -> LocalEnvironment:
    """
    这里有两次缓存检查：

    第一次：快速返回已有 environment
    第二次：等待创建锁后，防止重复创建

    构造 LocalEnvironment 时不持有全局 _env_lock，不同 session 可以并行初始化。

    当前构造函数还没有自动 bootstrap；会在缓存接入 terminal 的同一步启用
    """
    with _env_lock:
        environment = _active_environments.get(
            runtime_key
        )

    if environment is not None:
        return environment

    creation_lock = _get_creation_lock(
        runtime_key
    )

    with creation_lock:
        # 等待创建锁期间，其他线程可能已经完成创建
        with _env_lock:
            environment = _active_environments.get(
                runtime_key
            )

        if environment is not None:
            return environment

        environment = LocalEnvironment(
            bash_path,
            cwd=cwd,
            sensitive_env_names=(
                sensitive_env_names
            ),
        )

        with _env_lock:
            _active_environments[
                runtime_key
            ] = environment

        return environment


# 清理单个 session environment
def clear_terminal_environment(
        runtime_key: str | None,
) -> None:
    """
    执行顺序：

    取得该 key 的创建锁
    → 从缓存移除 environment
    → 释放锁
    → 清理快照文件

    暂时不删除 _creation_locks[key]。如果另一个线程仍在等待旧锁，此时删除会让新线程创建第二把锁，破坏“相同 key 只有一个创建者”的保证。
    """
    key = str(runtime_key or "default")
    creation_lock = _get_creation_lock(key)

    with creation_lock:
        with _env_lock:
            environment = (
                _active_environments.pop(
                    key,
                    None,
                )
            )

    if environment is not None:
        environment.cleanup()


def move_terminal_environment(
        source_key: str | None,
        target_key: str | None,
) -> None:
    """
    迁移 compression environment

    它移动的是同一个 LocalEnvironment 对象：

    old-session -> environment A
    变成
    new-session -> environment A

    不会复制包含环境状态的快照。若 target 已有旧对象，则在锁外清理被替换对象。

    这个函数只在 CLI 完成一个 conversation turn 后调用，此时没有并行 terminal handler，因此 _env_lock 下的原子映射迁移足够
    """
    source = str(source_key or "default")
    target = str(target_key or "default")

    if source == target:
        return

    replaced: LocalEnvironment | None = None

    with _env_lock:
        environment = _active_environments.pop(
            source,
            None,
        )

        if environment is None:
            return

        replaced = _active_environments.pop(
            target,
            None,
        )
        _active_environments[target] = environment

    if (
            replaced is not None
            and replaced is not environment
    ):
        replaced.cleanup()


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


def _resolve_workdir(
        raw_workdir: object,
        context: ToolExecutionContext | None,
) -> Path:
    """
    显式 workdir 优先；否则使用当前工具执行上下文的 cwd。
    相对 workdir 也以 context.cwd 为基准。
    """
    base_cwd = (
        context.cwd
        if context is not None
        else Path.cwd()
    ).resolve()

    if raw_workdir is None or raw_workdir == "":
        return base_cwd

    if not isinstance(raw_workdir, str):
        raise ValueError(
            "terminal workdir must be a string"
        )

    workdir = Path(raw_workdir).expanduser()

    if not workdir.is_absolute():
        workdir = base_cwd / workdir

    workdir = workdir.resolve()

    if not workdir.exists():
        raise ValueError(
            f"terminal workdir does not exist: {workdir}"
        )

    if not workdir.is_dir():
        raise ValueError(
            f"terminal workdir is not a directory: {workdir}"
        )

    return workdir


def check_terminal_requirements() -> bool:
    """
    找到并成功探测 Bash：返回 True，Registry 可以向 LLM 暴露 terminal。
    Bash 缺失或所有候选触发明确运行错误：返回 False，Registry 隐藏 terminal。
    不执行用户命令，也不处理 approval。
    """
    try:
        find_bash()
    except RuntimeError:
        return False

    return True


def terminal_tool(
        arguments: dict[str, Any],
) -> dict[str, object]:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return _error_result(
            "terminal requires a non-empty string argument: "
            "command"
        )

    risk = classify_command(command)
    if risk.level == "hardline":
        return _error_result(
            f"Hardline blocked: {risk.description}"
        )

    foreground_error = _foreground_error(command)
    if foreground_error is not None:
        return _error_result(foreground_error)

    config = load_config()
    terminal_config = config["terminal"]

    timeout = arguments.get(
        "timeout",
        terminal_config["timeout_seconds"],
    )
    if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout < 1
            or timeout > 600
    ):
        return _error_result(
            "terminal timeout must be an integer "
            "between 1 and 600"
        )
    # model_tools 已经绑定了 effective context，这里直接读取即可，不要重新创建 context，也不要再次读取 runtime cwd store
    # 这样 terminal 的默认目录和相对 workdir 都基于当前 session cwd
    context = get_current_tool_execution_context()

    try:
        workdir = _resolve_workdir(
            arguments.get("workdir"),
            context,
        )
    except ValueError as exc:
        return _error_result(str(exc))

    try:
        bash = find_bash()
    except RuntimeError as exc:
        return _error_result(
            f"terminal is unavailable: {exc}"
        )

    environment = LocalEnvironment(bash)
    try:
        result = environment.execute(
            command,
            cwd=workdir,
            timeout=timeout,
            max_output_chars=terminal_config[
                "max_output_chars"
            ],
            sensitive_env_names=(
                _provider_secret_env_names(config)
            ),
        )
    except OSError as exc:
        return _error_result(
            f"Failed to start terminal command: {exc}"
        )
    except Exception as exc:
        return _error_result(
            f"Terminal command failed: {exc}"
        )
    # 不要根据 exit_code == 0 判断。即使命令最终失败，前面的 cd 仍可能已改变 shell cwd；
    # 只有 timeout 或 marker 缺失时 result_cwd 才是 None，此时不会更新记录
    result_cwd = result.get("cwd")
    if (
            context is not None
            and isinstance(result_cwd, str)
            and result_cwd
    ):
        record_session_cwd(
            context.runtime_key,
            result_cwd,
        )

    return {
        "output": str(result.get("output", "")),
        "exit_code": int(
            result.get("returncode", -1)
        ),
        "error": None,
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="terminal",
            description=(
                "Execute one Bash command on the local host "
                "in the foreground. Background processes and "
                "interactive input are not supported."
            ),
            parameters=TERMINAL_PARAMETERS,
            handler=terminal_tool,
            toolset="terminal",
            check_fn=check_terminal_requirements,
        )
    )
