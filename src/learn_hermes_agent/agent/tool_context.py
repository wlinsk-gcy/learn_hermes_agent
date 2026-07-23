from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

ApprovalMode = Literal["ask", "auto", "deny"]


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str | None = None
    task_id: str = field(default_factory=lambda: str(uuid4()))
    cwd: Path = field(default_factory=Path.cwd)
    workspace_root: Path = field(default_factory=Path.cwd)
    approval_mode: ApprovalMode = "ask"
    # 当前这一次工具执行上下文是否开启“跳过普通审批”的模式。但hardline block就算是yolo_enabled=True也不应该被跳过
    yolo_enabled: bool = False

    @property
    def runtime_key(self) -> str:
        """session_id 必须优先，因为 interactive CLI 每轮都会创建新 task_id，但同一会话的 session_id 不变"""
        return self.session_id or self.task_id or "default"


# _CURRENT_TOOL_CONTEXT 用来让工具 handler 知道“当前这次工具调用属于哪个 session、在哪个 cwd、使用什么审批配置”。临时保存“当前正在执行的 context”。
# ContextVar 避免未来并发 session 共用普通全局变量
_CURRENT_TOOL_CONTEXT: ContextVar[
    ToolExecutionContext | None
    ] = ContextVar(
    "learn_hermes_current_tool_context",
    default=None,
)
"""
可以把 ContextVar 理解为：变量名字只有一个，但每个并发执行上下文都有自己的值。
# 需要注意：ContextVar 只负责隔离“当前值”，不负责让共享对象自动线程安全。这里的 ToolExecutionContext 是 frozen dataclass，不会被 handler修改，正好适合放入 ContextVar。

### 假设用普通全局变量：
current_context = None
两个 session 并发运行：
Session A：current_context = A
Session A：暂停，等待工具执行
Session B：current_context = B
Session B：暂停
Session A：恢复并读取 current_context
结果读到 B
因为两个 session 修改的是同一个全局值。

### ContextVar 的行为
_CURRENT_TOOL_CONTEXT = ContextVar(
  "current_tool_context",
  default=None,
)
并发时逻辑上类似：
Session A 的执行上下文：
_CURRENT_TOOL_CONTEXT = A
Session B 的执行上下文：
_CURRENT_TOOL_CONTEXT = B
虽然 _CURRENT_TOOL_CONTEXT 对象是全局创建的，但它保存的值按执行上下文隔离：
_CURRENT_TOOL_CONTEXT.get()
Session A 调用时得到 A，Session B 调用时得到 B。

### 为什么不用 threading.local

threading.local() 主要按线程隔离。
但 asyncio 中多个任务可能运行在同一个线程：
主线程
├── asyncio Task A
└── asyncio Task B
ContextVar 不仅能区分线程，也能区分同一线程中的不同异步任务，更适合未来的 Gateway、ACP 和并发工具调用。
"""

"""
@contextmanager 会把：
- yield 前面的代码作为 with 入口。
- yield 所在位置交给 with 内部代码执行。
- finally 作为 with 退出清理。

这里 reset(token) 恢复的是进入前的旧 context，不一定是 None，所以嵌套调用也能正确恢复外层 context。
"""
@contextmanager
def bind_tool_execution_context(
        context: ToolExecutionContext | None,
) -> Iterator[None]:
    """临时绑定当前工具调用的 context"""
    token = _CURRENT_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        # 保证正常返回或抛异常后都恢复旧 context
        _CURRENT_TOOL_CONTEXT.reset(token)


def get_current_tool_execution_context() -> ToolExecutionContext | None:
    """ handle_function_call里面的 handler 可以通过 get_current_tool_execution_context() 读取 TOOL_CONTEXT"""
    return _CURRENT_TOOL_CONTEXT.get()


def normalize_approval_mode(value: object) -> ApprovalMode:
    if isinstance(value, str):
        mode = value.strip().lower()
        if mode == "ask":
            return "ask"
        if mode == "auto":
            return "auto"
        if mode == "deny":
            return "deny"
    return "ask"


def create_tool_execution_context(
        config: dict,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        cwd: Path | str | None = None,
) -> ToolExecutionContext:
    """
    这是后续 AIAgent -> model_tools -> ToolRegistry 传递审批状态的最小上下文对象；先做不可变 dataclass，避免不同 session 的审批/yolo 状态共享或被工具 handler 临时改写。
    """
    security = config.get("security")
    if not isinstance(security, dict):
        security = {}
    # expanduser()：例如Path("~/project").expanduser()=C:\Users\admin\project 或 /home/admin/project
    cwd_path = Path(cwd).expanduser() if cwd is not None else Path.cwd()
    if not cwd_path.is_absolute():
        cwd_path = Path.cwd() / cwd_path
    # resolve() 是把路径规范化成一个明确的绝对路径。
    cwd_path = cwd_path.resolve()

    raw_root = security.get("workspace_root", ".")
    root_path = Path(str(raw_root)).expanduser()
    if not root_path.is_absolute():
        root_path = cwd_path / root_path
    root_path = root_path.resolve()

    return ToolExecutionContext(
        session_id=session_id,
        task_id=task_id or str(uuid4()),
        cwd=cwd_path,
        workspace_root=root_path,
        approval_mode=normalize_approval_mode(security.get("approval_mode")),
        yolo_enabled=security.get("yolo") is True,
    )
