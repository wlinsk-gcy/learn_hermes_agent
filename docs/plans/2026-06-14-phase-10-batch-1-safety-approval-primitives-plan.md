# Phase 10 Batch 1 Safety Approval Primitives Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the minimal safety and approval primitives for Phase 10 without opening terminal execution or file mutation tools yet.

**Architecture:** Add a small `ToolExecutionContext` that carries session/task/workspace/security policy into tool dispatch. Add pure command approval and file path safety evaluators, then thread the context through `AIAgent -> model_tools -> ToolRegistry dispatch` so future high-risk tools can share one preflight path.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`, `pathlib`, `re`, current `ToolRegistry`, current `AIAgent`, current argparse CLI. Per project rules, this plan uses compile/manual validation instead of default test files.

---

## Context

Reference design:

- `docs/plans/2026-06-14-phase-10-safety-approval-design.md`
- Hermes source alignment:
  - `D:\python-develop\project\hermes-agent\tools\approval.py`
  - `D:\python-develop\project\hermes-agent\agent\file_safety.py`
  - `D:\python-develop\project\hermes-agent\agent\tool_executor.py`
  - `D:\python-develop\project\hermes-agent\tools\terminal_tool.py`

Project constraints:

- Do not modify `D:\python-develop\project\hermes-agent`.
- Do not add tests unless the user explicitly asks.
- Do not implement `terminal`, `write_file`, `patch`, or checkpoint in Batch 1.
- Implementation code should normally be given to the user as snippets unless they explicitly ask Codex to edit code files.

## Acceptance Criteria

- `uv run python -m compileall -q src` passes.
- `uv run learn-hermes-agent tools` still lists current tools.
- `uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"` still works.
- Manual command policy checks return:
  - `echo hello` -> `allowed`
  - `sudo reboot` -> `approval_required` under default `ask`
  - `rm -rf /` -> `blocked`
- Manual file policy checks return:
  - `README.md` read/write path under workspace -> `allowed`
  - `.env`, `.ssh/id_rsa`, `.learn_hermes/config.yaml` write paths -> `blocked`
- `doctor` prints normalized security config without leaking secrets.

## Task 1: Add Tool Execution Context

**Files:**

- Create: `src/learn_hermes_agent/agent/tool_context.py`

**Purpose:**

Carry the runtime identity and safety policy into tool dispatch. Hermes uses task/session identity heavily for approval state and environment isolation; the learning project only needs a small immutable context first.

**Implementation:**

Create:

```python
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
    yolo_enabled: bool = False


def normalize_approval_mode(value: object) -> ApprovalMode:
    if isinstance(value, str):
        mode = value.strip().lower()
        if mode in {"ask", "auto", "deny"}:
            return mode  # type: ignore[return-value]
    return "ask"


def create_tool_execution_context(
    config: dict,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    cwd: Path | str | None = None,
) -> ToolExecutionContext:
    security = config.get("security")
    if not isinstance(security, dict):
        security = {}

    cwd_path = Path(cwd).expanduser() if cwd is not None else Path.cwd()
    if not cwd_path.is_absolute():
        cwd_path = Path.cwd() / cwd_path
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
        yolo_enabled=bool(security.get("yolo", False)),
    )
```

**Validation:**

Run:

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; c=create_tool_execution_context({'security': {'approval_mode': 'deny', 'workspace_root': '.'}}, session_id='s1'); print(c.session_id, c.approval_mode, c.workspace_root.is_absolute())"
```

Expected:

```text
s1 deny True
```

## Task 2: Normalize Security Config

**Files:**

- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Purpose:**

Make safety behavior explicit and visible. Batch 1 needs only `approval_mode`, `yolo`, and `workspace_root`; terminal/checkpoint config comes later.

**Implementation:**

In `DEFAULT_CONFIG`, add:

```python
"security": {
    "approval_mode": "ask",
    "yolo": False,
    "workspace_root": ".",
},
```

In `_normalize_config()`, after compression normalization, add:

```python
    security_config = config.get("security")
    if not isinstance(security_config, dict):
        security_config = {}

    approval_mode = security_config.get("approval_mode")
    if isinstance(approval_mode, str):
        approval_mode = approval_mode.strip().lower()
    if approval_mode not in {"ask", "auto", "deny"}:
        approval_mode = DEFAULT_CONFIG["security"]["approval_mode"]

    yolo = security_config.get("yolo")
    if not isinstance(yolo, bool):
        yolo = DEFAULT_CONFIG["security"]["yolo"]

    workspace_root = security_config.get("workspace_root")
    if workspace_root:
        workspace_root = str(workspace_root).strip()
    else:
        workspace_root = DEFAULT_CONFIG["security"]["workspace_root"]

    config["security"] = {
        "approval_mode": approval_mode,
        "yolo": yolo,
        "workspace_root": workspace_root,
    }
```

In `run_doctor()`, print:

```python
    security = config["security"]
    print(f"security_approval_mode: {security['approval_mode']}")
    print(f"security_yolo: {security['yolo']}")
    print(f"security_workspace_root: {security['workspace_root']}")
