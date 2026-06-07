# Hermes Agent Learning Roadmap Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `learn_hermes_agent` 中分阶段复刻 Hermes Agent，使每个阶段都有清晰学习目标、实现边界和可观察验证方式。

**Architecture:** 采用“核心 runtime 先行，外围入口后接入”的路线。先实现 `AIAgent -> ProviderTransport -> ToolRegistry -> SessionStore -> PromptBuilder` 的可运行闭环，再扩展 CLI、memory、skills、gateway、plugins、ACP/TUI/Cron/Batch。

**Tech Stack:** Python 3.11+、uv、SQLite、PyYAML、Rich、prompt_toolkit、OpenAI-compatible API、可选 FastAPI/Uvicorn。

---

## 执行说明

本计划是长期路线图，不要求一次 session 全部实现。

每个阶段完成后必须更新：

- `docs/04-progress-handoff.md`
- 本计划中对应阶段的状态

默认验证方式不要求新增测试，除非用户明确要求测试。优先使用：

- 命令能运行。
- 接口返回结构正确。
- SQLite 表和记录可观察。
- 消息协议不变量成立。
- 手工模拟 provider/tool 行为。

## Phase 0：项目基础

**状态：已完成**

**目标：** 建立 uv 项目和包结构。

**文件：**

- Create: `pyproject.toml`
- Create: `src/learn_hermes_agent/__init__.py`
- Create: `src/learn_hermes_agent/__main__.py`
- Create: `src/learn_hermes_agent/cli/__init__.py`
- Create: `src/learn_hermes_agent/cli/main.py`
- Create: `src/learn_hermes_agent/config.py`

**步骤：**

1. 创建 `pyproject.toml`，声明 Python 版本和基础依赖。
2. 建立 `src/` 包结构。
3. 写最小 CLI 入口。
4. 加入 `uv run python -m learn_hermes_agent --help` 的帮助输出。
5. 更新 `docs/04-progress-handoff.md`。

**验收：**

```powershell
uv run python -m learn_hermes_agent --help
uv run learn-hermes-agent doctor
```

应显示命令帮助，不报 import 错误。

## Phase 1：最小 Agent Loop

**状态：已完成**

**目标：** 跑通一轮无工具对话。

**文件：**

- Create: `src/learn_hermes_agent/agent/core.py`
- Create: `src/learn_hermes_agent/providers/base.py`
- Create: `src/learn_hermes_agent/providers/fake.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**步骤：**

1. 定义 OpenAI 风格 message 类型。
2. 定义 `ProviderTransport` 协议。
3. 实现 `FakeProviderTransport`。
4. 实现 `AIAgent.run_conversation()` 最小版本。
5. CLI 调用 agent 并打印结果。
6. 更新进度文档。

**验收：**

运行 CLI 输入一句话，返回 fake assistant response：

```powershell
uv run learn-hermes-agent chat "hello"
```

## Phase 2：Tool Registry

**状态：已完成（最小版）**

**目标：** 复刻工具注册和 schema 暴露。

**文件：**

- Create: `src/learn_hermes_agent/tools/registry.py`
- Create: `src/learn_hermes_agent/tools/echo.py`
- Create: `src/learn_hermes_agent/model_tools.py`

**步骤：**

1. 定义 `ToolEntry`。
2. 定义 `ToolRegistry.register()`。
3. 定义 `discover_builtin_tools()`。
4. 实现低风险 `echo` 工具。
5. 实现 `get_tool_definitions()`。
6. 实现 `handle_function_call()`。
7. CLI 增加 `tools` 和 `call-tool` debug 子命令。
8. 更新进度文档。

**验收：**

手工调用工具分发可返回 JSON 字符串。CLI 能列出工具名和 schema：

```powershell
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
```

说明：Windows PowerShell 下 JSON 参数需要保留内部双引号，当前推荐使用上面的转义形式。

## Phase 3：Tool Calling Conversation Loop

**状态：已完成（最小版）**

**目标：** 实现 assistant tool_calls 和 role=tool result 的完整配对。

**文件：**

- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/agent/messages.py`
- Modify: `src/learn_hermes_agent/providers/fake.py`
- Modify: `src/learn_hermes_agent/model_tools.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**步骤：**

1. 让 fake provider 支持脚本化响应：先返回 tool_call，再返回 final text。
2. agent loop 检测 `assistant_message.tool_calls`。
3. 校验工具名。
4. 校验 JSON arguments。
5. append assistant tool_calls message。
6. 执行工具。
7. append role=tool result。
8. 循环下一次 provider call。
9. 更新进度文档。

**验收：**

一次 fake 对话的消息序列包含：

```text
user
assistant(tool_calls)
tool
assistant(final)
```

当前最小版通过 `chat --tool-demo --show-messages` 观察完整消息序列；真实 provider、session store、并发工具执行、审批和 gateway 留到后续阶段。

## Phase 4：Session Store

**状态：已完成（最小版）**

**目标：** 持久化 session 和 messages。

**文件：**

- Create: `src/learn_hermes_agent/state/session_db.py`
- Create: `src/learn_hermes_agent/state/__init__.py`
- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**步骤：**

1. 建立 SQLite schema：`sessions`、`messages`。
2. 启用 WAL。
3. 实现 create/get/list session。
4. 实现 replace/append messages。
5. 持久化 system prompt 字段。
6. 若 SQLite 支持 FTS5，建立 messages FTS。
7. CLI 增加 session list/resume 的最小能力。
8. 更新进度文档。

**验收：**

CLI 对话后 SQLite 中存在 session 和 message；重启后可读取。

Phase 4 最小版通过 CLI 层持久化每次 `chat` 的完整 messages，并提供 `sessions` / `show-session` 观察入口。当时暂不实现 session resume、system prompt 持久化、FTS5 search、context compression parent-child 关系或 gateway 复用；system prompt 持久化已在 Phase 5 最小版接入。

## Phase 5：System Prompt Builder

**状态：已完成（最小版）**

**目标：** 实现 stable/context/volatile prompt 分层。

**文件：**

- Create: `src/learn_hermes_agent/agent/system_prompt.py`
- Create: `src/learn_hermes_agent/agent/prompt_builder.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/state/session_db.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**步骤：**

