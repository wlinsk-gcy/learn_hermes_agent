# Phase 8 Batch 1 Memory Structure Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add file-backed persistent memory with a manual `memory` tool and system prompt snapshot injection.

**Architecture:** Create `MemoryStore` under `agent/`, register a built-in `memory` tool under `tools/`, and inject a frozen memory snapshot into `PromptBuilder`'s volatile layer. Keep the implementation local and deterministic; do not add external providers, automatic memory writes, Skills, real provider integration, or tests.

**Tech Stack:** Python 3.11+、SQLite session metadata already present、current `ToolRegistry`、current `PromptBuilder`、current fake provider。

---

## Boundary

Implement:

- `.learn_hermes/memories/MEMORY.md`
- `.learn_hermes/memories/USER.md`
- `MemoryStore`
- built-in `memory` tool
- memory tool schema exposure
- memory snapshot injection into new session system prompt
- manual validation commands
- progress docs after user confirms implementation

Do not implement:

- automatic memory writing
- memory provider abstraction
- external memory providers
- prefetch/sync hooks
- compression memory hooks
- Skills
- real LLM provider
- tests

## Task 1: Add Memory Path Config Helper

**Files:**

- Modify: `src/learn_hermes_agent/config.py`

**Step 1: Add helper near `get_state_db_path()`**

```python
def get_memory_dir_path() -> Path:
    return get_app_home() / "memories"
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.config import get_memory_dir_path; print(get_memory_dir_path().name)"
```

Expected:

```text
memories
```

## Task 2: Create `MemoryStore`

**Files:**

- Create: `src/learn_hermes_agent/agent/memory_store.py`

**Step 1: Add module skeleton**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MemoryTarget = Literal["memory", "user"]

ENTRY_SEPARATOR = "\n\n---\n\n"
RISKY_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "you are now",
)
```

**Step 2: Add result dataclass**

```python
@dataclass(frozen=True)
class MemorySnapshot:
    memory: list[str]
    user: list[str]
```

**Step 3: Add `MemoryStore` class**

```python
class MemoryStore:
    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir

    def load(self) -> MemorySnapshot:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        return MemorySnapshot(
            memory=self._read_entries(self._path_for("memory")),
            user=self._read_entries(self._path_for("user")),
        )

    def read(self, target: MemoryTarget) -> dict[str, object]:
        entries = self._read_entries(self._path_for(target))
        return {"success": True, "target": target, "entries": entries}

    def add(self, target: MemoryTarget, content: str) -> dict[str, object]:
        content = self._normalize_content(content)
        self._validate_content(content)
        path = self._path_for(target)
        entries = self._read_entries(path)
        if content not in entries:
            entries.append(content)
            self._write_entries(path, entries)
        return {"success": True, "target": target, "entries": entries}

    def replace(self, target: MemoryTarget, old_text: str, content: str) -> dict[str, object]:
        old_text = old_text.strip()
        content = self._normalize_content(content)
        self._validate_content(content)
        path = self._path_for(target)
        entries = self._read_entries(path)
        index = self._find_unique_match(entries, old_text)
        entries[index] = content
        self._write_entries(path, entries)
        return {"success": True, "target": target, "entries": entries}

    def remove(self, target: MemoryTarget, old_text: str) -> dict[str, object]:
        old_text = old_text.strip()
        path = self._path_for(target)
        entries = self._read_entries(path)
        index = self._find_unique_match(entries, old_text)
        removed = entries.pop(index)
        self._write_entries(path, entries)
        return {"success": True, "target": target, "removed": removed, "entries": entries}
```

**Step 4: Add prompt rendering**

```python
    def system_prompt_block(self) -> str:
        snapshot = self.load()
        memory_entries = self._sanitize_for_prompt(snapshot.memory)
        user_entries = self._sanitize_for_prompt(snapshot.user)

        parts: list[str] = []
        if memory_entries:
            parts.append("MEMORY:\n" + "\n".join(f"- {entry}" for entry in memory_entries))
        if user_entries:
            parts.append("USER:\n" + "\n".join(f"- {entry}" for entry in user_entries))

        if not parts:
            return ""

        return "Persistent memory snapshot:\n\n" + "\n\n".join(parts)
