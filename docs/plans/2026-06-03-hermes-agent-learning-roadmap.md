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

**状态：已完成（最小版）**

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

当前最小版已完成：`CommandDef` / `COMMAND_REGISTRY`、slash command 解析、`chat` 无 message 进入最小交互循环、`/help`、`/new`、`/model`、`/tools`、`/sessions`、`/exit`。已实现 `config.yaml` 最小读取、默认配置深合并、`model.provider` / `model.default` / `agent.max_iterations` 覆盖、非法 YAML warning 和默认配置回退。暂不实现 `config set`、`config edit`、真实 provider resolver、credential 管理、TUI 或 `prompt_toolkit`。

## Phase 7：Context Compression 和 Budget

**状态：已完成（最小版）**

**目标：** 控制长会话上下文，并区分 context compression budget 与 agent iteration budget。

**文件：**

- Create: `src/learn_hermes_agent/agent/context_compressor.py`
- Create: `src/learn_hermes_agent/agent/iteration_budget.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/config.py`
- Later: `src/learn_hermes_agent/state/session_db.py`

**步骤：**

1. 对照真实 Hermes：
   - `agent/context_compressor.py`
   - `agent/conversation_compression.py`
   - `agent/conversation_loop.py`
   - `agent/iteration_budget.py`
   - `hermes_state.py`
   - `cli-config.yaml.example`
2. 先实现 `ContextCompressor` 最小版：
   - 粗略 token 估算。
   - `should_compress()` 阈值判断。
   - 保护 head messages。
   - 保护 tail messages。
   - 将 middle messages 压成 deterministic summary。
3. 在 `AIAgent.run_conversation()` 的 provider 调用前做 preflight compression。
4. 在 `config.py` 中新增最小 `compression` 配置：
   - `enabled`
   - `context_length`
   - `threshold`
   - `protect_first_n`
   - `protect_last_n`
5. 暂不实现真实 LLM summary、auxiliary compression model、post-response real usage 更新、compression session split、compression lock、memory/provider hooks。
6. 后续再单独实现 `IterationBudget`，其语义是 agent/tool loop 最大迭代次数，不是 context token budget。
7. 更新进度文档。

**验收：**

构造长 history 后，provider 请求前会触发压缩，返回的工作 messages 包含 summary + recent tail，并且仍能继续完成 fake 对话。当前最小版不要求看到 parent session；parent-child session chain 留到后续批次。

当前 Batch 1 已完成：`ContextCompressor`、compression 配置 normalize、`AIAgent` preflight compression、`SessionStore.replace_messages()`、交互模式压缩后整体替换当前 session messages、`doctor` 输出 compression 配置、单轮和交互模式在触发压缩时输出 `context compressed: yes`。

当前 Batch 2 已完成：抽出 `IterationBudget`，并让 `AIAgent.run_conversation()` 使用 `IterationBudget.consume()` 控制 provider/tool loop 次数。该 budget 只表示 agent loop 迭代次数，不表示 context token budget。

当前 Batch 3 已完成：`SessionStore` 增加 `parent_session_id`、`end_reason`、`ended_at`；`create_session()` 支持 parent session；新增 `end_session()`；交互模式触发 compression 时结束旧 session、创建 child session，并把 compressed working messages 写入 child session。暂不实现 compression lock、gateway projection、resume tip、memory hooks 或真实 provider usage。

当前 Batch 4 已完成：新增 session lineage 可观察性，`SessionStore.get_session_chain()` 可返回 root -> current 链路，`SessionStore.get_compression_tip()` 可从 compression parent 追到最新 continuation child，`show-session` 可输出 session metadata、compression tip、lineage 和 messages。Phase 7 最小版至此收口；compression lock、真实 provider usage、LLM summary、gateway projection、resume redirect、memory hooks 延后。

Phase 7.5 已完成：新增最小 `chat --resume <session_id>` 交互入口，可从普通 session 继续，也可把 compression parent 解析到 latest continuation child。该批次不实现 one-shot resume、session list projection、真实 provider、memory 或 skills。