1. 定义 stable prompt。
2. 读取 context file：`AGENTS.md`。
3. 实现简单 prompt injection marker 扫描。
4. 定义 volatile prompt 预留层，当前保持为空。
5. 在 provider request 中临时 prepend `role=system` message。
6. 持久化 `sessions.system_prompt`。
7. 更新进度文档。

**验收：**

当前最小版已实现：每次 `chat` 构建 system prompt，provider request 中包含 system message，返回/持久化的普通 messages 不包含 system role，`sessions.system_prompt` 可读回。暂不实现 `SOUL.md`、memory、skills、用户画像、真实 provider、gateway、context compression 或 system prompt cache invalidation。

## Phase 6：CLI 和配置

**状态：未开始**

**目标：** 形成可日常使用的交互 CLI。

**文件：**

- Create: `src/learn_hermes_agent/cli/commands.py`
- Modify: `src/learn_hermes_agent/cli/main.py`
- Modify: `src/learn_hermes_agent/config.py`

**步骤：**

1. 对照真实 Hermes：
   - `hermes_cli/_parser.py`
   - `hermes_cli/main.py`
   - `hermes_cli/commands.py`
   - `hermes_cli/config.py`
   - 根目录 `cli.py`
2. 建立最小 `CommandDef` 和 `COMMAND_REGISTRY`，先作为 slash command 元数据中心。
3. 实现 `resolve_command()` 和 `format_help_lines()`。
4. 修改 `chat`：让 message 参数可选；无 message 时进入最小交互循环。
5. 实现 `/help`、`/new`、`/model`、`/tools`、`/sessions`、`/exit`。
6. 第二批再支持 `config.yaml`，建议引入 `PyYAML`，复刻 Hermes 的 YAML 配置读取方向。
7. 更新进度文档。

**验收：**

CLI 支持连续对话、切 session、查看工具。当前阶段不复刻 TUI、`prompt_toolkit`、model picker、resume by title、provider runtime resolver、gateway command。

## Phase 7：Context Compression 和 Budget

**状态：未开始**

**目标：** 控制长会话上下文。

**文件：**

- Create: `src/learn_hermes_agent/agent/iteration_budget.py`
- Create: `src/learn_hermes_agent/agent/context_compressor.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/state/session_db.py`

**步骤：**

1. 实现 `IterationBudget`。
2. 实现粗略 token 估算。
3. 达到阈值时触发摘要压缩。
4. 创建 parent-child session 关系。
5. compression 后 invalid system prompt cache。
6. 更新进度文档。

**验收：**

长会话触发压缩后仍能继续对话，并能看到 parent session。

## Phase 8：Memory 和 Skills

**状态：未开始**

**目标：** 实现 Hermes 自改进能力的基础。

**文件：**

- Create: `src/learn_hermes_agent/agent/memory_store.py`
- Create: `src/learn_hermes_agent/tools/memory_tool.py`
- Create: `src/learn_hermes_agent/agent/skills.py`
- Create: `src/learn_hermes_agent/tools/skill_manager_tool.py`
- Modify: `src/learn_hermes_agent/agent/system_prompt.py`

