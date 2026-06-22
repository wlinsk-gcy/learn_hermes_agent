# Phase 10 Batch 2A Read File Tool Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Follow project rules: documentation may be written directly, but implementation code is provided as task-by-task snippets unless the user explicitly asks to edit code files.

**Goal:** Add the first safe file tool, `read_file`, without adding write tools, terminal execution, or checkpoint behavior.

**Architecture:** Keep the current `ToolRegistry` handler signature unchanged. Use `model_tools` preflight with `ToolExecutionContext` as the shared safety entry point, and keep `read_file` itself defensive by using the existing file safety policy before reading from disk.

**Tech Stack:** Python 3.11+, existing `ToolRegistry`, `model_tools.handle_function_call`, `agent/file_safety.py`, argparse CLI, lightweight validation via `compileall`, CLI calls, schema inspection, and path-safety invariants.

---

## Scope

Implement:
- `read_file` tool only.
- `path`, `offset`, and `limit` arguments.
- Workspace-root path safety via existing `check_read_path`.
- Basic text read with line-numbered output.
- Binary-extension guard for obvious binary files.
- Single-read size guard.
- Tool registration.
- CLI `call-tool` context propagation so manual calls use preflight.

Do not implement:
- `write_file`
- `patch`
- `terminal`
- checkpoint
- fuzzy patch
- read dedup tracker
- external modification detection
- multi-file patch
- LSP diagnostics
- new test files

## Reference Alignment

Hermes reference module: `D:\python-develop\project\hermes-agent\tools\file_tools.py`.

Keep these design intentions:
- Safety checks happen before real file I/O.
- Relative paths resolve through a stable working context.
- Reads are bounded by pagination and character limits.
- Tool results are structured JSON-compatible dictionaries.

Simplify these parts for the learning project:
- No terminal-backed `ShellFileOperations`.
- No per-task read dedup state.
- No cross-agent file state.
- No redaction engine.
- No V4A patch parser.

## Acceptance Criteria

- `uv run python -m compileall -q src` passes.
- `uv run learn-hermes-agent tools` lists `read_file`.
- `uv run learn-hermes-agent call-tool read_file '{"path":"README.md","limit":5}'` returns content with line numbers.
- Reading `.env` is blocked before file I/O.
- Reading a path outside the workspace root is blocked.
- Existing `echo`, `memory`, `skills_list`, and `skill_view` tools remain registered.
- `chat --tool-demo --show-messages` still works unchanged.

## Task 1: Add `read_file` Tool Module

**Files:**
- Create: `src/learn_hermes_agent/tools/file_tools.py`

**Purpose:**
Create the read-only tool implementation and schema, without registering it yet.

**Manual Verification:**
- Run `uv run python -m compileall -q src`.
- Optionally import the module in a Python one-liner.

## Task 2: Register `read_file`

**Files:**
- Modify: `src/learn_hermes_agent/tools/registry.py`

**Purpose:**
Expose `read_file` through the existing tool registry while preserving all existing tools.

**Manual Verification:**
- Run `uv run learn-hermes-agent tools`.
- Confirm existing tools plus `read_file` appear.

## Task 3: Pass Context Through CLI `call-tool`

**Files:**
- Modify: `src/learn_hermes_agent/cli/main.py`

**Purpose:**
Make manual tool calls use the same preflight path-safety checks as agent calls.

**Manual Verification:**
- Read `README.md` successfully.
- Attempt to read `.env` and confirm it is blocked.
- Attempt to read a parent/outside path and confirm it is blocked.

## Task 4: Final Batch 2A Validation

**Files:**
- No code changes expected.

**Purpose:**
Verify Batch 2A did not alter existing tool-calling behavior.

**Manual Verification:**
- `uv run python -m compileall -q src`
- `uv run learn-hermes-agent call-tool echo '{"text":"hello"}'`
- `uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"`
- Inspect `read_file` schema in `uv run learn-hermes-agent tools`.