```

**Step 5: Add private helpers**

```python
    def _path_for(self, target: MemoryTarget) -> Path:
        if target == "user":
            return self.memory_dir / "USER.md"
        return self.memory_dir / "MEMORY.md"

    def _read_entries(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        return [entry.strip() for entry in text.split(ENTRY_SEPARATOR) if entry.strip()]

    def _write_entries(self, path: Path, entries: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ENTRY_SEPARATOR.join(entry.strip() for entry in entries if entry.strip())
        path.write_text(text + ("\n" if text else ""), encoding="utf-8")

    def _normalize_content(self, content: str) -> str:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        return content.strip()

    def _validate_content(self, content: str) -> None:
        if not content:
            raise ValueError("content is required")
        if ENTRY_SEPARATOR.strip() in content:
            raise ValueError("content must not contain the memory entry separator")
        lowered = content.lower()
        if any(marker in lowered for marker in RISKY_MARKERS):
            raise ValueError("content contains a possible prompt-injection marker")

    def _find_unique_match(self, entries: list[str], old_text: str) -> int:
        if not old_text:
            raise ValueError("old_text is required")
        matches = [index for index, entry in enumerate(entries) if old_text in entry]
        if not matches:
            raise ValueError("old_text did not match any entry")
        if len(matches) > 1:
            raise ValueError("old_text matched multiple entries; provide a more specific substring")
        return matches[0]

    def _sanitize_for_prompt(self, entries: list[str]) -> list[str]:
        result: list[str] = []
        for entry in entries:
            lowered = entry.lower()
            if any(marker in lowered for marker in RISKY_MARKERS):
                result.append("[BLOCKED: memory entry contained a possible prompt-injection marker.]")
            else:
                result.append(entry)
        return result
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run python -c "from pathlib import Path; import tempfile; from learn_hermes_agent.agent.memory_store import MemoryStore; tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); store=MemoryStore(Path(tmp.name)); print(store.add('memory', 'project uses uv')['success']); print(store.read('memory')['entries']); print('MEMORY:' in store.system_prompt_block()); tmp.cleanup()"
```

Expected:

```text
True
['project uses uv']
True
```

## Task 3: Register Built-In `memory` Tool

**Files:**

- Create: `src/learn_hermes_agent/tools/memory.py`
- Modify: `src/learn_hermes_agent/tools/registry.py`

**Step 1: Create tool module**

```python
from __future__ import annotations

from typing import Any, cast

from learn_hermes_agent.agent.memory_store import MemoryStore, MemoryTarget
from learn_hermes_agent.config import get_memory_dir_path
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry
```

**Step 2: Add schema**

```python
MEMORY_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "read", "replace", "remove"],
            "description": "Memory action to perform.",
        },
        "target": {
            "type": "string",
            "enum": ["memory", "user"],
            "description": "Use 'memory' for agent notes and 'user' for user profile.",
        },
        "content": {
            "type": "string",
            "description": "Entry content. Required for add and replace.",
        },
        "old_text": {
            "type": "string",
            "description": "Unique substring identifying the entry for replace or remove.",
        },
    },
    "required": ["action", "target"],
    "additionalProperties": False,
}
```

**Step 3: Add handler**

```python
def memory(arguments: dict[str, Any]) -> dict[str, object]:
    action = arguments.get("action")
    target = arguments.get("target")

    if action not in {"add", "read", "replace", "remove"}:
        raise ValueError("memory action must be one of: add, read, replace, remove")
    if target not in {"memory", "user"}:
        raise ValueError("memory target must be one of: memory, user")

    typed_target = cast(MemoryTarget, target)
    store = MemoryStore(get_memory_dir_path())

    if action == "read":
        return store.read(typed_target)
    if action == "add":
        return store.add(typed_target, str(arguments.get("content") or ""))
    if action == "replace":
        return store.replace(
            typed_target,
            str(arguments.get("old_text") or ""),
            str(arguments.get("content") or ""),
        )
    return store.remove(typed_target, str(arguments.get("old_text") or ""))
```

**Step 4: Add registration**

```python
def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="memory",
            description=(
                "Save and inspect durable memory across sessions. "
                "Use target='memory' for agent notes and target='user' for user preferences."
            ),
            parameters=MEMORY_PARAMETERS,
            handler=memory,
        )
    )
