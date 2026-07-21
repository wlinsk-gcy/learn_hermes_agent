# Phase 10 Batch 2E Search Files Closeout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 Phase 10 Batch 2E `search_files` 的集中验收和文档交接，让下一轮可以从 ToolRegistry v2 开始。

**Architecture:** 不再扩展 `search_files` 功能，只验证现有最小实现的 schema、统一 preflight、候选路径过滤和工具调用兼容性。验证通过后更新 roadmap 与 progress handoff；不修改 `src/`，不新增测试文件。

**Tech Stack:** Python 3.11+、uv、当前 ToolRegistry、当前 model_tools、pathlib/re、compileall 和 CLI 手工验证。

---

## Task 1：复核 Batch 2E 接口

**Files:**

- Read: `src/learn_hermes_agent/tools/file_tools.py`
- Read: `src/learn_hermes_agent/model_tools.py`
- Read: `src/learn_hermes_agent/tools/registry.py`

检查：

- schema 只包含 `pattern`、`path`、`limit`。
- `pattern` 必填，`path` 默认 `.`，`limit` 范围为 1 到 200。
- `search_files` 已注册。
- `model_tools` 对 `search_files` 执行统一读路径 preflight。
- handler 在读取每个候选文件前再次执行 `check_read_path()`。

## Task 2：运行集中验收

不创建测试文件。运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

另用一次 Python 手工脚本验证：

- 正常文件内容搜索。
- 非法正则返回结构化错误。
- `limit=1` 时返回一条并标记 truncated。
- `.env` 和 workspace 外搜索根路径被阻断。
- 自定义 `ToolExecutionContext.workspace_root` 能约束 search preflight。
- 候选目录中的普通文件可命中，敏感 `.env` 被跳过。

预期：所有命令退出码为 0，所有轻量不变量成立。

## Task 3：更新路线图

**Files:**

- Modify: `docs/02-roadmap.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

记录：

- Phase 10 Batch 1 与 Batch 2A 至 2E 已完成。
- 下一批为 ToolRegistry v2。
- ToolExecutor、checkpoint、terminal 的后续顺序。
- 明确 terminal 和 checkpoint 尚未实现。

## Task 4：更新交接文档

**Files:**

- Modify: `docs/04-progress-handoff.md`

追加 Batch 2A 至 2E 的完成内容、修改文件、Hermes 对齐模块、实际运行的验证命令、观察结果和下一步。

## Task 5：一致性复核

运行：

```powershell
rg -n "Batch 2E|ToolRegistry v2|terminal|checkpoint" docs/02-roadmap.md docs/04-progress-handoff.md docs/plans
git status --short
```

预期：

- 最新计划与 handoff 指向 ToolRegistry v2。
- 文档明确 terminal/checkpoint 尚未实现。
- `src/` 没有因收尾任务产生新的代码改动。

## 执行约束

- 不修改参考仓库。
- 不修改项目代码。
- 不新增测试文件。
- 不开始 ToolRegistry v2、ToolExecutor、checkpoint 或 terminal 的实现。
- 不自动提交；提交由用户另行决定。