```

**Validation:**

Run:

```powershell
uv run learn-hermes-agent doctor
```

Expected output includes:

```text
security_approval_mode: ask
security_yolo: False
security_workspace_root: .
```

## Task 3: Add Command Approval Primitives

**Files:**

- Create: `src/learn_hermes_agent/tools/approval.py`

**Purpose:**

Mirror Hermes' central command guard in a minimal form. Hardline destructive commands are always blocked. Ordinary dangerous commands return `approval_required` under default ask mode. Safe commands are allowed.

**Implementation:**

Create:

```python
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
    ("rm_root", "delete filesystem root", re.compile(r"\brm\s+-[^\n]*r[^\n]*f[^\n]*\s+/(?:\s|$)")),
    ("windows_remove_root", "delete Windows drive root", re.compile(r"\bremove-item\b[^\n]*(?:-recurse|-r)[^\n]*(?:c:\\|[a-z]:\\)(?:\s|$)", re.IGNORECASE)),
    ("format_drive", "format a disk or drive", re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE)),
    ("mkfs", "create filesystem on a device", re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.IGNORECASE)),
    ("dd_disk", "write raw bytes to a disk device", re.compile(r"\bdd\b[^\n]*\bof=/dev/(?:sd[a-z]|nvme\d+n\d+|disk\d+)", re.IGNORECASE)),
)

DANGEROUS_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("sudo", "runs with elevated privileges", re.compile(r"(^|[;&|]\s*)sudo\b", re.IGNORECASE)),
    ("recursive_delete", "recursive delete", re.compile(r"\b(rm|remove-item)\b[^\n]*(?:-r|-recurse)", re.IGNORECASE)),
    ("chmod_recursive", "recursive permission change", re.compile(r"\bchmod\b[^\n]*-R\b", re.IGNORECASE)),
    ("chown_recursive", "recursive ownership change", re.compile(r"\bchown\b[^\n]*-R\b", re.IGNORECASE)),
    ("powershell_execution_policy", "changes PowerShell execution policy", re.compile(r"\bset-executionpolicy\b", re.IGNORECASE)),
    ("curl_pipe_shell", "downloads and executes a script", re.compile(r"\b(curl|wget)\b[^\n]*(\||iex|invoke-expression|sh\b|bash\b)", re.IGNORECASE)),
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
```

**Validation:**

Run:

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.tools.approval import check_command_approval; ctx=create_tool_execution_context({}); print(check_command_approval('echo hello', ctx).status); print(check_command_approval('sudo reboot', ctx).status); print(check_command_approval('rm -rf /', ctx).status)"
```

Expected:

```text
allowed
approval_required
blocked
```

## Task 4: Add File Path Safety Primitives

**Files:**

- Create: `src/learn_hermes_agent/agent/file_safety.py`

**Purpose:**

Build the shared read/write path policy before adding file tools. This mirrors Hermes' `agent/file_safety.py` at a smaller scale.

**Implementation:**

Create:

```python
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

SENSITIVE_WRITE_PARTS = {
    ".git",
}

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


def _common_path_block(path: str, resolved: Path, context: ToolExecutionContext, operation: PathOperation) -> PathDecision | None:
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
```

**Validation:**

Run:

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path; ctx=create_tool_execution_context({}); print(check_read_path('README.md', ctx).status); print(check_write_path('.env', ctx).status); print(check_write_path('.ssh/id_rsa', ctx).status); print(check_write_path('.learn_hermes/config.yaml', ctx).status)"
```

Expected:

```text
allowed
blocked
blocked
blocked
```

## Task 5: Thread Context Through Tool Dispatch

**Files:**

- Modify: `src/learn_hermes_agent/model_tools.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Purpose:**

Create the execution path that future high-risk tools will use. Existing tools should behave the same because none of them are in the high-risk preflight set.

**Implementation in `model_tools.py`:**

Add imports:

```python
from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.tools.approval import check_command_approval
```

Change signatures:

```python
def handle_function_call(
    name: str,
    arguments_json: str | dict[str, Any] | None = None,
    *,
    registry: ToolRegistry | None = None,
    context: ToolExecutionContext | None = None,
) -> str:
```

```python
def safe_handle_function_call(
    name: str,
    arguments_json: str | dict[str, Any] | None = None,
    *,
    registry: ToolRegistry | None = None,
    context: ToolExecutionContext | None = None,
) -> str:
```

Inside `handle_function_call`, keep lookup first, then parse args, then preflight:

```python
    target = registry or get_default_registry()
    entry = target.get(name)
    arguments = _parse_arguments(arguments_json)

    preflight_error = _preflight_tool_call(name, arguments, context)
    if preflight_error is not None:
        return json.dumps(preflight_error, ensure_ascii=False)

    result = entry.handler(arguments)
    return json.dumps(result, ensure_ascii=False)
```

Update `safe_handle_function_call` to pass `context=context`.

Add helper:

