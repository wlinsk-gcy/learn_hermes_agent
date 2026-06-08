# Phase 7 Batch 3 Session Split Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 context compression 增加最小 session lineage，让压缩后的工作消息进入 child session，而不是覆盖原 session。

**Architecture:** `SessionStore` 增加 `parent_session_id`、`end_reason`、`ended_at` 三个字段。压缩触发时，CLI 结束旧 session，创建 child session，并把 compressed messages 写入 child session。当前批次只做 CLI 内部 session split，不做 gateway projection、resume tip、compression lock、memory hooks。

**Tech Stack:** Python 3.11+、SQLite、当前 `SessionStore`、当前 `AIAgent.last_context_compressed`。

---

## 对照的真实 Hermes 源码

- `agent/conversation_compression.py`
  - 压缩成功后结束旧 session。
  - 创建新的 child session。
  - 更新当前 `agent.session_id`。
  - 新 session 写入 compressed messages。
- `hermes_state.py`
  - `sessions.parent_session_id` 表示 session lineage。
  - `end_reason='compression'` 区分压缩结束和普通结束。
  - 真实项目还有 `compression_locks` 防并发压缩竞争。

## Batch 3 边界

实现：

- `sessions.parent_session_id`
- `sessions.end_reason`
- `sessions.ended_at`
- `SessionStore.create_session(parent_session_id=...)`
- `SessionStore.end_session(session_id, reason)`
- `SessionStore.list_sessions()` 输出 parent/end 信息
- 交互模式压缩时：
  - `end_session(old_session_id, "compression")`
  - `create_session(parent_session_id=old_session_id)`
  - `append_messages(child_session_id, messages)`
  - 更新当前 `session_id`

暂不实现：

- compression lock
- gateway session projection
- resume 自动跳到 compression tip
- memory hooks
- system prompt rebuild
- 真实 provider usage
- `/compress` 手动命令

## Task 1: SessionStore Schema

**Files:**

- Modify: `src/learn_hermes_agent/state/session_db.py`

**Steps:**

1. 在 `sessions` 建表 SQL 中增加：
   - `parent_session_id text`
   - `end_reason text`
   - `ended_at text`
2. 在初始化后用 `_ensure_column()` 迁移旧 DB。
3. 为 `parent_session_id` 创建 index。

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.config import get_state_db_path; from learn_hermes_agent.state.session_db import SessionStore; s=SessionStore(get_state_db_path()); s.initialize(); print('ok')"
```

## Task 2: SessionStore Methods

**Files:**

- Modify: `src/learn_hermes_agent/state/session_db.py`

**Steps:**

1. 扩展 `create_session()` 参数：
   - `parent_session_id: str | None = None`
2. 插入 session 时写入 `parent_session_id`。
3. 新增 `end_session(session_id, reason)`。
4. `get_session()` 返回 parent/end 字段。
5. `list_sessions()` 返回 parent/end 字段。

**Manual Validation:**

```powershell
uv run python -c "from pathlib import Path; import tempfile; from learn_hermes_agent.state.session_db import SessionStore; tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); store=SessionStore(Path(tmp.name)/'state.db'); store.initialize(); parent=store.create_session(title='parent'); store.end_session(parent, 'compression'); child=store.create_session(title='child', parent_session_id=parent); print(store.get_session(parent)['end_reason']); print(store.get_session(child)['parent_session_id']); tmp.cleanup()"
```

Expected:

```text
compression
<parent session id>
```

## Task 3: CLI Compression Split

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Steps:**

1. 在交互模式 `agent.last_context_compressed` 分支中，不再 `replace_messages()` 当前 session。
2. 保存 `old_session_id = session_id`。
3. 调用 `store.end_session(old_session_id, "compression")`。
4. 创建 child session：
   - title 继承当前交互标题即可，最小版可用 `"compressed continuation"`。
   - `system_prompt=system_prompt`
   - `parent_session_id=old_session_id`
5. 将 compressed `messages` append 到 child session。
6. 更新 `session_id` 为 child session。
7. 输出新的 `session_id`，便于观察。

**Manual Validation:**

```powershell
uv run python -m compileall -q src
@("<多轮长输入>", "/sessions", "/q") | uv run learn-hermes-agent chat --show-messages
```

Expected:

- 输出 `context compressed: yes`
- 输出新 `session_id`
- `/sessions` 中能看到 parent session 的 `end_reason` 是 `compression`
- child session 的 `parent_session_id` 指向 parent

## Acceptance

- 普通未压缩对话仍 append 到当前 session。
- 压缩对话不覆盖旧 session，而是创建 child session。
- CLI 能观察 parent/child 关系。
- 不实现 resume tip 或 gateway projection。
