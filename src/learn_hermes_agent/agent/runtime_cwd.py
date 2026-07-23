from __future__ import annotations

from pathlib import Path
from threading import Lock
from dataclasses import replace
from learn_hermes_agent.agent.tool_context import ToolExecutionContext

# 例如：session-a → D:\project\a
_SESSION_CWDS: dict[str, Path] = {}
# Lock 用来保护字典读写。未来两个 session 可能同时执行： Session A：更新 cwd，Session B：读取 cwd， Lock 保证一次只有一个线程操作 _SESSION_CWDS，避免读取和修改交错产生不一致状态。
_SESSION_CWDS_LOCK = Lock()


def _normalize_session_key(
        session_key: str | None,
) -> str:
    return str(session_key or "default")


def _resolve_existing_directory(
        cwd: Path | str | None,
) -> Path | None:
    """
    负责把输入转换成规范、真实存在的绝对目录。

    例如：_resolve_existing_directory("~/project")
    需要展开~，相对路径转绝对路径，解析路径中的.或者..
    检查是否为真实目录
    """
    if cwd is None:
        return None

    try:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    if not path.is_dir():
        return None

    return path


def record_session_cwd(
        session_key: str | None,
        cwd: Path | str | None,
) -> None:
    """记录某个 session 当前所在目录"""
    resolved = _resolve_existing_directory(cwd)
    if resolved is None:
        return

    key = _normalize_session_key(session_key)
    with _SESSION_CWDS_LOCK:
        _SESSION_CWDS[key] = resolved


def get_session_cwd(
        session_key: str | None,
) -> Path | None:
    """读取 session 已记录的目录"""
    key = _normalize_session_key(session_key)
    with _SESSION_CWDS_LOCK:
        return _SESSION_CWDS.get(key)


def clear_session_cwd(
        session_key: str | None,
) -> None:
    """
    删除 session 的 cwd 状态

    主要用于：

    - 用户执行 /new。
    - session 被删除。
    - session 生命周期结束。
    - 避免废弃 session 一直占用内存。
    """
    key = _normalize_session_key(session_key)
    with _SESSION_CWDS_LOCK:
        _SESSION_CWDS.pop(key, None)


def copy_session_cwd(
        source_key: str | None,
        target_key: str | None,
) -> None:
    """
    把旧 session 的 cwd 复制给新 session

    主要用于 context compression：

    old session 因压缩结束
    → 创建 continuation session
    → 新 session 应继续停留在原来的目录

    调用顺序将是：

    copy_session_cwd(old_id, new_id)
    clear_session_cwd(old_id)
    """
    source = _normalize_session_key(source_key)
    target = _normalize_session_key(target_key)

    with _SESSION_CWDS_LOCK:
        cwd = _SESSION_CWDS.get(source)
        if cwd is not None:
            _SESSION_CWDS[target] = cwd


def resolve_tool_execution_context(
        context: ToolExecutionContext | None,
) -> ToolExecutionContext | None:
    # context is None 时保持兼容
    if context is None:
        return None

    key = context.runtime_key
    # session 第一次执行时，用 context.cwd 初始化记录
    initial_cwd = _resolve_existing_directory(
        context.cwd
    )
    # 初始化查询和写入放在同一个 lock 中，避免同一 session 并发初始化时产生竞态
    with _SESSION_CWDS_LOCK:
        recorded_cwd = _SESSION_CWDS.get(key)
        # 后续执行优先使用 session 已记录 cwd
        if recorded_cwd is None and initial_cwd is not None:
            recorded_cwd = initial_cwd
            _SESSION_CWDS[key] = recorded_cwd

    if (
            recorded_cwd is None
            or recorded_cwd == context.cwd
    ):
        return context
    # replace() 创建新的 frozen dataclass，不修改原 context
    # 把context的cwd替换成recorded_cwd，然后创建一个新的对象出来，这里只能用replace，因为ToolExecutionContext是froze=True的
    return replace(
        context,
        cwd=recorded_cwd, # 只替换cwd，其他信息不变
    )
