# Phase 7 Batch 4 Lineage Observability Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add minimal CLI observability for compression session lineage.

**Architecture:** Extend `SessionStore` with read-only lineage helpers, then make `show-session` output session metadata, compression tip, lineage, and messages in one JSON object. Do not change chat persistence, compression behavior, provider behavior, or session list projection.

**Tech Stack:** Python 3.11+、SQLite、当前 `SessionStore`、当前 argparse CLI。

---

## Boundary

Implement:

- `SessionStore.get_session_chain(session_id)`
- `SessionStore.get_compression_tip(session_id)`
- richer `show-session <session_id>` JSON output
- manual validation commands
- progress docs after user confirms implementation

Do not implement:

- compression lock
- resume command
- session list projection
- real provider usage
- LLM summary
- auxiliary compression model
- memory hooks
- gateway projection
- tests

## Task 1: Add SessionStore Lineage Helpers

**Files:**

- Modify: `src/learn_hermes_agent/state/session_db.py`

**Step 1: Add `get_session_chain()`**

Add this method near `get_session()` / `list_sessions()`:

```python
    def get_session_chain(self, session_id: str) -> list[dict[str, Any]]:
        """Return parent lineage from root to the requested session."""
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current_id: str | None = session_id

        while current_id and current_id not in seen:
            seen.add(current_id)
            session = self.get_session(current_id)
            if session is None:
                break

            chain.append(session)
            parent_id = session.get("parent_session_id")
            current_id = str(parent_id) if parent_id else None

        chain.reverse()
        return chain
```

**Step 2: Add `get_compression_tip()`**

Add this method after `get_session_chain()`:

```python
    def get_compression_tip(self, session_id: str) -> str:
        """Return the latest compression continuation session id."""
        current_id = session_id
        seen: set[str] = set()

        while current_id not in seen:
            seen.add(current_id)
            current_session = self.get_session(current_id)
            if current_session is None:
                return current_id

            if current_session.get("end_reason") != "compression":
                return current_id

            child_id = self._get_latest_child_session_id(current_id)
            if child_id is None:
                return current_id

            current_id = child_id

        return current_id
```

**Step 3: Add private child lookup helper**

Add this private helper near `_connect()`:

```python
    def _get_latest_child_session_id(self, parent_session_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select id
                from sessions
                where parent_session_id = ?
                order by created_at desc, id desc
                limit 1
                """,
                (parent_session_id,),
            ).fetchone()

        if row is None:
            return None
        return str(row["id"])
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run python -c "from pathlib import Path; import tempfile; from learn_hermes_agent.state.session_db import SessionStore; tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); s=SessionStore(Path(tmp.name)/'state.db'); s.initialize(); p=s.create_session(title='parent'); s.end_session(p, 'compression'); c=s.create_session(title='child', parent_session_id=p); print(s.get_compression_tip(p) == c); print([row['id'] for row in s.get_session_chain(c)] == [p, c]); tmp.cleanup()"
```

Expected:

```text
True
True
```

## Task 2: Make `show-session` Show Metadata + Lineage

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Replace `run_show_session()` body**

Replace current body with:

```python
def run_show_session(session_id: str) -> int:
    store = get_session_store()
    session = store.get_session(session_id)
    if session is None:
        print(f"error: session not found: {session_id}", file=sys.stderr)
        return 1

    messages = store.get_session_messages(session_id)
    payload = {
        "session": {
            **session,
            "message_count": len(messages),
        },
        "compression_tip": store.get_compression_tip(session_id),
        "lineage": store.get_session_chain(session_id),
        "messages": messages,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent show-session <known_session_id>
uv run learn-hermes-agent show-session missing-session-id
```

Expected:

- Existing session returns JSON object with `session`, `compression_tip`, `lineage`, `messages`.
- Missing session returns non-zero exit code and stderr error.

## Task 3: Validate With A Real Compression Split

**Files:**

- No code file changes.

**Manual Validation:**

Use a temporary config to force compression:

```powershell
$root=(Get-Location).Path
$tmp=Join-Path $root ('.tmp_phase7_batch4_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
@'
model:
  provider: fake
  default: fake-lineage

agent:
  max_iterations: 3

compression:
  enabled: true
  context_length: 40
  threshold: 0.5
  protect_first_n: 1
  protect_last_n: 2
'@ | Set-Content -Path (Join-Path $tmp 'config.yaml') -Encoding UTF8
$env:LEARN_HERMES_HOME=$tmp
$long='x'*120
@("one $long","two $long","three $long","/sessions","/q") | uv run learn-hermes-agent chat
```

Then inspect both parent and child ids from the output:

```powershell
uv run learn-hermes-agent show-session <parent_session_id>
uv run learn-hermes-agent show-session <child_session_id>
```

Expected:

- Parent `session.end_reason` is `compression`.
- Parent `compression_tip` equals child id.
- Child `lineage` contains parent then child.
- Child `messages` include the `[Context compression summary]` message.

Cleanup:

```powershell
Remove-Item -LiteralPath $tmp -Recurse -Force
```

## Task 4: Update Progress Docs

**Files:**

- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Step 1: Update roadmap Phase 7 status**

Change:

```markdown
**状态：进行中（Batch 1-3 已完成）**
```

to:

```markdown
**状态：已完成（最小版）**
```

Add a short Batch 4 completion note after the Batch 3 note:

```markdown
当前 Batch 4 已完成：新增 session lineage 可观察性，`show-session` 可输出 session metadata、compression tip、lineage 和 messages。Phase 7 最小版至此收口；compression lock、真实 provider usage、LLM summary、gateway projection、resume redirect、memory hooks 延后。
```

**Step 2: Append handoff entry**

Append a `2026-06-09 Phase 7 Batch 4 进度更新` section including:

- 本次目标
- 已完成
- 修改文件
- 对照的 Hermes 源码
- 验证方式
- 设计结论
- 下一步

**Manual Validation:**

```powershell
rg -n "Phase 7|Batch 4|compression_tip|lineage|已完成（最小版）" docs
git status --short --untracked-files=all
```

Expected:

- Roadmap says Phase 7 minimal version is complete.
- Handoff has Batch 4 entry.
- Git status shows only user code changes plus docs changes.

