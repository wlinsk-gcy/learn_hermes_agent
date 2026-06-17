from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from learn_hermes_agent.agent.tool_context import ToolExecutionContext

RiskLevel = Literal["safe", "dangerous", "hardline"]
ApprovalStatus = Literal["allowed", "approval_required", "blocked"]


@dataclass(frozen=True)
class CommandRisk:
    level: RiskLevel
    pattern_key: str
    description: str


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    status: ApprovalStatus
    reason: str
    command: str
    pattern_key: str = ""
    risk_level: RiskLevel = "safe"
    approved_by_policy: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "status": self.status,
            "reason": self.reason,
            "command": self.command,
            "pattern_key": self.pattern_key,
            "risk_level": self.risk_level,
            "approved_by_policy": self.approved_by_policy,
        }


HARDLINE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # 匹配删除根目录的命令, 例如：rm -rf /
    ("rm_root", "delete filesystem root", re.compile(r"\brm\s+-[^\n]*r[^\n]*f[^\n]*\s+/(?:\s|$)", re.IGNORECASE)),
    # 匹配 PowerShell 递归删除 Windows 盘根目录，例如：Remove-Item -Recurse C:\ 或者 Remove-Item -r D:\
    ("windows_remove_root", "delete Windows drive root",
     re.compile(r"\bremove-item\b[^\n]*(?:-recurse|-r)[^\n]*(?:c:\\|[a-z]:\\)(?:\s|$)", re.IGNORECASE)),
    # 匹配格式化 Windows 盘符，例如：format C:
    ("format_drive", "format a disk or drive", re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE)),
    # 匹配 Linux 创建文件系统命令，例如： mkfs /dev/sda 或者 mkfs.ext4 /dev/sdb1
    ("mkfs", "create filesystem on a device", re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.IGNORECASE)),
    # 匹配 dd 直接写入磁盘设备，例如：dd if=image.iso of=/dev/sda 或者 dd if=x of=/dev/nvme0n1
    ("dd_disk", "write raw bytes to a disk device",
     re.compile(r"\bdd\b[^\n]*\bof=/dev/(?:sd[a-z]|nvme\d+n\d+|disk\d+)", re.IGNORECASE)),
)

DANGEROUS_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # 匹配命令开头或 shell 分隔符后的 sudo，例如：sudo reboot 或者 echo ok; sudo apt install x ；提权命令默认需要审批
    ("sudo", "runs with elevated privileges", re.compile(r"(^|[;&|]\s*)sudo\b", re.IGNORECASE)),
    # 匹配递归删除，例如：rm -r build 或者 Remove-Item -Recurse dist 递归删除可能合理，但有破坏性，所以进入审批流。
    ("recursive_delete", "recursive delete", re.compile(r"\b(rm|remove-item)\b[^\n]*(?:-r|-recurse)", re.IGNORECASE)),
    # 匹配递归修改权限，例如： chmod -R 777 . 可能破坏权限边界或暴露文件，需审批。
    ("chmod_recursive", "recursive permission change", re.compile(r"\bchmod\b[^\n]*-R\b", re.IGNORECASE)),
    # 匹配递归修改 owner，例如：chown -R root:root . 可能破坏项目权限，需审批。
    ("chown_recursive", "recursive ownership change", re.compile(r"\bchown\b[^\n]*-R\b", re.IGNORECASE)),
    # 匹配修改 PowerShell 执行策略，例如：Set-ExecutionPolicy Unrestricted；这是系统级脚本执行安全策略变更，需审批。
    ("powershell_execution_policy", "changes PowerShell execution policy",
     re.compile(r"\bset-executionpolicy\b", re.IGNORECASE)),
    # 匹配下载内容后执行脚本的常见形态，例如：curl https://x/install.sh | sh 或者 wget https://x/install.sh | bash 或者 irm https://x | iex
    # 远程脚本直接执行风险高，需审批。当前是粗略匹配，后续接 terminal 前可再精细化，避免误报普通 curl ... 文本。
    ("curl_pipe_shell", "downloads and executes a script",
     re.compile(r"\b(curl|wget)\b[^\n]*(\||iex|invoke-expression|sh\b|bash\b)", re.IGNORECASE)),
)


def classify_command(command: str) -> CommandRisk:
    normalized = command.strip()
    if not normalized:
        return CommandRisk("dangerous", "empty", "empty command")

    for pattern_key, description, pattern in HARDLINE_PATTERNS:
        if pattern.search(normalized):
            return CommandRisk("hardline", pattern_key, description)

    for pattern_key, description, pattern in DANGEROUS_PATTERNS:
        if pattern.search(normalized):
            return CommandRisk("dangerous", pattern_key, description)

    return CommandRisk("safe", "", "no risky pattern detected")


def check_command_approval(command: str, context: ToolExecutionContext) -> ApprovalDecision:
    risk = classify_command(command)

    if risk.level == "hardline":
        return ApprovalDecision(
            approved=False,
            status="blocked",
            reason=f"Hardline blocked: {risk.description}",
            command=command,
            pattern_key=risk.pattern_key,
            risk_level=risk.level,
        )

    if risk.level == "safe":
        return ApprovalDecision(
            approved=True,
            status="allowed",
            reason=risk.description,
            command=command,
            risk_level=risk.level,
        )

    if context.yolo_enabled or context.approval_mode == "auto":
        return ApprovalDecision(
            approved=True,
            status="allowed",
            reason=f"Approved by policy: {risk.description}",
            command=command,
            pattern_key=risk.pattern_key,
            risk_level=risk.level,
            approved_by_policy=True,
        )

    if context.approval_mode == "deny":
        return ApprovalDecision(
            approved=False,
            status="blocked",
            reason=f"Denied by policy: {risk.description}",
            command=command,
            pattern_key=risk.pattern_key,
            risk_level=risk.level,
        )

    return ApprovalDecision(
        approved=False,
        status="approval_required",
        reason=f"Approval required: {risk.description}",
        command=command,
        pattern_key=risk.pattern_key,
        risk_level=risk.level,
    )