## Phase 8：Memory 和 Skills

**状态：进行中（Batch 2 Skills 结构层已完成）**

**目标：** 实现 Hermes 自改进能力的基础。

**文件：**

- Create: `src/learn_hermes_agent/agent/memory_store.py`
- Create: `src/learn_hermes_agent/tools/memory.py`
- Create: `src/learn_hermes_agent/agent/skills.py`
- Create: `src/learn_hermes_agent/tools/skills.py`
- Later: `src/learn_hermes_agent/tools/skill_manager_tool.py`
- Modify: `src/learn_hermes_agent/agent/prompt_builder.py`
- Modify: `src/learn_hermes_agent/cli/main.py`
- Modify: `src/learn_hermes_agent/config.py`

**步骤：**

1. 实现 `MEMORY.md` / `USER.md` 风格存储。
2. 注册 `memory` 工具。
3. memory 注入 system prompt。
4. 实现 `skills/` 目录扫描。
5. 实现 `SKILL.md` frontmatter 解析。
6. 注册只读 `skills_list` / `skill_view`。
7. CLI 支持列出和查看 skills。
8. 后续再评估 `skill_manage`、自动 skill review 和自修改边界。
9. 更新进度文档。

**验收：**

agent 可保存 memory；新 session 能读取 memory；skill 能被列出和注入。

当前 Batch 1 已完成：新增 file-backed `MemoryStore`，使用 `.learn_hermes/memories/MEMORY.md` 和 `USER.md` 持久化；新增内置 `memory` tool，支持 `add` / `read` / `replace` / `remove`；`PromptBuilder` 的 volatile layer 可注入 `Persistent memory snapshot`；CLI 新建 session 时会把 snapshot 固化进 `sessions.system_prompt`。当前仍不实现自动记忆、external memory provider、prefetch/sync hooks、Skills、真实 provider 或测试。

当前 Batch 2 已完成：新增 `SkillLibrary`，可扫描 `.learn_hermes/skills/**/SKILL.md`、解析 YAML frontmatter、列出 skill metadata、查看 `SKILL.md` 或 skill 目录内 linked file；新增内置只读工具 `skills_list` / `skill_view`；CLI 新增 `skills` / `view-skill`；`PromptBuilder` 的 volatile layer 可注入轻量 `Available skills` index。当前仍不实现 `skill_manage`、自动创建/更新 skill、background review、external skill dirs、platform gating、真实 provider 或测试。

## Phase 9：Provider Runtime 扩展

**状态：最小版本已完成，高级 provider 适配后延**

**目标：** 从 fake provider 扩展到 Hermes 风格 provider runtime，让 agent loop 只依赖规范化后的 provider response，而不是直接理解每个厂商的原始响应。

**文件：**

