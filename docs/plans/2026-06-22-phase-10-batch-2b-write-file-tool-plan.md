# Phase 10 Batch 2B Write File Tool Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Follow project rules: documentation may be written directly, but implementation code is provided as task-by-task snippets unless the user explicitly asks to edit code files.

**Goal:** Add the controlled `write_file` tool after `read_file`, without adding `patch`, `terminal`, or checkpoint behavior.

**Architecture:** Keep the current `ToolRegistry` handler signature unchanged. Reuse `model_tools` preflight for the primary safety gate, and repeat `check_write_path()` inside the handler as defense-in-depth before performing UTF-8 file writes.

**Tech Stack:** Python 3.11+, existing `ToolRegistry`, `model_tools.handle_function_call`, `agent/file_safety.py`, argparse CLI, lightweight validation via `compileall`, CLI calls, schema inspection, and path-safety invariants.

---

## Scope

Implement:
- `write_file` tool only.
- `path` and `content` arguments.
- Workspace-root write safety via existing `check_write_path`.
- Parent directory creation.
- UTF-8 complete-file overwrite.
- Structured mutation response with `resolved_path` and `files_modified`.
- Registry exposure through the existing file tools module.

Do not implement:
- `patch`
- `terminal`
- checkpoint
- append mode
- partial writes
- fuzzy patch
- external modification detection
- multi-file patch
- LSP diagnostics
- new test files

## Acceptance Criteria

- `uv run python -m compileall -q src` passes.
- `uv run learn-hermes-agent tools` lists `write_file`.
- `uv run learn-hermes-agent call-tool write_file '{"path":"sandbox/write_file_smoke.txt","content":"hello"}'` succeeds.
- Reading `sandbox/write_file_smoke.txt` with `read_file` returns `hello`.
- Writing `.env` is blocked.
- Writing `.git/config` is blocked.
- Writing outside the workspace root is blocked.
- Existing `echo`, `read_file`, `memory`, `skills_list`, and `skill_view` tools remain registered.
- `chat --tool-demo --show-messages` still works unchanged.

## Task 1: Add `write_file` Schema and Handler

**Files:**
- Modify: `src/learn_hermes_agent/tools/file_tools.py`

**Purpose:**
Add `WRITE_FILE_PARAMETERS` and `write_file(arguments)` beside the existing `read_file` implementation, without registering it yet.

**Manual Verification:**
- Run `uv run python -m compileall -q src`.
- Optionally import and call `write_file` directly in a Python one-liner against a safe temporary workspace path.

## Task 2: Register `write_file`

**Files:**
- Modify: `src/learn_hermes_agent/tools/file_tools.py`

**Purpose:**
Expose `write_file` from the existing file tools registration function.

**Manual Verification:**
- Run `uv run learn-hermes-agent tools`.
- Confirm existing tools plus `write_file` appear.

## Task 3: Validate Safety and Read-Back Behavior

**Files:**
- No code changes expected.

**Purpose:**
Confirm successful writes produce the expected mutation shape and blocked writes stop before file I/O.

**Manual Verification:**
- Write `sandbox/write_file_smoke.txt`.
- Read it back with `read_file`.
- Attempt blocked writes to `.env`, `.git/config`, and `../write_file_escape.txt`.

## Task 4: Final Batch 2B Regression Check

**Files:**
- No code changes expected.

**Purpose:**
Verify existing tool-calling behavior remains stable.

**Manual Verification:**
- `uv run python -m compileall -q src`
- `uv run learn-hermes-agent call-tool echo '{"text":"hello"}'`
- `uv run learn-hermes-agent call-tool read_file '{"path":"README.md","limit":2}'`
- `uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"`
