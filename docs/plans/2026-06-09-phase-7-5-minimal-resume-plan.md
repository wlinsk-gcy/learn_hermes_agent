# Phase 7.5 Minimal Resume Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal interactive resume path that continues a persisted session and resolves compression parents to their latest continuation child.

**Architecture:** Add `--resume <session_id>` to the existing `chat` command. `run_interactive_chat()` initializes from a persisted tip session when resume is requested; otherwise it keeps creating a new session exactly as before. Do not implement one-shot resume, session list projection, real provider, memory, skills, or tests.

**Tech Stack:** Python 3.11+、SQLite、当前 `SessionStore`、当前 argparse CLI、当前 fake provider。

---

## Boundary

Implement:

- `learn-hermes-agent chat --resume <session_id>`
- parent-to-tip resolution through `SessionStore.get_compression_tip()`
- resumed interactive history loaded from persisted messages
- persisted `system_prompt` reuse
- manual validation commands
- progress docs after user confirms implementation

Do not implement:

- `chat --resume <id> "message"` one-shot resume
- `/resume` slash command
- standalone `resume` subcommand
- session list projection
- title/search resume
- real provider
- memory/skills
- tests

## Task 1: Add `--resume` CLI Argument

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Add parser argument**

Add this near the existing `chat_parser.add_argument("--show-messages", ...)` block:

```python
    chat_parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume an existing session. Compression parents resume from their latest continuation child.",
    )
```

**Step 2: Update `run_chat()` signature and routing**

Replace the current function header and first branch with:

```python
def run_chat(
    message: str | None,
    *,
    tool_demo: bool = False,
    show_messages: bool = False,
    resume_session_id: str | None = None,
) -> int:
    if resume_session_id is not None and message is not None:
        print(
            "error: --resume currently supports interactive chat only; omit the message argument.",
            file=sys.stderr,
        )
        return 1

    if message is None:
        return run_interactive_chat(
            tool_demo=tool_demo,
            show_messages=show_messages,
            resume_session_id=resume_session_id,
        )
```

Leave the existing one-shot non-resume body below that branch unchanged.

**Step 3: Pass parsed resume id from `main()`**

Update the `run_chat()` call in `main()`:

```python
        return run_chat(
            args.message,
            tool_demo=args.tool_demo,
            show_messages=args.show_messages,
            resume_session_id=args.resume,
        )
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat --help
uv run learn-hermes-agent chat --resume missing "hello"
```

Expected:

- `compileall` passes.
- `chat --help` shows `--resume SESSION_ID`.
- `chat --resume missing "hello"` returns non-zero and prints the one-shot resume unsupported error.

## Task 2: Initialize Interactive Chat From Resume Target

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Update function signature**

Replace:

```python
def run_interactive_chat(*, tool_demo: bool = False, show_messages: bool = False) -> int:
```

with:

```python
def run_interactive_chat(
    *,
    tool_demo: bool = False,
    show_messages: bool = False,
    resume_session_id: str | None = None,
) -> int:
```

**Step 2: Replace the initial session setup block**

Replace the block that currently builds `system_prompt`, creates a new session, builds `agent`, and sets `history = []` with:

```python
    if resume_session_id is None:
        system_prompt = build_system_prompt()
        session_id = store.create_session(
            title="interactive chat",
            system_prompt=system_prompt,
        )
        history: list[ChatMessage] = []
        resumed_from: str | None = None
    else:
        requested_session = store.get_session(resume_session_id)
        if requested_session is None:
            print(f"error: session not found: {resume_session_id}", file=sys.stderr)
            return 1

        session_id = store.get_compression_tip(resume_session_id)
        session = store.get_session(session_id)
        if session is None:
            print(f"error: resume target not found: {session_id}", file=sys.stderr)
            return 1

        raw_system_prompt = session.get("system_prompt")
        system_prompt = str(raw_system_prompt) if raw_system_prompt else build_system_prompt()
        history = store.get_session_messages(session_id)
        resumed_from = resume_session_id

    agent = build_agent(config, tool_demo=tool_demo)
```

**Step 3: Add resume visibility to startup output**

Keep the existing startup lines, but after printing `session_id`, add:

```python
    if resumed_from is not None:
        print(f"resumed_from: {resumed_from}")
```

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat --resume missing
```

Expected:

- `compileall` passes.
- Missing session returns non-zero and prints `error: session not found: missing`.

## Task 3: Validate Resume Appends To Tip Session

**Files:**

- No code file changes.

**Manual Validation:**

Create a temporary state DB with a compression parent and child:

```powershell
$root=(Get-Location).Path
$tmp=Join-Path $root ('.tmp_phase75_resume_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$env:LEARN_HERMES_HOME=$tmp
$ids = uv run python -c "from learn_hermes_agent.state.session_db import SessionStore; from learn_hermes_agent.config import get_state_db_path; from learn_hermes_agent.agent.messages import user_message, assistant_message; s=SessionStore(get_state_db_path()); s.initialize(); p=s.create_session(title='parent', system_prompt='stable prompt'); s.append_message(p, user_message('before compression')); s.end_session(p, 'compression'); c=s.create_session(title='child', system_prompt='stable prompt', parent_session_id=p); s.append_message(c, user_message('[Context compression summary]')); s.append_message(c, assistant_message('ready')); print(p); print(c)"
$parent=($ids | Select-Object -First 1)
$child=($ids | Select-Object -Last 1)
@("continued after resume","/q") | uv run learn-hermes-agent chat --resume $parent
uv run learn-hermes-agent show-session $parent
uv run learn-hermes-agent show-session $child
Remove-Item Env:\LEARN_HERMES_HOME -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $tmp -Recurse -Force
```

Expected:

- Startup output prints `session_id: <child>` and `resumed_from: <parent>`.
- Parent `message_count` remains unchanged.
- Child `messages` include the new `continued after resume` user message and the fake assistant response.

## Task 4: Validate Normal Chat Is Unchanged

**Files:**

- No code file changes.

**Manual Validation:**

```powershell
uv run learn-hermes-agent chat "resume smoke"
@("hello normal","/q") | uv run learn-hermes-agent chat
```

Expected:

- One-shot `chat "resume smoke"` still creates a new session and returns a fake assistant response.
- Interactive `chat` without `--resume` still creates a new session and accepts messages.

## Task 5: Update Progress Docs

**Files:**

- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Step 1: Add Phase 7.5 note to roadmap**

Add a short note after the Phase 7 Batch 4 completion paragraph:

```markdown
Phase 7.5 已完成：新增最小 `chat --resume <session_id>` 交互入口，可从普通 session 继续，也可把 compression parent 解析到 latest continuation child。该批次不实现 one-shot resume、session list projection、真实 provider、memory 或 skills。
```

**Step 2: Append handoff entry**

Append a `2026-06-09 Phase 7.5 Minimal Resume 进度更新` section including:

- 本次目标
- 已完成
- 修改文件
- 对照的 Hermes 源码
- 验证方式
- 设计结论
- 下一步

**Manual Validation:**

```powershell
rg -n "Phase 7.5|resume|compression_tip|chat --resume" docs
git status --short --untracked-files=all
```

Expected:

- Roadmap mentions Phase 7.5.
- Handoff has Phase 7.5 entry.
- Git status shows only expected user code changes plus docs changes.