- Create: `src/learn_hermes_agent/providers/openai_compatible.py`
- Create: `src/learn_hermes_agent/providers/runtime.py`
- Create: `src/learn_hermes_agent/providers/types.py`
- Create: `src/learn_hermes_agent/providers/fallback.py`
- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/fake.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/cli/main.py`
- Modify: `src/learn_hermes_agent/config.py`

**步骤：**

1. 已定义 Hermes 风格 `NormalizedResponse`、`ToolCall`、`Usage`。
2. 已将 `ProviderTransport.complete()` 改为返回 `NormalizedResponse`，并支持可选 `tools`。
3. 已实现 OpenAI-compatible `/chat/completions` provider，解析 `content`、`tool_calls`、`finish_reason` 和 usage。
4. 已实现 runtime provider resolver，支持 `fake`、`openai-compatible` / `openai`。
5. 已扩展 `config.yaml` 的 model 字段：`provider`、`default`、`base_url`、`api_key_env`、`timeout_seconds`、`fallbacks`。
6. 已在 `AIAgent` 中记录 `last_usage`、`last_finish_reason` 和 session token usage，并在 `--show-messages` 输出 `usage_snapshot()`。
7. 已实现 `FallbackProviderTransport`，按顺序尝试 provider，并记录实际命中的 provider index/model、最后错误和 `fallback_used` 可观测状态。
8. 已保持显式主 provider 缺少 API key 时 fail fast，不静默降级到 fake。
9. Anthropic、Gemini、Codex Responses、streaming、retry/backoff、model catalog 后延。

**验收：**

- `uv run python -m compileall -q src` 通过。
- 默认 fake provider 仍可运行。
- `chat --tool-demo --show-messages` 仍能输出 `user -> assistant(tool_calls) -> tool -> assistant(final)`。
- OpenAI-compatible provider 在缺少 API key 时输出可读错误。
- `doctor` 显示 provider、model、base_url、api_key_env 状态、timeout 和 fallback 概要，不打印真实 API key。
- fallback wrapper 可在主 provider 失败时切到后备 provider；即使主 provider 和 fallback provider 的 model 名相同，也能通过 `last_provider_index` 正确显示 `fallback_used: true`。

## Phase 10：Security / Approval / Execution

**状态：Batch 1、Batch 2A 至 2E、Batch 3、Batch 4、Batch 5 已完成；下一步为 Batch 6 Local Foreground Terminal 的源码对齐和设计**

**目标：** 为强工具建立统一安全边界。Phase 10 不从 terminal executor 开始，而是先建立工具执行上下文、命令审批策略、文件路径安全策略和 dispatch 前 preflight，再分批接入文件工具、checkpoint 和 terminal。

**Batch 1：Safety / Approval Primitives（已完成）**

已完成文件：

- Create: `src/learn_hermes_agent/agent/tool_context.py`
- Create: `src/learn_hermes_agent/agent/file_safety.py`
- Create: `src/learn_hermes_agent/tools/approval.py`
- Modify: `src/learn_hermes_agent/model_tools.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

已完成内容：

1. 定义 `ToolExecutionContext`，携带 session/task/workspace/security policy。
2. 在配置中新增 `security.approval_mode`、`security.yolo`、`security.workspace_root`，并在 `doctor` 中显示规范化结果。
3. 定义 `CommandRisk`、`ApprovalDecision` 和 `check_command_approval()`。
4. 定义 `PathDecision`、`check_read_path()`、`check_write_path()` 和 `resolve_workspace_path()`。
5. 将 context 贯穿到 `AIAgent.run_conversation()`、`safe_handle_function_call()`、`handle_function_call()`。
6. 在 `model_tools._preflight_tool_call()` 中预留 `terminal`、`read_file`、`search_files`、`write_file`、`patch` 的统一 preflight 入口。

Batch 1 验收：

- `uv run python -m compileall -q src` 通过。
- `doctor` 输出 security 配置。
- `tools` / `call-tool echo` / `chat --tool-demo --show-messages` 既有行为保持可运行。
- 命令策略：
  - `echo hello` -> `allowed`
  - `sudo reboot` -> `approval_required`
  - `rm -rf /` -> `blocked`
- 文件策略：
  - `README.md` read -> `allowed`
  - `.env` write -> `blocked`
  - `.ssh/id_rsa` write -> `blocked`
  - `.learn_hermes/config.yaml` write -> `blocked`

**Batch 2：File Tools With Safety（已完成）**

- Batch 2A：新增 `read_file`，支持 `path`、`offset`、`limit`、`LINE|CONTENT` 展示格式、UTF-8 和明显二进制文件阻断。
- Batch 2B：新增 `write_file`，写前执行统一 preflight 和 handler 内 defense-in-depth，返回 `files_modified`。
- Batch 2C：新增 `read_file` 行号展示文本检测，阻止展示文本被直接写回真实文件。
- Batch 2D：新增最小 `patch`，只支持单文件精确字符串替换、唯一性检查和显式 `replace_all`。
- Batch 2E：新增最小 `search_files`，只支持内容正则搜索、`path` 和 `limit`；根路径与候选文件均执行读安全检查。

