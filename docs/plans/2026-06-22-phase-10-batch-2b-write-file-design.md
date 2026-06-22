# Phase 10 Batch 2B Write File Tool Design

## Goal

Add a controlled `write_file` tool after the validated `read_file` tool, without adding `patch`, `terminal`, or checkpoint behavior.

## Constraints

- Do not modify the reference repository `D:\python-develop\project\hermes-agent`.
- Do not add `terminal`.
- Do not add `patch`.
- Do not add checkpoint.
- Do not add new test files by default.
- Keep implementation aligned with Hermes' intent: safety checks before file I/O, stable path resolution, and structured mutation results.

## Reference Alignment

Hermes' `tools/file_tools.py` treats file writes as a higher-risk operation than reads:

- sensitive paths are checked before writing;
- writes return the absolute target path;
- successful writes report `files_modified`;
- file tool handlers still perform defensive checks, even when a dispatcher preflight exists.

For this learning project, Batch 2B keeps only the minimal controlled-write subset:

- `path`
- `content`
- UTF-8 complete-file overwrite
- parent directory creation
- `check_write_path()` before write
- `files_modified` response

## Decision

Implement only `write_file` in this batch.

This is safer than adding `write_file` and `patch` together because checkpoint does not exist yet. A single full-file write gives us enough surface to validate the write safety policy and mutation result shape before adding targeted patching.

## Data Flow

```text
CLI / Agent
  -> model_tools.handle_function_call(..., context=ToolExecutionContext)
  -> _preflight_tool_call("write_file", ...)
  -> check_write_path(path, context)
  -> ToolRegistry dispatch
  -> write_file handler
  -> defense-in-depth check_write_path(path, config-derived context)
  -> create parent directories
  -> write UTF-8 content
  -> return JSON-compatible mutation result
```

## Response Shape

Successful result:

```json
{
  "path": "notes/example.txt",
  "resolved_path": "D:\\python-develop\\project\\learn_hermes_agent\\notes\\example.txt",
  "bytes_written": 12,
  "files_modified": [
    "D:\\python-develop\\project\\learn_hermes_agent\\notes\\example.txt"
  ]
}
```

Blocked result follows the existing `PathDecision.to_dict()` shape and includes `error`.

## Non-Goals

- No append mode.
- No partial write.
- No patch/replace mode.
- No external modification detection.
- No checkpoint.
- No approval prompt.
- No binary handling beyond treating `content` as a string.

## Validation

- `uv run python -m compileall -q src`
- `uv run learn-hermes-agent tools`
- Write a normal workspace file.
- Confirm the file can be read back with `read_file`.
- Confirm `.env` write is blocked.
- Confirm `.git/config` write is blocked.
- Confirm outside-workspace write is blocked.
- Confirm existing `echo` and `chat --tool-demo` still work.
