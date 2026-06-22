from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.config import get_config_path, get_state_db_path

PathStatus = Literal["allowed", "blocked"]
PathOperation = Literal["read", "write"]


@dataclass(frozen=True)
class PathDecision:
    allowed: bool
    status: PathStatus
    operation: PathOperation
    path: str
    resolved_path: str
    reason: str
    pattern_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "operation": self.operation,
            "path": self.path,
            "resolved_path": self.resolved_path,
            "reason": self.reason,
            "pattern_key": self.pattern_key,
        }


SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "authorized_keys",
    "credentials",
    "credentials.json",
    "auth.json",
}

SENSITIVE_PARTS = {
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
}
# 这里用集合是要直接做值的匹配，用来判断路径片段是否直接命中
SENSITIVE_WRITE_PARTS = {
    ".git",
}
# 因为业务逻辑是用SYSTEM_PREFIXES做前缀匹配，所以用有序、不可变的 tuple 更合适。因为不是检查某一段名字，而是检查路径整体开头
SENSITIVE_SYSTEM_PREFIXES = (
    "/etc",
    "/boot",
    "/usr/lib/systemd",
    "c:\\windows",
    "c:\\program files",
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_workspace_path(path: str, context: ToolExecutionContext) -> Path:
    raw_path = Path(path).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve(strict=False)
    return (context.cwd / raw_path).resolve(strict=False)


def _block(
        *,
        operation: PathOperation,
        path: str,
        resolved: Path,
        reason: str,
        pattern_key: str,
) -> PathDecision:
    return PathDecision(
        allowed=False,
        status="blocked",
        operation=operation,
        path=path,
        resolved_path=str(resolved),
        reason=reason,
        pattern_key=pattern_key,
    )


def _allow(*, operation: PathOperation, path: str, resolved: Path) -> PathDecision:
    return PathDecision(
        allowed=True,
        status="allowed",
        operation=operation,
        path=path,
        resolved_path=str(resolved),
        reason="path allowed",
    )


def _common_path_block(
        path: str,
        resolved: Path,
        context: ToolExecutionContext,
        operation: PathOperation,
) -> PathDecision | None:
    if not path or not str(path).strip():
        return _block(
            operation=operation,
            path=path,
            resolved=resolved,
            reason="path must not be empty",
            pattern_key="empty_path",
        )

    if not _is_relative_to(resolved, context.workspace_root):
        return _block(
            operation=operation,
            path=path,
            resolved=resolved,
            reason="path is outside workspace root",
            pattern_key="outside_workspace",
        )

    parts_lower = {part.lower() for part in resolved.parts}
    name_lower = resolved.name.lower()

    if name_lower in SENSITIVE_FILE_NAMES:
        return _block(
            operation=operation,
            path=path,
            resolved=resolved,
            reason="path targets a sensitive file name",
            pattern_key="sensitive_name",
        )

    if parts_lower & SENSITIVE_PARTS:
        return _block(
            operation=operation,
            path=path,
            resolved=resolved,
            reason="path targets a sensitive credential directory",
            pattern_key="sensitive_directory",
        )

    resolved_lower = str(resolved).lower()
    # 前缀匹配，意思是：完整路径字符串是否以某个系统敏感目录开头。
    if any(resolved_lower.startswith(prefix) for prefix in SENSITIVE_SYSTEM_PREFIXES):
        return _block(
            operation=operation,
            path=path,
            resolved=resolved,
            reason="path targets a sensitive system location",
            pattern_key="system_path",
        )

    return None


def check_read_path(path: str, context: ToolExecutionContext) -> PathDecision:
    resolved = resolve_workspace_path(path, context)
    common = _common_path_block(path, resolved, context, "read")
    if common is not None:
        return common
    return _allow(operation="read", path=path, resolved=resolved)


def check_write_path(path: str, context: ToolExecutionContext) -> PathDecision:
    resolved = resolve_workspace_path(path, context)
    common = _common_path_block(path, resolved, context, "write")
    if common is not None:
        return common

    parts_lower = {part.lower() for part in resolved.parts}
    # 做交集。路径的任意一段只要包含 .git，就阻断写入。
    if parts_lower & SENSITIVE_WRITE_PARTS:
        return _block(
            operation="write",
            path=path,
            resolved=resolved,
            reason="path targets internal VCS metadata",
            pattern_key="vcs_metadata",
        )

    protected_paths = {
        get_config_path().resolve(strict=False),
        get_state_db_path().resolve(strict=False),
    }
    if resolved in protected_paths:
        return _block(
            operation="write",
            path=path,
            resolved=resolved,
            reason="path targets learn-hermes internal state",
            pattern_key="internal_state",
        )

    return _allow(operation="write", path=path, resolved=resolved)