```

**Step 5: Register from discovery**

In `src/learn_hermes_agent/tools/registry.py`, update `discover_builtin_tools()`:

```python
    from learn_hermes_agent.tools.echo import register_tools as register_echo_tools
    from learn_hermes_agent.tools.memory import register_tools as register_memory_tools
    register_echo_tools(target)
    register_memory_tools(target)
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool memory '{\"action\":\"add\",\"target\":\"memory\",\"content\":\"project uses uv\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"read\",\"target\":\"memory\"}'
```

Expected:

- `tools` includes `memory`.
- add/read return JSON with `"success": true`.

## Task 4: Inject Memory Snapshot Into System Prompt

**Files:**

- Modify: `src/learn_hermes_agent/agent/prompt_builder.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Update PromptBuilder imports**

```python
from learn_hermes_agent.agent.memory_store import MemoryStore
```

**Step 2: Update constructor**

```python
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.project_root = project_root or Path.cwd()
        self.memory_store = memory_store
```

**Step 3: Update volatile layer**

Replace `_build_volatile_layer()` with:

```python
    def _build_volatile_layer(self) -> str:
        if self.memory_store is None:
            return ""
        return self.memory_store.system_prompt_block()
```

**Step 4: Update CLI `build_system_prompt()`**

In `src/learn_hermes_agent/cli/main.py`, add imports:

```python
from learn_hermes_agent.agent.memory_store import MemoryStore
from learn_hermes_agent.config import get_memory_dir_path
```

Then replace:

```python
def build_system_prompt() -> str:
    return PromptBuilder().build()
```

with:

```python
def build_system_prompt() -> str:
    memory_store = MemoryStore(get_memory_dir_path())
    return PromptBuilder(memory_store=memory_store).build()
```

**Manual Validation:**

Use a temporary home so local memory is not polluted:

```powershell
$root=(Get-Location).Path
$tmp=Join-Path $root ('.tmp_phase8_memory_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$env:LEARN_HERMES_HOME=$tmp
uv run learn-hermes-agent call-tool memory '{\"action\":\"add\",\"target\":\"memory\",\"content\":\"project uses uv\"}'
uv run learn-hermes-agent chat "memory prompt smoke"
uv run learn-hermes-agent sessions
uv run learn-hermes-agent show-session <session_id_from_chat>
Remove-Item Env:\LEARN_HERMES_HOME -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $tmp -Recurse -Force
```

Expected:

- `show-session` output includes `session.system_prompt`.
- `session.system_prompt` contains `Persistent memory snapshot` and `project uses uv`.

## Task 5: Validate Replace/Remove And Regression Paths

**Files:**

- No code file changes.

**Manual Validation:**

```powershell
uv run learn-hermes-agent call-tool memory '{\"action\":\"add\",\"target\":\"user\",\"content\":\"prefers concise answers\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"replace\",\"target\":\"user\",\"old_text\":\"concise\",\"content\":\"prefers concise Chinese answers\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"remove\",\"target\":\"user\",\"old_text\":\"Chinese\"}'
uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
uv run learn-hermes-agent chat --resume missing
```

Expected:

- replace updates the matching entry.
- remove deletes the matching entry.
- `echo` still works.
- `chat --resume missing` still returns the existing missing-session error.

## Task 6: Update Progress Docs

**Files:**

- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Step 1: Update roadmap Phase 8 status**

Change Phase 8 status from:

```markdown
**状态：未开始**
```

to:

```markdown
**状态：进行中（Batch 1 Memory 结构层已完成）**
```

Add a short note:

```markdown
当前 Batch 1 已完成：新增 file-backed `MemoryStore`、`memory` tool、`MEMORY.md` / `USER.md` 持久化和 system prompt snapshot 注入。当前只支持手工 tool 调用和新 session 注入；自动记忆、external memory providers、prefetch/sync hooks、Skills 和真实 provider 延后。
```

**Step 2: Append handoff entry**

Append a `2026-06-09 Phase 8 Batch 1 Memory 进度更新` section including:

- 本次目标
- 已完成
- 修改文件
- 对照的 Hermes 源码
- 验证方式
- 设计结论
- 下一步

**Manual Validation:**

```powershell
rg -n "Phase 8|MemoryStore|memory tool|MEMORY.md|USER.md|Persistent memory snapshot" docs
git status --short --untracked-files=all
```

Expected:

- Roadmap marks Phase 8 in progress.
- Handoff has Batch 1 memory entry.
- Git status shows expected code and docs changes.
