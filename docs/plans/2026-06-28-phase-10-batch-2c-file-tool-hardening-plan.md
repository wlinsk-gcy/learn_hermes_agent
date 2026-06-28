# Phase 10 Batch 2C File Tool Hardening Plan

## Goal

对齐最新版 Hermes `tools/file_tools.py` 的一个关键安全意图：`write_file` 不应把 `read_file`
返回给模型看的展示文本直接写回真实文件。

## Scope

实现：

- 将本项目 `read_file` 的行号展示格式调整为 `LINE|CONTENT`，贴近最新版 Hermes。
- 新增轻量检测：当 `write_file` 的内容主要由连续的 `read_file` 行号展示文本组成时拒绝写入。
- 保留既有路径安全检查、UTF-8 完整覆盖写入、`files_modified` 响应。

暂不实现：

- `patch`
- `terminal`
- checkpoint
- file staleness ledger
- cross-agent file locks
- verification evidence ledger
- 新测试文件

## Acceptance Criteria

- `uv run python -m compileall -q src` passes.
- `read_file` 返回 `1|...` 风格的行号内容。
- `write_file` 可以正常写入普通内容。
- `write_file` 拒绝连续的 `read_file` 行号展示文本。
- `.env`、`.git/config`、workspace 外路径仍然被拒绝。