```python
def _preflight_tool_call(
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext | None,
) -> dict[str, object] | None:
    if context is None:
        return None

    if name == "terminal":
        command = arguments.get("command")
        if not isinstance(command, str):
            return {"error": "terminal requires a string argument: command"}
        decision = check_command_approval(command, context)
        if decision.approved:
            return None
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    if name == "read_file":
        path = arguments.get("path")
        if not isinstance(path, str):
            return {"error": "read_file requires a string argument: path"}
        decision = check_read_path(path, context)
        if decision.allowed:
            return None
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    if name in {"write_file", "patch"}:
        path = arguments.get("path")
        if not isinstance(path, str):
            return {"error": f"{name} requires a string argument: path"}
        decision = check_write_path(path, context)
        if decision.allowed:
            return None
        payload = decision.to_dict()
        payload["error"] = decision.reason
        return payload

    return None
```

**Implementation in `agent/core.py`:**

Add import:

```python
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
```

Change `run_conversation` signature:

```python
    def run_conversation(
        self,
        user_input: str,
        *,
        history: Sequence[ChatMessage] | None = None,
        system_prompt: str | None = None,
        tool_context: ToolExecutionContext | None = None,
    ) -> list[ChatMessage]:
```

Pass context into tool dispatch:

```python
                result_json = safe_handle_function_call(
                    function_name,
                    arguments,
                    registry=self.registry,
                    context=tool_context,
                )
```

**Implementation in `cli/main.py`:**

Add import:

```python
from learn_hermes_agent.agent.tool_context import create_tool_execution_context
```

In one-shot `run_chat`, after `session_id` is created:

```python
    tool_context = create_tool_execution_context(config, session_id=session_id)
```

Pass:

```python
        messages = agent.run_conversation(
            message,
            system_prompt=system_prompt,
            tool_context=tool_context,
        )
```

In interactive loop, before each `agent.run_conversation(...)` call:

```python
        tool_context = create_tool_execution_context(config, session_id=session_id)
```

Pass `tool_context=tool_context`.

**Validation:**

Run:

```powershell
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

Expected:

- Existing tool demo still returns user -> assistant(tool_calls) -> tool -> assistant.
- No new security output appears because `echo` is not a high-risk tool.

## Task 6: Add Manual Policy Validation Commands

**Files:**

- No code changes required.

**Purpose:**

Because project rules say not to add tests by default, use one-line Python commands to observe the policy behavior.

**Validation commands:**

Command approval:

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.tools.approval import check_command_approval; ctx=create_tool_execution_context({}); print(check_command_approval('echo hello', ctx).to_dict()); print(check_command_approval('sudo reboot', ctx).to_dict()); print(check_command_approval('rm -rf /', ctx).to_dict())"
```

Expected:

- First dict has `"status": "allowed"`.
- Second dict has `"status": "approval_required"`.
- Third dict has `"status": "blocked"` and `"risk_level": "hardline"`.

File path safety:

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path; ctx=create_tool_execution_context({}); print(check_read_path('README.md', ctx).to_dict()); print(check_write_path('.env', ctx).to_dict()); print(check_write_path('.ssh/id_rsa', ctx).to_dict()); print(check_write_path('.learn_hermes/config.yaml', ctx).to_dict())"
```

Expected:

- `README.md` read is allowed.
- `.env`, `.ssh/id_rsa`, `.learn_hermes/config.yaml` writes are blocked.

Existing CLI behavior:

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{"text":"hello"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

Expected:

- `compileall` exits 0.
- `doctor` prints security config.
- Existing tools and chat behavior remain unchanged.

## Task 7: Update Progress Handoff After Implementation

**Files:**

- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Purpose:**

Keep project handoff current after Batch 1 is implemented and manually verified.

**Handoff content to add:**

- Phase 10 Batch 1 completed.
- Files changed.
- Hermes modules aligned:
  - `tools/approval.py`
  - `agent/file_safety.py`
  - `agent/tool_executor.py`
- Validation commands actually run.
- Explicitly state that terminal/file mutation/checkpoint are still deferred.

**Roadmap update:**

Under Phase 10, mark Batch 1 as completed and list remaining batches:

- Batch 2: file tools with safety
- Batch 3: local terminal backend
- Batch 4: minimal checkpoint

## Implementation Order

1. Task 1: context object.
2. Task 2: config normalization and doctor visibility.
3. Task 3: command approval pure functions.
4. Task 4: file path safety pure functions.
5. Task 5: dispatch context threading.
6. Task 6: manual validation.
7. Task 7: documentation update.

## Suggested Commit Message

```text
feat: add phase 10 safety approval primitives
```

Commit body:

```text
- add tool execution context for session/task security policy
- add minimal command approval and file path safety evaluators
- thread tool context through agent tool dispatch
- expose security config in doctor
- document Phase 10 Batch 1 progress
```

## Notes For Execution

- Do not implement `terminal` in this batch.
- Do not register `read_file`, `write_file`, or `patch` in this batch.
- Do not add tests unless the user explicitly changes the constraint.
- If type checker complains about `Literal` return inference in `normalize_approval_mode`, prefer a small local cast or explicit branch returns over weakening the type to `str`.
- If `.learn_hermes/config.yaml` does not exist, `get_config_path().resolve(strict=False)` is still valid and should still be protected.