**步骤：**

1. 实现 `MEMORY.md` / `USER.md` 风格存储。
2. 注册 `memory` 工具。
3. memory 注入 system prompt。
4. 实现 `skills/` 目录扫描。
5. 实现 `SKILL.md` frontmatter 解析。
6. 注册 `skill_manage`。
7. CLI 支持列出 skills。
8. 更新进度文档。

**验收：**

agent 可保存 memory；新 session 能读取 memory；skill 能被列出和注入。

## Phase 9：Provider Runtime 扩展

**状态：未开始**

**目标：** 从 fake/openai 扩展到多 provider。

**文件：**

- Create: `src/learn_hermes_agent/providers/openai_compatible.py`
- Create: `src/learn_hermes_agent/providers/anthropic.py`
- Create: `src/learn_hermes_agent/providers/runtime.py`
- Create: `src/learn_hermes_agent/providers/types.py`
- Modify: `src/learn_hermes_agent/config.py`

**步骤：**

1. 定义 normalized response 类型。
2. OpenAI-compatible provider 规范化。
3. usage normalization。
4. runtime provider resolver。
5. fallback chain。
6. 再接 Anthropic 或 Gemini。
7. 更新进度文档。

**验收：**

至少两个 provider 可按配置切换；fake fallback 可观察。

## Phase 10：Security / Approval / Terminal

**状态：未开始**

**目标：** 为强工具加安全边界。

**文件：**

- Create: `src/learn_hermes_agent/tools/approval.py`
- Create: `src/learn_hermes_agent/tools/terminal_tool.py`
- Create: `src/learn_hermes_agent/tools/path_security.py`
- Create: `src/learn_hermes_agent/tools/checkpoint_manager.py`

**步骤：**

1. 定义 approval policy。
2. terminal 工具执行前判断危险命令。
3. 文件写入前路径检查。
4. per-session approval context。
5. checkpoint 简化实现。
6. 更新进度文档。

**验收：**

危险命令默认要求审批；审批状态不跨 session 泄漏。

## Phase 11：Gateway

**状态：未开始**

**目标：** 复刻异步消息入口。

**文件：**

- Create: `src/learn_hermes_agent/gateway/runner.py`
- Create: `src/learn_hermes_agent/gateway/session.py`
- Create: `src/learn_hermes_agent/gateway/platforms/base.py`
- Create: `src/learn_hermes_agent/gateway/platforms/api_server.py`

**步骤：**

1. 定义 `BasePlatformAdapter`。
2. 定义 `GatewayRunner`。
3. 实现 HTTP/API adapter。
4. session key 映射到 session_id。
5. 实现 running queue。
6. 实现 `/stop`、`/new`、`/status`。
7. 更新进度文档。

**验收：**

HTTP 请求可触发 agent turn；运行中消息可排队或停止。

## Phase 12：Plugins

**状态：未开始**

**目标：** 加入扩展系统。

**文件：**

- Create: `src/learn_hermes_agent/plugins/manager.py`
- Create: `src/learn_hermes_agent/plugins/context.py`
- Create: `plugins/demo/plugin.yaml`
- Create: `plugins/demo/plugin.py`

**步骤：**

1. 定义 plugin manifest。
2. 扫描插件目录。
3. `PluginContext.register_tool()`。
4. `PluginContext.register_command()`。
5. hook 注册与 `invoke_hook()`。
6. 接入 agent loop 和 tool dispatch。
7. 更新进度文档。

**验收：**

demo 插件能注册工具和 transform hook。

## Phase 13：ACP / TUI / Cron / Batch

**状态：未开始**

**目标：** 扩展外围入口和研究能力。

**文件：**

- Create: `src/learn_hermes_agent/acp_adapter/`
- Create: `src/learn_hermes_agent/tui_gateway/`
- Create: `src/learn_hermes_agent/cron/`
- Create: `src/learn_hermes_agent/batch_runner.py`
- Create: `src/learn_hermes_agent/trajectory.py`

**步骤：**

1. 先实现 Cron scheduler。
2. 实现 batch runner。
3. 保存 trajectory。
4. 实现 ACP server 最小协议。
5. 实现 TUI gateway JSON-RPC 后端。
6. 更新进度文档。

**验收：**

CLI/Gateway/Cron/Batch 至少共享同一个 `AIAgent` runtime。

## 完成标准

阶段完成不是“代码存在”，而是满足这些条件：

- 文档已更新。
- 入口可运行。
- 消息协议或数据结构可观察。
- 和 Hermes 对应源码文件做过对照。
- 下一阶段边界清楚。
