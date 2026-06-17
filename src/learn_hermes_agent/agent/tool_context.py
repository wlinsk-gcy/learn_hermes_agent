from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

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