Batch 2 暂未实现 fuzzy/V4A patch、外部修改检测、多文件 patch、LSP diagnostics、read dedup、search pagination、file glob、file-name search 或 ripgrep backend。

**Batch 3：ToolRegistry v2（已完成）**

- `ToolEntry` 增加兼容默认值 `toolset="other"` 和可选 `check_fn`。
- `ToolRegistry.generation` 从 0 开始，每次成功注册或覆盖后递增；失败的重复注册不递增。
- `get_definitions(tool_names=...)` 支持名称子集和可用性过滤。
- `check_fn=False` 或抛出异常时不向模型暴露工具；异常记录 warning，一个检查失败不影响其他工具。
- 同一次定义查询中，共享同一个 `check_fn` 的工具只检查一次；未增加跨调用 TTL cache。
- 增加 toolset 名称、toolset 内工具和单工具归属查询。
- 内置工具分类为 `file`、`memory`、`skills` 和默认 `other`。
- `AIAgent` 与 `model_tools.get_tool_definitions()` 已迁移到 `get_definitions()`；`list_definitions()` 暂保留为兼容包装。

Batch 3 验收：

- `uv run python -m compileall -q src` 通过。
- `tools` 仍输出八个内置工具定义。
- `call-tool echo` 和 `chat --tool-demo --show-messages` 通过。
- generation、可用性过滤、共享检查缓存、toolset 查询和 file 子集 schema 的轻量不变量通过。

Batch 3 暂未实现 deregister、check_fn TTL/failure grace cache、dynamic schema overrides、MCP/plugin ownership、toolset alias、执行范围拦截或异步 dispatch。

**Batch 4：Minimal ToolExecutor（已完成）**

- 新增模块级 `_parse_tool_arguments()`，只允许 JSON object 进入实际工具分发。
- 新增模块级 `execute_tool_calls_sequential()`，负责模型工具范围检查、参数解析、顺序执行和 tool result 追加。
- `AIAgent.valid_tool_names` 从实际发送给 Provider 的同一批 definitions 生成。
- 被 `check_fn=False` 隐藏的已注册工具不能被模型执行。
- `model_tools` 继续负责 Registry lookup、安全 preflight、handler dispatch 和 CLI 兼容。

Batch 4 验收：

- `uv run python -m compileall -q src` 通过。
- `tools` 仍输出八个内置工具定义。
- `call-tool echo`、未知工具严格错误语义和 `chat --tool-demo --show-messages` 通过。
- 参数解析、范围阻断、失败后继续执行、tool-call/result 配对和隐藏工具不变量通过。
- `.env` 读取仍被现有路径安全 preflight 阻断。

Batch 4 暂未实现 executor 类、并发、segmented execution、middleware、guardrails、checkpoint、terminal 或 dynamic registry refresh。

**Batch 5：Minimal Checkpoint（已完成）**

- 新增默认关闭的 checkpoint 配置和独立存储路径。
- 使用单一共享 shadow Git object store，并以 per-workspace ref/index 隔离状态。
- 实现快照、每 Provider/tool iteration 去重、无变化跳过、列举、真实数量裁剪和 Manager 级恢复。
- `AIAgent` 透明持有 `CheckpointManager`；checkpoint 不注册为模型工具。
- `write_file` / `patch` 只在安全 preflight 通过后、handler 前创建 best-effort checkpoint。
- checkpoint 失败 fail-open，安全 preflight fail-closed。
- 对齐 Hermes HEAD `477c08b44` 和 checkpoint path fix `d7b36070e`。
- 暂不实现 checkpoint CLI、rollback UX、diff、全局容量限制、自动维护、legacy migration 或 terminal checkpoint。

**后续批次：**

- Batch 6：Terminal Local Backend
  - 最后接入最小 local foreground `terminal` tool。
  - 执行前调用 `check_command_approval()`，hardline block 永远不可被 `force`、`yolo` 或 `auto` 绕过。
  - 暂不实现 Docker/SSH/Modal/Daytona、PTY、background process 或 streaming output。

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
