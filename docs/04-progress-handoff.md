# 进度与 Session 交接

## 当前日期

2026-07-22

## 当前状态

已完成：

- 读取官方文档入口：https://hermes-agent.nousresearch.com/docs
- 读取官方开发文档中的架构、agent loop、provider runtime、session storage、gateway internals。
- 使用本地代码索引分析 `D:\python-develop\project\hermes-agent`。
- 已在 2026-07-21 重新分析最新版本地 Hermes 源码；当前核心工具链包括 `agent/conversation_loop.py`、`agent/tool_executor.py`、`model_tools.py`、`tools/registry.py` 和 `tools/*.py`。
- 已建立当前学习项目结构：
  - `pyproject.toml`
  - `src/learn_hermes_agent/`
  - `docs/`
  - `AGENTS.md`
  - `README.md`
- 已创建中文文档：
  - `AGENTS.md`
  - `docs/00-overview.md`
  - `docs/01-architecture-analysis.md`
  - `docs/02-roadmap.md`
  - `docs/03-source-reading-index.md`
  - `docs/04-progress-handoff.md`
  - `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- Phase 0：已初始化 uv 项目，建立 `pyproject.toml`、`src/learn_hermes_agent/` 包结构、最小 CLI、`doctor` 命令。
- Phase 1：已实现最小 `AIAgent`、OpenAI 风格 message 类型、`ProviderTransport` 协议、`FakeProviderTransport`、`chat` 命令。
- Phase 2：已实现最小工具注册和分发层：
  - `ToolEntry`
  - `ToolRegistry`
  - `discover_builtin_tools()`
  - `get_default_registry()`
  - 内置 `echo` 工具
  - `get_tool_definitions()`
  - `handle_function_call()`
  - CLI `tools`
  - CLI `call-tool`
- Phase 3：已实现最小工具调用对话循环：
  - `assistant.tool_calls` 接入 `AIAgent.run_conversation()`
  - `role=tool` 消息构造
  - `tool_call_id` 配对
  - fake provider 脚本化响应
  - CLI `chat --tool-demo`
  - CLI `chat --show-messages`
- Phase 4：已实现最小 SQLite session store：
  - `sessions` / `messages` 表
  - SQLite WAL
  - `SessionStore.initialize()`
  - `SessionStore.create_session()`
  - `SessionStore.append_message()` / `append_messages()`
  - `SessionStore.get_session_messages()`
  - `SessionStore.list_sessions()`
  - CLI `sessions`
  - CLI `show-session`
  - `chat` 后自动持久化一次性 session
- Phase 5：已实现最小 system prompt builder：
  - `STABLE_SYSTEM_PROMPT`
  - `PromptLayers`
  - `PromptBuilder`
  - 读取本项目 `AGENTS.md` 作为 context layer
  - 最小 prompt injection marker 扫描
  - provider 请求临时注入 `role=system`
  - 普通对话 messages 不返回、不持久化 system message
  - `sessions.system_prompt` 持久化
- Phase 6 Batch 1：已实现最小交互 CLI 和 slash command registry：
  - `CommandDef`
  - `COMMAND_REGISTRY`
  - `resolve_command()`
  - `parse_slash_command()`
  - `format_help_lines()`
  - `chat` message 参数可选
  - `chat` 无 message 时进入最小交互循环
  - `/help`
  - `/new`
  - `/model`
  - `/tools`
  - `/sessions`
  - `/exit` 及别名
- Phase 6 Batch 2：已实现最小 `config.yaml` 读取和默认配置合并：
  - 引入 `PyYAML`
  - 读取 `.learn_hermes/config.yaml`
  - 与 `DEFAULT_CONFIG` 深合并
  - 支持 `model.provider`
  - 支持 `model.default`
  - 支持 `agent.max_iterations`
  - 非法 YAML 或非 object 配置会输出 stderr warning 并回退默认配置
- Phase 7：已完成最小上下文预算、压缩、session split、lineage observability 和 resume。
- Phase 8：已完成最小 file-backed Memory 与只读 Skills 结构层。
- Phase 9：已完成最小 Provider Runtime、OpenAI-compatible provider、规范化 response、usage 和 fallback chain。
- Phase 10 Batch 1：已完成 ToolExecutionContext、命令审批、文件路径安全和 dispatch preflight。
- Phase 10 Batch 2A 至 2E：已完成 `read_file`、`write_file`、文件工具加固、精确替换版 `patch` 和最小 `search_files`。
- Phase 10 Batch 3：已完成 ToolRegistry v2，包括 toolset、check_fn、generation、可用定义过滤和 toolset 查询。
- Phase 10 Batch 4：已完成 Minimal ToolExecutor，包括 Provider definitions 范围快照、模型参数解析、顺序执行和 tool result 追加。
- Phase 10 Batch 5：已完成 Minimal Checkpoint，包括配置、共享 shadow Git store、快照/去重/列举/裁剪、Manager 级恢复、`AIAgent` iteration 生命周期，以及安全 preflight 后的自动写前 checkpoint。

尚未完成：

- 尚未实现 Anthropic、Gemini、Codex Responses、streaming 和完整 provider runtime 状态机。
- 尚未实现 `skill_manage`、自动记忆和完整 system prompt cache invalidation。
- 尚未实现 checkpoint CLI、rollback UX、terminal、并发或 segmented ToolExecutor、gateway、plugins、ACP/TUI 或 Cron。
- 尚未实现 `config set`、`config edit` 等配置写入命令。

## 已确认的关键设计结论

1. Hermes 的核心是 `AIAgent`，不是 CLI 或 Gateway。
2. `run_agent.py` 中的 `AIAgent.run_conversation()` 已经转发到 `agent/conversation_loop.py`，真实 loop 在后者。
3. 最新工具承重链路是：

   ```text
   CLI / Gateway / ACP / TUI
     -> AIAgent
     -> agent/conversation_loop.py
     -> agent/tool_executor.py
     -> model_tools.py
     -> tools/registry.py
     -> tools/*.py
   ```

4. OpenAI 风格消息协议是工具调用的核心：

   ```text
   assistant(tool_calls) -> tool(tool_call_id)
   ```

   任何复刻实现都必须维护这个配对，不然后续 provider 请求会坏。

5. system prompt 必须 session 内稳定。Hermes 通过缓存和 session DB 持久化 system prompt 来保护 prefix cache。
6. session store 是跨 CLI/Gateway/ACP/TUI 的基础。SQLite WAL + FTS5 是必须理解的设计。
7. Gateway 的难点在运行态控制，不是平台 API 本身。
8. Memory 和 Skills 是 Hermes “自改进”定位的核心，但应该在 agent loop、tool loop、session、prompt 稳定后实现。

## 下一个 session 的推荐操作

从 Phase 7 开始，不要跳到真实 provider、gateway、memory 或 skills。

建议步骤：

1. 阅读 `docs/00-overview.md`。
2. 阅读 `docs/02-roadmap.md`。
3. 阅读 `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`。
4. 阅读当前实现文件：
   - `src/learn_hermes_agent/agent/messages.py`
   - `src/learn_hermes_agent/agent/core.py`
   - `src/learn_hermes_agent/providers/base.py`
   - `src/learn_hermes_agent/providers/fake.py`
   - `src/learn_hermes_agent/tools/registry.py`
   - `src/learn_hermes_agent/tools/echo.py`
   - `src/learn_hermes_agent/model_tools.py`
   - `src/learn_hermes_agent/agent/system_prompt.py`
   - `src/learn_hermes_agent/agent/prompt_builder.py`
   - `src/learn_hermes_agent/state/session_db.py`
   - `src/learn_hermes_agent/cli/main.py`
5. 先 review Phase 6 当前代码是否仍可运行。
6. 开始 Phase 7：Context Compression 和 Budget 的设计，不接真实 provider 或 gateway。

## 重要约束

- 不修改 `D:\python-develop\project\hermes-agent`。
- 不把 Hermes 源码整块复制进来。需要先按模块边界复刻。
- 默认不写测试，除非用户明确要求。
- 除文档编写和维护外，代码实现默认只在对话中输出，不写入文件。用户会手动抄写代码；只有用户明确要求写文件、改代码或写测试时，才执行文件写入。
- 每阶段完成后更新本文件，写清楚：
  - 完成了什么。
  - 哪些源码文件已对照。
  - 当前行为如何验证。
  - 下一个阶段是什么。

## 2026-06-03 进度更新

### 本次目标

同步协作原则：文档可由 Codex 维护，代码实现默认只输出不落盘。

### 已完成

- 更新 `AGENTS.md`，新增“代码输出原则”。
- 更新本交接文档的重要约束。

### 设计结论

后续进入实现阶段时，Codex 默认提供代码片段、文件路径建议、运行命令和手工验证步骤；不主动写入代码文件。

### 下一步

历史记录：当时下一步是 Phase 0。该步骤已经完成，后续以 2026-06-04 进度更新为准。

## 2026-06-04 进度更新

### 本次目标

同步 Phase 0、Phase 1、Phase 2 完成状态，为结束当前 session 和开启新 session 做交接。

### 已完成

- Phase 0 已完成并可运行：
  - `pyproject.toml`
  - `src/learn_hermes_agent/__init__.py`
  - `src/learn_hermes_agent/__main__.py`
  - `src/learn_hermes_agent/config.py`
  - `src/learn_hermes_agent/cli/main.py`
- Phase 1 已完成最小 agent/provider 闭环：
  - `src/learn_hermes_agent/agent/messages.py`
  - `src/learn_hermes_agent/agent/core.py`
  - `src/learn_hermes_agent/providers/base.py`
  - `src/learn_hermes_agent/providers/fake.py`
- Phase 2 已完成最小工具注册和手动分发：
  - `src/learn_hermes_agent/tools/registry.py`
  - `src/learn_hermes_agent/tools/echo.py`
  - `src/learn_hermes_agent/model_tools.py`
  - CLI `tools`
  - CLI `call-tool`

### 修改文件

本次只维护文档，不改实现代码。

### 对照的 Hermes 源码

- `tools/registry.py`
- `model_tools.py`
- `run_agent.py`
- `agent/conversation_loop.py`
- `agent/tool_executor.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent chat "hello"
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
uv run learn-hermes-agent call-tool echo '{\"text\":123}'
uv run learn-hermes-agent call-tool missing '{}'
```

已观察到：

- `doctor` 输出运行环境和默认配置。
- `chat` 通过 fake provider 返回 assistant message。
- `tools` 输出 OpenAI function tool 风格 schema。
- `call-tool echo` 能返回 JSON 字符串。
- 非法参数和未知工具会以非零退出码报错。

### 设计结论

- Phase 2 当前只实现 `echo`，这是有意收窄：文件读写工具要等安全边界和路径策略更清楚后再实现。
- 当前工具层的职责边界是：
  - `tools/registry.py`：注册、查找、schema 暴露。
  - `tools/echo.py`：具体工具实现和注册函数。
  - `model_tools.py`：把模型工具名和 JSON 参数分发到 Python handler。
  - `cli/main.py`：提供手动观察入口。
- 下一阶段的核心不再是“能不能手动调用工具”，而是“agent loop 能否维护 OpenAI tool call 消息协议”。

### 下一步

进入 Phase 3：Tool Calling Conversation Loop。

新 session 建议先实现：

1. 扩展 `ChatMessage` helper，增加 `tool_message()`。
2. 扩展 `FakeProviderTransport`，支持 scripted responses。
3. 扩展 `AIAgent.run_conversation()`：
   - 检测 assistant `tool_calls`。
   - append assistant tool_calls message。
   - 调用 `handle_function_call()`。
   - append role=`tool` result message。
   - 再次调用 provider 获取 final assistant message。
4. 增加一个 CLI debug 命令或复用 `chat` 命令观察完整消息序列。

## 2026-06-06 进度更新

### 本次目标

完成 Phase 3：Tool Calling Conversation Loop 的最小实现，让 `AIAgent` 能维护 OpenAI 风格工具调用消息配对。

### 已完成

- 扩展 `ChatMessage` helper：
  - `assistant_message()` 支持 `content=None` 和 `tool_calls`
  - 新增 `tool_message()`
- 扩展 `FakeProviderTransport`：
  - 支持 `scripted_responses`
  - 新增 `tool_demo_provider()`
- 扩展 `model_tools.py`：
  - 保留 strict `handle_function_call()`
  - 新增 `safe_handle_function_call()`，用于 agent loop 将工具错误回灌给模型
- 扩展 `AIAgent.run_conversation()`：
  - 循环调用 provider，最多 `max_iterations`
  - 检测 assistant `tool_calls`
  - append assistant tool_calls message
  - 执行工具
  - append `role=tool` result message
  - 再次调用 provider 获取 final assistant message
- 扩展 CLI：
  - `chat --tool-demo`
  - `chat --show-messages`

### 修改文件

- `src/learn_hermes_agent/agent/messages.py`
- `src/learn_hermes_agent/agent/core.py`
- `src/learn_hermes_agent/providers/fake.py`
- `src/learn_hermes_agent/model_tools.py`
- `src/learn_hermes_agent/cli/main.py`

### 对照的 Hermes 源码

- `agent/conversation_loop.py`
- `agent/tool_executor.py`
- `agent/tool_dispatch_helpers.py`
- `agent/chat_completion_helpers.py`
- `model_tools.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat hello
uv run learn-hermes-agent chat --tool-demo hello --show-messages
uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
```

已观察到：

- `compileall` 通过。
- 普通 `chat` 保持原 fake provider 行为。
- `call-tool echo` 保持 Phase 2 手动分发行为。
- `chat --tool-demo --show-messages` 输出消息序列：

  ```text
  user
  assistant(tool_calls)
  tool
  assistant(final)
  ```

- `role=tool` 消息包含与 assistant tool call 相同的 `tool_call_id`。

### 设计结论

- Phase 3 只实现顺序工具执行，不引入并发、审批、插件 hook、真实 provider、session store 或 gateway。
- 工具调用错误在 agent loop 中通过 `safe_handle_function_call()` 转成 JSON 字符串并作为 tool result 回灌，避免破坏消息序列。
- `handle_function_call()` 继续保持 strict 行为，供 CLI `call-tool` debug 命令暴露错误。
- fake provider 的 `scripted_responses` 只是模拟 provider 已规范化后的返回，不模拟真实 LLM 推理。

### 下一步

进入 Phase 4：Session Store。

Phase 4 边界：

1. 先实现 SQLite `sessions` / `messages` 的最小持久化。
2. 让 CLI 对话后可以观察到 session 和 message 记录。
3. 暂不实现 system prompt builder、真实 provider、gateway、memory、skills 或文件工具。

## 2026-06-06 Phase 4 进度更新

### 本次目标

完成 Phase 4：Session Store 的最小实现，让 CLI 对话消息能写入 SQLite 并在后续命令中观察。

### 已完成

- 新增 `SessionStore`：
  - 初始化 SQLite `sessions` / `messages` 表
  - 启用 WAL
  - 创建 session
  - 追加单条或多条 message
  - 按 session 读取 messages
  - 列出 sessions 和 message_count
- 新增 `get_state_db_path()`，默认数据库路径为 `.learn_hermes/state.db`。
- 新增 `.learn_hermes/` git ignore，避免本地运行态数据库进入提交。
- CLI `chat` 执行后会创建一次性 session 并持久化完整 messages。
- CLI 新增：
  - `sessions`
  - `show-session <session_id>`

### 修改文件

- `.gitignore`
- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/state/__init__.py`
- `src/learn_hermes_agent/state/session_db.py`
- `src/learn_hermes_agent/cli/main.py`

### 对照的 Hermes 源码

- `hermes_state.py`
- `agent/conversation_loop.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.config import get_state_db_path; from learn_hermes_agent.state.session_db import SessionStore; from learn_hermes_agent.agent.messages import user_message; store=SessionStore(get_state_db_path()); store.initialize(); sid=store.create_session(title='manual phase4 check'); store.append_message(sid, user_message('hello')); print(sid); print(store.get_session_messages(sid)); print(store.list_sessions()[0]['message_count'])"
uv run learn-hermes-agent chat --tool-demo hello --show-messages
uv run learn-hermes-agent sessions
uv run learn-hermes-agent show-session <session_id>
uv run learn-hermes-agent chat hello
```

已观察到：

- `compileall` 通过。
- 手工 `SessionStore` 验证可创建 session、写入 message、读回 message，并看到 `message_count=1`。
- `chat --tool-demo --show-messages` 输出完整 Phase 3 消息序列并返回 `session_id`。
- `show-session <session_id>` 可读回持久化后的：

  ```text
  user
  assistant(tool_calls)
  tool
  assistant(final)
  ```

- `sessions` 可列出 session id、title、created_at、updated_at、message_count。
- 普通 `chat hello` 保持可运行，并会输出新建的 `session_id`。

### 设计结论

- Phase 4 最小版采用 CLI 层持久化：`AIAgent` 不直接依赖 `SessionStore`，避免过早污染 agent core。
- 当前每次 `chat` 创建一个新 session，不做 resume。
- `raw_json` 保存完整 message，方便后续扩展字段。
- `role`、`content`、`name`、`tool_call_id`、`tool_calls_json` 是为了轻量观察和查询。
- FTS5、system prompt 持久化、session resume、compression parent-child 关系都留到后续阶段。

### 下一步

进入 Phase 5：System Prompt Builder。

Phase 5 边界：

1. 先实现 stable/context/volatile prompt 分层。
2. 先读取本项目 `AGENTS.md` 作为 context file。
3. 在 session store 中预留或接入 system prompt 持久化。
4. 暂不接真实 provider、gateway、memory、skills 或 context compression。

## 2026-06-07 Phase 5 进度更新

### 本次目标

完成 Phase 5：System Prompt Builder 的最小实现，让 agent 请求 provider 时携带稳定的 system prompt，并把该 prompt 持久化到 session 行。

### 已完成

- 新增稳定 system prompt 常量 `STABLE_SYSTEM_PROMPT`。
- 新增 `PromptLayers`，表达 stable/context/volatile 三层 prompt。
- 新增 `PromptBuilder`：
  - stable layer 来自 `STABLE_SYSTEM_PROMPT`
  - context layer 读取本项目 `AGENTS.md`
  - volatile layer 当前保留为空
  - 对 context file 做最小 prompt injection marker 扫描
- 扩展 `AIAgent.run_conversation()`：
  - 支持 `system_prompt` 参数
  - 调 provider 前临时 prepend `role=system` message
  - 返回的 conversation messages 仍只包含 user/assistant/tool，不包含 system message
- 扩展 `SessionStore`：
  - `sessions` 表增加 `system_prompt` 字段
  - 初始化时对旧表执行轻量 schema migration
  - `create_session()` 支持写入 system prompt
  - `get_session()` 可读回 system prompt
- 扩展 CLI `chat`：
  - 每次 chat 构建 system prompt
  - 创建 session 时保存 system prompt
  - 持久化的 messages 不写入 system role

### 修改文件

- `src/learn_hermes_agent/agent/system_prompt.py`
- `src/learn_hermes_agent/agent/prompt_builder.py`
- `src/learn_hermes_agent/agent/core.py`
- `src/learn_hermes_agent/state/session_db.py`
- `src/learn_hermes_agent/cli/main.py`

### 对照的 Hermes 源码

- `agent/conversation_loop.py`
- `agent/chat_completion_helpers.py`
- `hermes_state.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.agent.prompt_builder import PromptBuilder; prompt=PromptBuilder().build(); print(prompt[:80]); print('AGENTS.md' in prompt)"
uv run learn-hermes-agent chat --tool-demo phase5-agent-check --show-messages
uv run learn-hermes-agent show-session <session_id>
uv run python -c "from learn_hermes_agent.config import get_state_db_path; from learn_hermes_agent.state.session_db import SessionStore; store=SessionStore(get_state_db_path()); store.initialize(); session=store.list_sessions()[0]; print(bool(store.get_session(session['id'])['system_prompt']))"
```

已观察到：

- `compileall` 通过。
- `PromptBuilder().build()` 输出包含 stable prompt，并包含 `AGENTS.md` context。
- provider 收到的第一条 request message 是 `role=system`。
- `AIAgent.run_conversation()` 返回的 messages 不包含 system role。
- `show-session <session_id>` 只显示 user/assistant/tool/assistant 序列。
- SQLite `sessions.system_prompt` 能读回非空 prompt。

### 设计结论

- system prompt 是 provider request layer 的输入，不是普通 conversation message。
- session 行保存 `system_prompt`，为后续 session resume、prefix cache 和 context compression 做准备。
- 当前只读取 `AGENTS.md`，暂不读取 `SOUL.md`、memory、skills 或用户画像。
- 当前 prompt injection 扫描只是学习阶段的最小 marker scan，不等价于完整安全机制。
- `volatile` layer 当前为空，后续进入 memory/skills 阶段再接入。

### 下一步

进入 Phase 6：CLI 和配置。

Phase 6 边界：

1. 建立更清晰的 CLI command 分发结构。
2. 对照真实 Hermes 的 `hermes_cli/commands.py`，先实现最小 `CommandDef` / `COMMAND_REGISTRY`。
3. 对照真实 Hermes 的 `hermes_cli/_parser.py` 和 `hermes_cli/main.py`，让 `chat` 负责进入最小交互入口；不要凭空新增偏离真实项目的入口。
4. 支持最小 slash command：`/help`、`/new`、`/model`、`/tools`、`/sessions`、`/exit`。
5. 下一批再支持读取本地 `config.yaml`。
6. 暂不接真实 provider、gateway、memory、skills、TUI、prompt_toolkit、model picker 或文件工具。

## 2026-06-07 Phase 6 设计校正

### 本次目标

在开始写 Phase 6 代码前，重新对照真实 Hermes 源码，校正 CLI/config 的复刻方向。

### 已完成

- 对照真实 Hermes CLI 入口：
  - `pyproject.toml`
  - `hermes`
  - `hermes_cli/main.py`
  - `hermes_cli/_parser.py`
  - `hermes_cli/commands.py`
  - `hermes_cli/config.py`
  - 根目录 `cli.py`
- 确认真实 Hermes 不是单一 CLI 文件承载所有行为，而是：
  - `hermes_cli/main.py` 作为安装后的 `hermes` 入口
  - `hermes_cli/_parser.py` 构建 top-level parser 和 `chat` parser
  - 根目录 `cli.py` 承载经典 REPL 和 agent turn 执行
  - `hermes_cli/commands.py` 维护 slash command registry
  - `hermes_cli/config.py` 读取 `~/.hermes/config.yaml`

### 修改文件

本次只更新文档，不修改实现代码。

### 对照的 Hermes 源码

- `pyproject.toml`
- `hermes`
- `hermes_cli/main.py`
- `hermes_cli/_parser.py`
- `hermes_cli/commands.py`
- `hermes_cli/config.py`
- `cli.py`

### 设计结论

- Phase 6 不应先凭空新增一个独立 `repl` 子命令；更贴近 Hermes 的做法是让 `chat` 承担交互入口。
- `CommandDef` / `COMMAND_REGISTRY` 是有真实源码依据的，应作为 Phase 6 第一批实现。
- slash command registry 只描述命令元数据和解析结果；具体执行逻辑仍放在 CLI runtime 中，避免 registry 同时承担 UI、session、provider 职责。
- `config.yaml` 是真实 Hermes 的配置面，但当前项目还没有 `PyYAML` 依赖；建议作为 Phase 6 第二批实现。
- 暂不复刻 TUI、`prompt_toolkit`、model picker、resume by title、provider runtime resolver、gateway command。

### 下一步

Phase 6 Batch 1：

1. 新增 `src/learn_hermes_agent/cli/commands.py`。
2. 定义最小 `CommandDef` 和 `COMMAND_REGISTRY`。
3. 实现 `resolve_command()` 和 `format_help_lines()`。
4. 修改 `cli/main.py`：让 `chat` 的 `message` 变成可选。
5. 当 `chat` 没有 message 时进入最小交互循环。
6. 在交互循环中支持 `/help`、`/new`、`/model`、`/tools`、`/sessions`、`/exit`。
7. 保持 provider 为 fake，不接真实 provider。

## 2026-06-08 Phase 6 Batch 1 进度更新

### 本次目标

完成 Phase 6 第一批：复刻真实 Hermes 的 slash command registry 方向，并让 `chat` 无 message 时进入最小交互循环。

### 已完成

- 新增 `src/learn_hermes_agent/cli/commands.py`：
  - `CommandDef`
  - `COMMAND_REGISTRY`
  - `resolve_command()`
  - `parse_slash_command()`
  - `format_help_lines()`
- 扩展 `src/learn_hermes_agent/cli/main.py`：
  - `chat` 的 `message` 参数改为可选
  - 新增 `build_agent()`
  - 新增 `run_interactive_chat()`
  - 支持连续对话 history
  - 每轮只持久化新增 messages，避免重复写入历史
  - `/new` 创建新 session 并清空 history
  - `/help` 输出 registry 生成的帮助文本
  - `/model` 显示当前 fake provider/model
  - `/tools` 显示工具 schema
  - `/sessions` 显示 session 列表
  - `/exit`、`/quit`、`/q` 退出交互
  - 未知 slash command 输出 `unknown command`
  - 已注册但未实现的 slash command 有兜底提示

### 修改文件

- `src/learn_hermes_agent/cli/commands.py`
- `src/learn_hermes_agent/cli/main.py`

### 对照的 Hermes 源码

- `hermes_cli/commands.py`
- `hermes_cli/_parser.py`
- `hermes_cli/main.py`
- 根目录 `cli.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat "phase6 smoke"
@(
'/help'
'/abc'
'hello batch1'
'/new phase6 batch1'
'hello after new'
'/model'
'/tools'
'/sessions'
'/q'
) | uv run learn-hermes-agent chat
```

已观察到：

- `compileall` 通过。
- 普通单轮 `chat "phase6 smoke"` 保持可运行。
- `/help` 输出命令分组，`Session` 分类不重复分裂。
- `/abc` 输出未知命令提示。
- 普通消息进入 agent 并返回 fake assistant 响应。
- `/new phase6 batch1` 创建新 session。
- `/model` 输出 `provider: fake` 和 `model: fake-basic`。
- `/tools` 输出当前 `echo` tool schema。
- `/sessions` 可观察交互 session 和 message count。
- `/q` 作为 `/exit` 别名正常退出。

### 设计结论

- `commands.py` 只做 slash command 元数据和解析，不承担执行逻辑。
- `main.py` 的交互循环负责执行命令，符合当前学习项目的最小边界。
- `chat` 无 message 进入交互循环，比新增独立 `repl` 子命令更贴近真实 Hermes 的入口方向。
- 当前交互模式使用 `input()`，不引入 `prompt_toolkit`。
- 当前 provider 仍然是 fake，不做真实 provider runtime resolver。

### 下一步

进入 Phase 6 Batch 2：`config.yaml` 最小读取。

Batch 2 边界：

1. 引入 `PyYAML` 或明确选择一个临时无依赖方案。
2. 读取 `.learn_hermes/config.yaml`。
3. 与 `DEFAULT_CONFIG` 做深合并。
4. 支持覆盖 `model.provider`、`model.default`、`agent.max_iterations`。
5. 配置解析失败时给出清晰 stderr 提示，并回退默认配置。
6. 暂不实现 `config set`、`config edit`、真实 provider 或 credential 管理。

## 2026-06-08 Phase 6 Batch 2 进度更新

### 本次目标

完成 Phase 6 第二批：实现 `.learn_hermes/config.yaml` 的最小读取、默认配置合并和错误降级。

### 已完成

- `pyproject.toml` 引入 `PyYAML` 依赖。
- `src/learn_hermes_agent/config.py` 支持读取 `.learn_hermes/config.yaml`。
- 新增 `read_raw_config()`：
  - 配置文件不存在时返回空配置。
  - YAML 解析失败时输出 stderr warning。
  - YAML 顶层不是 object 时输出 stderr warning。
  - 异常配置会回退默认配置。
- 新增 `load_config()` 的深合并流程：
  - `DEFAULT_CONFIG`
  - user config
  - `_deep_merge()`
  - `_normalize_config()`
- 支持配置覆盖：
  - `model.provider`
  - `model.default`
  - `agent.max_iterations`
- CLI `doctor` 和 `chat` 已能读取配置中的 fake model 名称。

### 修改文件

- `pyproject.toml`
- `src/learn_hermes_agent/config.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `hermes_cli/config.py`
- `cli-config.yaml.example`
- `hermes_cli/runtime_provider.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.config import load_config; c=load_config(); print(c['model']['provider']); print(c['model']['default']); print(c['agent']['max_iterations'])"
uv run learn-hermes-agent doctor
uv run learn-hermes-agent chat "config check"
```

已观察到：

- `compileall` 通过。
- 当前 `.learn_hermes/config.yaml` 可正常覆盖配置：

  ```text
  fake
  fake-configured
  3
  ```

- `doctor` 输出：

  ```text
  provider: fake
  model: fake-configured
  max_iterations: 3
  ```

- `chat "config check"` 输出 fake provider 响应，并使用 `fake-configured` 作为 model 名称。
- 临时非法 YAML 配置会触发 stderr warning，并回退：

  ```text
  fake
  fake-basic
  10
  ```

### 设计结论

- `yaml.safe_load()` 只负责解析 YAML，并在非法 YAML 时抛出解析异常。
- `warning: failed to parse config file ...; using defaults.` 是本项目 `read_raw_config()` 的异常处理逻辑，不是 PyYAML 自动输出。
- 当前只做最小配置读取和 normalize，不实现 `config set`、`config edit`、真实 provider resolver、credential 管理。
- provider 仍保持 fake；配置中的 provider/model 当前只影响可观察输出和 fake provider 初始化。

### 下一步

进入 Phase 7：Context Compression 和 Budget。

Phase 7 边界：

1. 先实现 `ContextCompressor` 最小版和粗略 token 估算。
2. `ContextCompressor` 负责 context token 阈值判断和压缩。
3. `IterationBudget` 只表示 agent/tool loop 最大迭代次数，不表示 context token budget；如需抽出，放到后续批次。
4. 压缩结果先作为 deterministic summary message 进入 working messages。
5. 暂不实现 parent-child session split、compression lock、真实 LLM summary、真实 provider、memory、skills、gateway 或完整 session resume。

## 2026-06-08 Phase 7 设计校正

### 本次目标

校正 Phase 7 的设计边界，确保 context compression 方向对齐真实 Hermes，而不是把 `IterationBudget` 误当作上下文 token budget。

### 已完成

- 对照真实 Hermes 源码确认：
  - `agent/context_compressor.py` 承担 context compression。
  - `agent/conversation_loop.py` 在 provider 调用前执行 preflight compression。
  - `agent/conversation_compression.py` 负责压缩后的 session split 和 session_id rotation。
  - `hermes_state.py` 使用 `parent_session_id` 表示 compression continuation chain。
  - `agent/iteration_budget.py` 只负责 agent/tool loop 迭代次数。
  - `cli-config.yaml.example` 中真实配置区是 `compression.*`。
- 新增设计文档：
  - `docs/plans/2026-06-08-phase-7-context-compression-design.md`
- 更新路线图 Phase 7，明确 Batch 1 先实现 deterministic `ContextCompressor`，不做 parent-child session split。

### 修改文件

- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- `docs/plans/2026-06-08-phase-7-context-compression-design.md`

### 对照的 Hermes 源码

- `agent/context_compressor.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `agent/iteration_budget.py`
- `hermes_state.py`
- `cli-config.yaml.example`

### 设计结论

- Phase 7 Batch 1 先做 preflight context compression。
- `ContextCompressor` 负责粗略 token 估算、阈值判断、head/tail 保护和 middle summary。
- 当前学习项目先使用 deterministic summary，不调用真实 LLM。
- 压缩后的 working messages 会继续进入 provider 和 CLI 持久化，避免下一轮重复携带被压缩的中间窗口。
- `parent_session_id` 和 compression session split 是真实 Hermes 设计，但放到后续批次。

### 下一步

开始 Phase 7 Batch 1，实现：

1. `src/learn_hermes_agent/agent/context_compressor.py`
2. `config.py` 新增 `compression` 配置读取和 normalize。
3. `AIAgent.run_conversation()` provider 调用前执行 preflight compression。

## 2026-06-08 Phase 7 Batch 1 进度更新

### 本次目标

实现最小 deterministic context compression，并接入 `AIAgent` 的 provider 调用前 preflight compression。

### 已完成

- 新增 `src/learn_hermes_agent/agent/context_compressor.py`：
  - `CompressionConfig`
  - `ContextCompressor`
  - `estimate_request_tokens_rough()`
  - `estimate_message_tokens_rough()`
  - `estimate_text_tokens_rough()`
  - deterministic summary message 构造
- 扩展 `config.py`：
  - `DEFAULT_CONFIG["compression"]`
  - `compression.enabled`
  - `compression.context_length`
  - `compression.threshold`
  - `compression.protect_first_n`
  - `compression.protect_last_n`
  - 非法 compression 配置回退默认值
- 扩展 `AIAgent`：
  - 构造时接收 `ContextCompressor`
  - `last_context_compressed`
  - provider 调用前执行 `context_compressor.compress()`
- 扩展 CLI：
  - `build_context_compressor()`
  - `build_agent()` 从 config 构造 compressor
  - `doctor` 输出 compression 配置
  - 单轮 `chat` 触发压缩时输出 `context compressed: yes`
  - 交互模式触发压缩时输出 `context compressed: yes`
- 扩展 `SessionStore`：
  - `replace_messages()`
- 修复交互模式压缩后的持久化问题：
  - 未压缩时 append 本轮新增 messages
  - 压缩时 replace 当前 session messages
  - 每轮统一更新 `history = messages`

### 修改文件

- `src/learn_hermes_agent/agent/context_compressor.py`
- `src/learn_hermes_agent/agent/core.py`
- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/cli/main.py`
- `src/learn_hermes_agent/state/session_db.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `agent/context_compressor.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `hermes_state.py`
- `cli-config.yaml.example`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.config import load_config; from learn_hermes_agent.cli.main import build_agent; agent=build_agent(load_config()); print(agent.context_compressor.config); print(agent.last_context_compressed)"
uv run python -c "<构造长 history，低 context_length 触发 AIAgent preflight compression>"
uv run learn-hermes-agent chat "phase7 integration smoke"
uv run learn-hermes-agent chat --tool-demo "phase7 tool smoke" --show-messages
@('hello one','hello two','/q') | uv run learn-hermes-agent chat --show-messages
uv run python -c "<验证 compression 分支 replace_messages 后 DB 中包含 summary 和 final assistant>"
uv run learn-hermes-agent doctor
uv run learn-hermes-agent chat "compression visibility smoke"
@("<多轮长输入>", '/q') | uv run learn-hermes-agent chat --show-messages
```

已观察到：

- `compileall` 通过。
- `build_agent()` 能构造 `CompressionConfig(enabled=True, context_length=2000, threshold=0.5, protect_first_n=2, protect_last_n=6)`。
- 长 history 低阈值用例中 `agent.last_context_compressed=True`。
- 16 条 history 经压缩后可持久化为 8 条 working messages。
- 持久化 messages 中包含 `[Context compression summary]` 和 final assistant message。
- 普通 `chat` 和 `chat --tool-demo --show-messages` 未被破坏。
- 交互模式连续两轮时，未压缩路径只输出本轮新增 messages。
- `doctor` 可观察到 compression 配置。
- 普通短消息不触发 compression notice。
- 低阈值临时配置的多轮长输入中可观察到 `context compressed: yes`。

### 设计结论

- Batch 1 不做真实 LLM summary；summary 是 deterministic，便于学习和验证。
- Batch 1 不做 parent-child session split；压缩后先用 `replace_messages()` 更新当前 session。
- `AIAgent` 不直接依赖 `config.py`，由 CLI 从 config 构造 `ContextCompressor` 后注入。
- `last_context_compressed` 是当前阶段的最小可观察状态，CLI 已据此输出 compression notice。

### 下一步

Phase 7 Batch 2：抽出 `IterationBudget`。

建议先实现：

1. 新增 `src/learn_hermes_agent/agent/iteration_budget.py`。
2. `IterationBudget` 只负责 `max_iterations` 的 consume/remaining 语义。
3. `AIAgent.run_conversation()` 使用 `IterationBudget` 替代 `for range(max_iterations)`。
4. 暂不接 subagent、refund、线程锁或 `execute_code` 特例；这些是真实 Hermes 的后续复杂度。

## 2026-06-08 Phase 7 Batch 2 进度更新

### 本次目标

抽出 `IterationBudget`，让 `max_iterations` 的语义从裸 `for range(...)` 变成可观察的 agent loop budget。

### 已完成

- 新增 `src/learn_hermes_agent/agent/iteration_budget.py`：
  - `IterationBudget`
  - `consume()`
  - `used`
  - `remaining`
- 扩展 `AIAgent`：
  - 构造时初始化 `self.iteration_budget`
  - 每次 `run_conversation()` 开始时重建本轮 budget
  - 使用 `while self.iteration_budget.consume()` 替代 `for range(self.max_iterations)`
  - 预算耗尽时抛出 `Exceeded max_iterations=...`

### 修改文件

- `src/learn_hermes_agent/agent/iteration_budget.py`
- `src/learn_hermes_agent/agent/core.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `agent/iteration_budget.py`
- `agent/conversation_loop.py`

### 验证方式

当前实现已用以下命令验证：

```powershell
uv run python -m compileall -q src
uv run python -c "from learn_hermes_agent.agent.iteration_budget import IterationBudget; b=IterationBudget(2); print(b.remaining); print(b.consume()); print(b.consume()); print(b.consume()); print(b.used); print(b.remaining)"
uv run learn-hermes-agent chat --tool-demo "budget smoke" --show-messages
uv run python -c "from learn_hermes_agent.agent.core import AIAgent; from learn_hermes_agent.providers.fake import tool_demo_provider; agent=AIAgent(tool_demo_provider(), max_iterations=1); agent.run_conversation('needs two provider calls')"
```

已观察到：

- `compileall` 通过。
- `IterationBudget(2)` 输出：

  ```text
  2
  True
  True
  False
  2
  0
  ```

- `chat --tool-demo` 仍能完成 `user -> assistant(tool_calls) -> tool -> assistant(final)`。
- `max_iterations=1` 且 tool demo 需要两次 provider call 时，会抛出：

  ```text
  RuntimeError: Exceeded max_iterations=1 before receiving a final assistant message.
  ```

### 设计结论

- `IterationBudget` 只管 agent/provider/tool loop 的迭代次数。
- `IterationBudget` 不参与 context token 估算，也不决定 compression threshold。
- 当前学习项目暂不实现真实 Hermes 里的线程锁、`refund()`、subagent 独立 budget 或 `execute_code` 特例。

### 下一步

Phase 7 Batch 3：设计最小 compression session split。

建议先做设计，不直接写代码：

1. 对照真实 Hermes 的 `conversation_compression.py` 和 `hermes_state.py`。
2. 评估是否在当前学习项目中新增：
   - `sessions.parent_session_id`
   - `sessions.end_reason`
   - `SessionStore.end_session()`
   - `SessionStore.create_session(parent_session_id=...)`
3. 决定压缩时继续使用当前 session replace，还是切到 child session。
4. 暂不实现 compression lock、gateway session projection、memory hooks 或完整 resume。

## 2026-06-09 Phase 7 Batch 3 进度更新

### 本次目标

实现最小 compression session split：压缩发生后不再覆盖旧 session，而是结束旧 session、创建 child session，并用 `parent_session_id` 保留 lineage。

### 已完成

- 扩展 `sessions` schema：
  - `parent_session_id`
  - `end_reason`
  - `ended_at`
- 初始化时为旧数据库补齐新增列，并创建 `parent_session_id` index。
- 扩展 `SessionStore.create_session()`，支持 `parent_session_id`。
- 新增 `SessionStore.end_session(session_id, reason)`。
- `get_session()` 和 `list_sessions()` 返回 parent/end 相关字段。
- 交互模式触发 compression 时：
  - 结束旧 session，`end_reason="compression"`。
  - 创建 child session，继承 `system_prompt`。
  - 写入 compressed working messages。
  - 更新当前 `session_id` 并输出新 session id。

### 修改文件

- `src/learn_hermes_agent/state/session_db.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `agent/conversation_compression.py`
- `hermes_state.py`

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
@("one <long>", "two <long>", "three <long>", "/sessions", "/q") | uv run learn-hermes-agent chat
```

已观察到：

- `compileall` 通过。
- 第三轮长输入触发 `context compressed: yes`。
- CLI 输出新的 child `session_id`。
- `/sessions` 中 parent session 的 `end_reason` 为 `compression`。
- child session 的 `parent_session_id` 指向 parent session。
- parent 保留压缩前 messages，child 保存 compressed working messages。

### 设计结论

- Batch 3 对齐真实 Hermes 的 session lineage 方向：compression 是 session 边界，不是简单删除或覆盖旧历史。
- 当前只在 CLI 交互模式里做 session split；单轮 `chat` 和非压缩路径继续保持原行为。
- 暂不实现 compression lock、gateway session projection、resume tip、memory hooks、system prompt rebuild 或真实 provider usage。

### 下一步

进入 Phase 7 Batch 4：收尾 Phase 7 的可观察性和边界整理。

建议先做：

1. 检查 `sessions` 输出字段是否足够清晰，必要时微调排序或显示。
2. 整理 Phase 7 当前剩余项：compression lock 是否继续后延，还是实现一个最小 no-op 保护接口。
3. 明确 Phase 8 是否开始 memory/skills，还是先做 session resume 的最小入口。

## 2026-06-09 Phase 7 Batch 4 进度更新

### 本次目标

为 Phase 7 收口最小 session lineage 可观察性，让 compression parent/child 关系可以通过 CLI 清楚查看。

### 已完成

- 新增 `SessionStore.get_session_chain(session_id)`，从当前 session 往上追 parent，并返回 root -> current。
- 新增 `SessionStore.get_compression_tip(session_id)`，当传入 compression parent 时追到最新 continuation child。
- 新增私有 helper `_get_latest_child_session_id(parent_session_id)`。
- 增强 `show-session <session_id>` 输出：
  - `session`
  - `compression_tip`
  - `lineage`
  - `messages`
- missing session 会输出 stderr 错误并返回非零退出码。

### 修改文件

- `src/learn_hermes_agent/state/session_db.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `hermes_state.py`
- `agent/conversation_compression.py`

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run python -c "from pathlib import Path; import tempfile; from learn_hermes_agent.state.session_db import SessionStore; tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); s=SessionStore(Path(tmp.name)/'state.db'); s.initialize(); p=s.create_session(title='parent'); s.end_session(p, 'compression'); c=s.create_session(title='child', parent_session_id=p); print(s.get_compression_tip(p) == c); print([row['id'] for row in s.get_session_chain(c)] == [p, c]); tmp.cleanup()"
uv run learn-hermes-agent show-session <known_session_id>
uv run learn-hermes-agent show-session missing-session-id
@("one <long>", "two <long>", "three <long>", "/sessions", "/q") | uv run learn-hermes-agent chat
uv run learn-hermes-agent show-session <parent_session_id>
uv run learn-hermes-agent show-session <child_session_id>
```

已观察到：

- `compileall` 通过。
- helper 验证输出：

  ```text
  True
  True
  ```

- 普通 session 的 `compression_tip` 等于自身，`lineage` 只包含自身。
- missing session 返回退出码 `1`，并输出 `error: session not found: ...`。
- 真实 compression split 中 parent 的 `end_reason` 为 `compression`。
- parent 的 `compression_tip` 指向 child。
- child 的 `lineage` 包含 parent 和 child。
- child 的 `messages` 包含 `[Context compression summary]`。

### 设计结论

- Batch 4 只增强 lineage 可观察性，不改变 compression 触发、chat 持久化、session list projection 或 provider 行为。
- `get_compression_tip()` 在链路不完整时返回当前可确认的 session id，而不是抛错或返回 `None`，保证观察命令可用。
- Phase 7 最小版已经收口：context compression、iteration budget、session split、lineage/tip observability 都已具备。
- compression lock、真实 provider usage、LLM summary、gateway projection、resume redirect 和 memory hooks 继续后延。

### 下一步

进入 Phase 8 前建议先做一个短决策：是否直接开始 memory/skills，还是先补一个最小 `resume` 入口来消费 Batch 4 已建立的 `compression_tip`。

## 2026-06-09 Phase 7.5 Minimal Resume 进度更新

### 本次目标

在进入 Memory/Skills 前补齐最小 session resume 入口，让用户可以从已有 session 继续交互，并在传入 compression parent 时自动跳到 latest continuation child。

### 已完成

- `chat` 子命令新增 `--resume SESSION_ID` 参数。
- `run_chat()` 拒绝 `chat --resume <id> "message"` one-shot resume，当前只支持交互模式。
- `run_interactive_chat()` 支持从 persisted session 初始化：
  - 普通 session 直接读取该 session。
  - compression parent 通过 `get_compression_tip()` 跳到 latest continuation child。
  - 使用 tip session 的 `system_prompt`，缺失时 fallback 到 `build_system_prompt()`。
  - 使用 tip session messages 作为 history。
- resume 启动时输出当前 `session_id` 和 `resumed_from`。
- missing session 返回非零退出码并输出 stderr 错误。

### 修改文件

- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `hermes_state.py`
  - `get_compression_tip()`
  - `resolve_resume_session_id()`
- `agent/conversation_compression.py`

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat --help
uv run learn-hermes-agent chat --resume missing "hello"
uv run learn-hermes-agent chat --resume missing
uv run learn-hermes-agent chat --resume <compression_parent_session_id>
uv run learn-hermes-agent show-session <parent_session_id>
uv run learn-hermes-agent show-session <child_session_id>
uv run learn-hermes-agent chat "resume smoke"
@("hello normal", "/q") | uv run learn-hermes-agent chat
```

已观察到：

- `compileall` 通过。
- `chat --help` 显示 `--resume SESSION_ID`。
- one-shot resume 返回退出码 `1`，并提示当前只支持 interactive resume。
- missing session 返回退出码 `1`，并提示 `error: session not found: missing`。
- 用 compression parent resume 时，启动输出的 `session_id` 是 child/tip，`resumed_from` 是 parent。
- parent `message_count` 保持不变。
- child messages 追加了 resume 后的新 user message 和 fake assistant response。
- 普通 one-shot `chat` 和普通交互 `chat` 仍可运行。

### 设计结论

- Phase 7.5 只做 interactive resume，不做 one-shot resume，避免复制一套单轮持久化和 compression split 逻辑。
- resume 消费 Phase 7 Batch 4 的 `compression_tip`，把 lineage 可观察性转化为可用的 continuation 能力。
- 当前仍不进入真实 provider、Memory、Skills、session list projection 或 title/search resume。

### 下一步

现在可以进入 Phase 8 的结构版 Memory/Skills 设计；由于仍未接真实 LLM provider，建议先实现 memory/skills 的存储、扫描、schema 和注入边界，不期待 fake provider 展现自主使用行为。

## 2026-06-09 Phase 8 Batch 1 Memory 进度更新

### 本次目标

在不接真实 LLM provider 的前提下，先实现 Memory 的结构层：本地文件存储、手工 `memory` tool、system prompt snapshot 注入边界。

### 已完成

- 新增 `get_memory_dir_path()`，统一使用 `.learn_hermes/memories/` 作为本地 memory 目录。
- 新增 `MemoryStore`：
  - `MEMORY.md` 保存 agent/project 长期记忆。
  - `USER.md` 保存用户画像/偏好类记忆。
  - 支持 `load()`、`read()`、`add()`、`replace()`、`remove()` 和 `system_prompt_block()`。
  - 写入时做基础内容校验，prompt 注入前做基础 marker 过滤。
- 新增内置 `memory` tool：
  - `action`: `add` / `read` / `replace` / `remove`
  - `target`: `memory` / `user`
  - 通过 `call-tool memory ...` 可手工写读长期记忆。
- `discover_builtin_tools()` 现在同时注册 `echo` 和 `memory`。
- `PromptBuilder` 支持可选 `MemoryStore`，并把 memory snapshot 放进 volatile layer。
- `build_system_prompt()` 创建 `MemoryStore(get_memory_dir_path())`，让新 session 的 `system_prompt` 固化当前 memory snapshot。

### 修改文件

- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/agent/memory_store.py`
- `src/learn_hermes_agent/tools/memory.py`
- `src/learn_hermes_agent/tools/registry.py`
- `src/learn_hermes_agent/agent/prompt_builder.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- `docs/plans/2026-06-09-phase-8-memory-design.md`
- `docs/plans/2026-06-09-phase-8-memory-plan.md`

### 对照的 Hermes 源码

- `tools/memory_tool.py`
- `agent/memory_manager.py`
- `agent/memory_provider.py`
- `tools/skills_tool.py`
- `agent/skill_utils.py`

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool memory '{\"action\":\"add\",\"target\":\"memory\",\"content\":\"memory_one\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"replace\",\"target\":\"memory\",\"old_text\":\"memory_one\",\"content\":\"memory_two\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"remove\",\"target\":\"memory\",\"old_text\":\"memory_two\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"read\",\"target\":\"memory\"}'
uv run learn-hermes-agent call-tool echo '{\"text\":\"echo_ok\"}'
uv run learn-hermes-agent chat --resume missing
```

另用临时 `LEARN_HERMES_HOME` 验证：写入 `project_uses_uv` 后运行新 `chat`，新 session 的 `system_prompt` 同时包含 `Persistent memory snapshot` 和 `project_uses_uv`。

已观察到：

- `compileall` 通过。
- `tools` 输出包含 `echo` 和 `memory`。
- `memory` tool 的 add/read/replace/remove 都能通过 CLI 链路执行。
- `MEMORY.md` 文件会被真实写入。
- 新 session 的 `system_prompt` 会固化当前 memory snapshot。
- `echo` 工具仍可调用。
- `chat --resume missing` 仍返回预期错误路径。

### 设计结论

- 当前 Batch 1 只做 Memory 的可运行结构层，不做自动记忆。
- fake provider 不会自主调用 `memory` tool；当前价值在于 schema、存储、tool 边界和 prompt 注入链路已经打通。
- `system_prompt` 使用创建 session 时的 frozen snapshot；后续 memory 变化不会 retroactively 改写旧 session。
- 真实 Hermes 的 `MemoryManager`、external provider、prefetch/sync hooks 和 compression hooks 后延。

### 下一步

进入 Phase 8 Batch 2：Skills 结构层。建议先实现 `skills/` 目录扫描、`SKILL.md` frontmatter 解析和 CLI/tool 可观察入口，不期待 fake provider 自主选择 skill。

## 2026-06-09 Phase 8 Batch 2 Skills 进度更新

### 本次目标

在不接真实 LLM provider 的前提下，先实现 Skills 的只读结构层：本地 `skills/` 扫描、`SKILL.md` frontmatter 解析、`skills_list` / `skill_view` 工具、CLI 可观察入口和轻量 skills index 注入。

### 已完成

- 新增 `get_skills_dir_path()`，统一使用 `.learn_hermes/skills/` 作为本地 skills 目录。
- 新增 `SkillLibrary`：
  - 扫描 `.learn_hermes/skills/**/SKILL.md`。
  - 解析 YAML frontmatter 中的 `name` 和 `description`。
  - 支持目录名 fallback、正文首个非标题行 fallback、name/description 长度限制。
  - 支持 category 过滤。
  - 支持查看 `SKILL.md` 和 skill 目录内 linked file。
  - 拦截绝对路径、`..` 和越界 linked file 访问。
  - 使用 `utf-8-sig` 兼容 PowerShell 写出的 UTF-8 BOM。
- 新增内置只读 tools：
  - `skills_list`
  - `skill_view`
- `discover_builtin_tools()` 现在注册 `echo`、`memory`、`skills_list` 和 `skill_view`。
- CLI 新增：
  - `skills [--category CATEGORY]`
  - `view-skill NAME [FILE_PATH]`
- `PromptBuilder` 支持可选 `SkillLibrary`，并把轻量 `Available skills` index 放进 volatile layer。
- `build_system_prompt()` 同时传入 `MemoryStore` 和 `SkillLibrary`，让新 session 的 `system_prompt` 固化当前 memory snapshot 和 skills index。

### 修改文件

- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/agent/skills.py`
- `src/learn_hermes_agent/tools/skills.py`
- `src/learn_hermes_agent/tools/registry.py`
- `src/learn_hermes_agent/agent/prompt_builder.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- `docs/plans/2026-06-09-phase-8-skills-design.md`
- `docs/plans/2026-06-09-phase-8-skills-plan.md`

### 对照的 Hermes 源码

- `tools/skills_tool.py`
- `agent/skill_utils.py`
- `agent/agent_init.py`
- `agent/conversation_loop.py`

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent skills
uv run learn-hermes-agent skills --category planning
uv run learn-hermes-agent view-skill writing-plans
uv run learn-hermes-agent view-skill writing-plans references/example.md
uv run learn-hermes-agent call-tool skills_list '{\"category\":\"planning\"}'
uv run learn-hermes-agent call-tool skill_view '{\"name\":\"writing-plans\"}'
uv run learn-hermes-agent call-tool echo '{\"text\":\"echo_ok\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"read\",\"target\":\"memory\"}'
uv run learn-hermes-agent chat --resume missing
```

另用临时 `LEARN_HERMES_HOME` 验证：创建 sample skill 后运行新 `chat`，新 session 的 `system_prompt` 包含 `Available skills` 和 `writing-plans`，且不包含 linked file 内容 `Example reference`。

已观察到：

- `compileall` 通过。
- `tools` 输出包含 `echo`、`memory`、`skills_list` 和 `skill_view`。
- `skills` CLI 能列出 sample skill。
- `skills --category planning` 能过滤 sample skill。
- `view-skill writing-plans` 能读取 `SKILL.md`。
- `view-skill writing-plans references/example.md` 能读取 linked file。
- `view-skill writing-plans ../outside.md` 会返回越界路径错误。
- `call-tool skills_list` 和 `call-tool skill_view` 可运行。
- `echo` 和 `memory` 工具仍可调用。
- `chat --resume missing` 仍返回预期错误路径。

### 设计结论

- 当前 Batch 2 只实现只读 Skills 结构层，不实现 `skill_manage`。
- fake provider 不会自主选择 skill；当前价值在于 schema、扫描、查看、CLI 和 prompt index 边界已经打通。
- system prompt 只注入轻量 skill index，不自动注入完整 `SKILL.md` 或 linked file 内容。
- `skill_manage`、自动 skill review、background update、external skill dirs、platform gating 和 plugin skills 后延。

### 下一步

进入 Phase 8 Batch 3 前建议先做决策：是否补一个最小 `skill_manage` 写入能力，还是先进入 Phase 9 Provider Runtime。由于 `skill_manage` 涉及文件写入和自修改安全边界，默认建议先做设计，不直接实现。

## 2026-06-12 Phase 9 Provider Runtime 进度更新

### 本次目标

完成 Phase 9 的最小 Provider Runtime：让当前 agent loop 能通过 Hermes 风格的规范化 provider response 接入真实 OpenAI-compatible provider，并具备 usage 与 fallback chain 的基础可观测性。

### 已完成

- 新增 Hermes 风格 provider 类型：
  - `NormalizedResponse`
  - `ToolCall`
  - `Usage`
- `ProviderTransport.complete()` 现在返回 `NormalizedResponse`，并支持可选 `tools`。
- `FakeProviderTransport` 已迁移到 `NormalizedResponse` 返回形态。
- 新增 `OpenAICompatibleProviderTransport`：
  - 使用 stdlib `urllib.request` 发送 POST 到 `/chat/completions`。
  - 支持传入 OpenAI function tool definitions。
  - 解析 `content`、`tool_calls`、`finish_reason` 和 token usage。
  - 对 HTTP、URL、timeout 和 invalid JSON 返回可读 `RuntimeError`。
- 新增 runtime provider resolver：
  - `fake`
  - `openai-compatible`
  - `openai` alias
  - `--tool-demo` 继续强制使用 scripted fake provider。
- 扩展 `config.yaml` 的 `model` 配置：
  - `provider`
  - `default`
  - `base_url`
  - `api_key_env`
  - `timeout_seconds`
  - `fallbacks`
- CLI `doctor` 输出 provider 配置概要和 API key env var 状态，但不打印真实 API key。
- `AIAgent` 已记录 provider usage：
  - `last_usage`
  - `last_finish_reason`
  - session prompt/completion/total/cached token counters
  - `usage_snapshot()`
- CLI `chat --show-messages` 会输出 `usage_snapshot()`。
- 新增 `FallbackProviderTransport`：
  - 按顺序尝试 provider。
  - 记录 `last_provider_model`、`last_provider_index`、`last_error`。
  - `usage_snapshot()` 输出 `configured_model`、`last_model`、`last_provider_index`、`fallback_used`、`last_error`。
  - 使用 index 判断 fallback 是否发生，避免同名 model 误判。
- 保持显式主 provider 缺少 API key 时 fail fast，不静默 fallback 到 fake。

### 修改文件

- `src/learn_hermes_agent/providers/types.py`
- `src/learn_hermes_agent/providers/base.py`
- `src/learn_hermes_agent/providers/fake.py`
- `src/learn_hermes_agent/providers/openai_compatible.py`
- `src/learn_hermes_agent/providers/runtime.py`
- `src/learn_hermes_agent/providers/fallback.py`
- `src/learn_hermes_agent/agent/core.py`
- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/plans/2026-06-10-phase-9-provider-runtime-design.md`
- `docs/plans/2026-06-10-phase-9-provider-runtime-plan.md`
- `docs/plans/2026-06-11-phase-9-normalized-response-correction.md`

### 对照的 Hermes 源码

- `agent/transports/types.py`
- `agent/transports/base.py`
- `agent/transports/chat_completions.py`
- `agent/agent_init.py`
- `agent/chat_completion_helpers.py`
- `agent/agent_runtime_helpers.py`

### 验证方式

已运行并观察：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
uv run learn-hermes-agent chat "hello"
```

另用脚本验证：

- runtime 在无 fallback 时返回单 provider。
- runtime 在配置 `fallbacks` 时返回 `FallbackProviderTransport`。
- 主 provider 缺少 API key 时返回可读错误，不静默降级。
- fallback wrapper 在主 provider 失败、后备 provider 成功时记录 `last_provider_index = 1`。
- 主 provider 和 fallback provider 使用同名 model 时，`fallback_used` 仍能正确为 `true`。

### 设计结论

- 当前实现方向对齐 Hermes：agent loop 消费规范化 provider response，provider 差异收敛在 transport/runtime 层。
- 当前学习项目只实现最小 provider runtime，不照搬 Hermes 的完整 provider 状态机、client cache、cooldown、streaming 和多 API mode。
- `NormalizedResponse.provider_data` 暂不扩展为 fallback 状态承载面；fallback 可观测性先放在 `FallbackProviderTransport` 和 `AIAgent.usage_snapshot()`。
- 缺 API key 是配置错误，应 fail fast；fallback 只处理 provider 构建成功后的请求阶段失败。
- Anthropic、Gemini、Codex Responses、streaming、retry/backoff、model catalog 后延。

### 下一步

先不要开始 Phase 10 实现。进入 Phase 10 前，应先对齐 `D:\python-develop\project\hermes-agent` 中安全、审批和执行环境相关设计，再写 Phase 10 设计文档。

## 2026-06-22 Phase 10 Batch 1 Safety / Approval Primitives 进度更新

### 本次目标

完成 Phase 10 Batch 1：先建立最小安全/审批策略层和工具执行上下文，不开放新的高风险工具。

### 已完成

- 新增 `ToolExecutionContext`：
  - 携带 `session_id`、`task_id`、`cwd`、`workspace_root`、`approval_mode`、`yolo_enabled`。
  - `create_tool_execution_context()` 从当前配置创建每轮工具执行上下文。
  - `normalize_approval_mode()` 将非法审批模式回退到 `ask`。
- 扩展安全配置：
  - `security.approval_mode`
  - `security.yolo`
  - `security.workspace_root`
  - CLI `doctor` 现在输出规范化后的 security 配置。
- 新增命令审批策略：
  - `CommandRisk`
  - `ApprovalDecision`
  - `classify_command()`
  - `check_command_approval()`
  - hardline 命令永远先于 yolo/auto 被阻断。
- 新增文件路径安全策略：
  - `PathDecision`
  - `resolve_workspace_path()`
  - `check_read_path()`
  - `check_write_path()`
  - 阻断 workspace 外路径、敏感凭据文件、敏感目录、系统路径、`.git` 写入和 `.learn_hermes` 内部状态写入。
- 将 `ToolExecutionContext` 贯穿到工具分发链路：
  - `AIAgent.run_conversation(..., tool_context=...)`
  - `safe_handle_function_call(..., context=...)`
  - `handle_function_call(..., context=...)`
  - `_preflight_tool_call()` 在 handler 执行前预留 `terminal`、`read_file`、`write_file`、`patch` 的统一拦截点。

### 修改文件

- `src/learn_hermes_agent/agent/tool_context.py`
- `src/learn_hermes_agent/agent/file_safety.py`
- `src/learn_hermes_agent/tools/approval.py`
- `src/learn_hermes_agent/model_tools.py`
- `src/learn_hermes_agent/agent/core.py`
- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/cli/main.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `tools/approval.py`
- `agent/file_safety.py`
- `agent/tool_executor.py`
- `tools/terminal_tool.py`

### 验证方式

已运行并观察：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

另用手工策略命令验证：

```powershell
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.tools.approval import check_command_approval; ctx=create_tool_execution_context({}); print(check_command_approval('echo hello', ctx).to_dict()); print(check_command_approval('sudo reboot', ctx).to_dict()); print(check_command_approval('rm -rf /', ctx).to_dict())"
uv run python -c "from learn_hermes_agent.agent.tool_context import create_tool_execution_context; from learn_hermes_agent.agent.file_safety import check_read_path, check_write_path; ctx=create_tool_execution_context({}); print(check_read_path('README.md', ctx).to_dict()); print(check_write_path('.env', ctx).to_dict()); print(check_write_path('.ssh/id_rsa', ctx).to_dict()); print(check_write_path('.learn_hermes/config.yaml', ctx).to_dict())"
```

已观察到：

- `compileall` 通过。
- `doctor` 输出 `security_approval_mode: ask`、`security_yolo: False`、`security_workspace_root: .`。
- `tools` 仍列出既有 `echo`、`memory`、`skill_view`、`skills_list`。
- `call-tool echo` 返回 `{"text": "hello"}`。
- `chat --tool-demo --show-messages` 仍输出 `user -> assistant(tool_calls) -> tool -> assistant(final)`。
- 命令策略：
  - `echo hello` -> `allowed`
  - `sudo reboot` -> `approval_required`
  - `rm -rf /` -> `blocked`
- 文件策略：
  - `README.md` read -> `allowed`
  - `.env` write -> `blocked`
  - `.ssh/id_rsa` write -> `blocked`
  - `.learn_hermes/config.yaml` write -> `blocked`

说明：普通沙箱下 `uv` 访问 `C:\Users\admin\AppData\Local\uv\cache` 会被拒绝；上述 `uv` 验证已按权限规则授权后运行。

### 设计结论

- Phase 10 Batch 1 对齐 Hermes 的核心意图：安全/审批策略先集中在统一模块中，工具 handler 不自行发明安全规则。
- hardline block 必须优先于 `yolo_enabled` 和 `approval_mode=auto`，避免高风险命令被模式开关绕过。
- 文件安全是 defense-in-depth，不是完整安全边界；真正开放 terminal 前仍必须保留命令审批策略。
- 当前 Batch 1 只接入策略和上下文，不注册 `terminal`、`read_file`、`write_file`、`patch`，也不实现 checkpoint。

### 下一步

进入 Phase 10 Batch 2 前，应继续遵守当前边界：先设计并实现 file tools with safety，不开始 terminal local backend，不实现 checkpoint。

## 2026-07-21 Phase 10 Batch 2 File Tools With Safety 进度更新

### 本次目标

完成并集中验收 Phase 10 Batch 2A 至 2E 的最小文件工具闭环，同时根据最新版 Hermes 的 Registry / ToolExecutor 分层重新确定后续路线。

### 已完成

- Batch 2A `read_file`：
  - 支持 `path`、`offset`、`limit`。
  - 使用 `LINE|CONTENT` 行号展示格式。
  - 限制单次读取行数和字符数，拒绝明显二进制文件和非 UTF-8 文本。
- Batch 2B `write_file`：
  - 支持 UTF-8 完整覆盖写入和父目录创建。
  - dispatch preflight 与 handler 内部均执行写路径安全检查。
  - 返回 `bytes_written` 和 `files_modified`。
- Batch 2C 文件工具加固：
  - 识别主要由连续 `read_file` 行号展示文本组成的内容。
  - 拒绝把展示文本直接写回真实文件。
- Batch 2D `patch`：
  - 支持单文件精确字符串替换。
  - 默认要求 `old_string` 唯一；多处匹配必须显式传入 `replace_all=true`。
  - 返回替换次数、最终字节数和 `files_modified`。
- Batch 2E `search_files`：
  - 支持 UTF-8 文件内容正则搜索、搜索根路径和结果数量限制。
  - 非法正则返回结构化错误。
  - 根路径通过统一 preflight；每个递归候选文件在读取前再次执行 `check_read_path()`。
  - 敏感文件和解析到 workspace 外的候选路径会被跳过。

### 修改文件

- `src/learn_hermes_agent/tools/file_tools.py`
- `src/learn_hermes_agent/tools/registry.py`
- `src/learn_hermes_agent/model_tools.py`
- `docs/02-roadmap.md`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- `docs/plans/2026-06-22-phase-10-batch-2a-read-file-tool-plan.md`
- `docs/plans/2026-06-22-phase-10-batch-2b-write-file-design.md`
- `docs/plans/2026-06-22-phase-10-batch-2b-write-file-tool-plan.md`
- `docs/plans/2026-06-28-phase-10-batch-2c-file-tool-hardening-plan.md`
- `docs/plans/2026-07-21-phase-10-post-file-tools-route-design.md`
- `docs/plans/2026-07-21-phase-10-batch-2e-search-files-closeout-plan.md`

### 对照的 Hermes 源码

- `tools/file_tools.py`
  - `read_file_tool()` / `write_file_tool()` / `patch_tool()` / `search_tool()`。
  - `_filter_read_blocked_search_results()` 对 search 结果逐项执行读取安全过滤。
- `tools/registry.py`
  - `ToolEntry` 的 toolset、check_fn、generation 和可用工具定义过滤。
- `agent/tool_executor.py`
  - 参数解析、阻断判断、guardrails、checkpoint preflight 和执行分发。

### 验证方式

本轮集中运行并观察：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

另通过 `get_tool_definitions()`、`handle_function_call()` 和临时 `sandbox/batch2e_closeout/` 数据执行轻量不变量验证：

- registry 同时暴露 `read_file`、`write_file`、`patch`、`search_files`。
- `search_files` schema 的 `pattern` 必填，`path` 默认 `.`，`limit` 范围为 1 到 200。
- `write_file` 正常写入并返回 `files_modified`。
- `read_file` 返回 `1|red blue red`。
- `write_file` 拒绝 `1|alpha\n2|beta` 形式的展示文本。
- `patch` 唯一替换成功；多处匹配默认拒绝；`replace_all=true` 成功替换两处。
- `search_files` 在普通文件和 `.env` 含有相同标记时只返回普通文件，并记录敏感文件被跳过。
- 非法正则、`.env` 根路径、workspace 外路径和自定义 context 外路径均返回结构化阻断结果。
- `limit=1` 返回一条结果并标记 `truncated=true`。
- `chat --tool-demo --show-messages` 仍输出 `user -> assistant(tool_calls) -> tool -> assistant(final)`。

所有验证命令退出码均为 0；临时验证目录已删除。

### 设计结论

- 文件工具 handler 保留 defense-in-depth，但 per-call workspace 边界仍由 dispatch preflight 统一执行。
- `search_files` 必须对候选文件逐项检查，不能只验证搜索根目录，否则递归搜索可能读取嵌套敏感文件或 workspace 外符号链接目标。
- 当前 `patch` 和 `search_files` 是学习项目的最小子集，不追求一次复制 Hermes 的 V4A patch、ripgrep backend、pagination 和重复搜索 guard。
- 不应直接进入 terminal：最新版 Hermes 已把 Registry 元数据和 ToolExecutor 执行职责明确分层，学习项目应先补齐这两个承重层。

### 下一步

进入 Phase 10 Batch 3：ToolRegistry v2。先对齐最新版 `tools/registry.py`，只实现带兼容默认值的 `toolset`、`check_fn` 和 `generation`；不开始 ToolExecutor、checkpoint 或 terminal。

## 2026-07-21 Phase 10 Batch 3 ToolRegistry v2 进度更新

### 本次目标

以兼容方式升级工具注册元数据和模型定义生成入口，为下一批独立 ToolExecutor 建立稳定边界，不改变当前工具执行链路。

### 已完成

- `ToolEntry` 增加 `toolset: str = "other"` 和可选 `check_fn`。
- `ToolRegistry` 增加只读 `generation`：初始为 0，成功注册或覆盖后递增，失败的重复注册不递增。
- 新增 `get_definitions(tool_names=None)`：
  - 支持按工具名称集合过滤。
  - `check_fn=None` 的工具默认可用。
  - `check_fn=False` 或检查异常时不向模型暴露工具。
  - 检查异常记录 warning，不中断其他定义生成。
  - 同一次查询中，共享同一个 `check_fn` 的工具只检查一次。
- 保留 `list_definitions()` 作为兼容包装入口。
- 新增 toolset 查询：已注册分类、分类内工具名称和单工具归属。
- 内置工具分类：
  - `file`：`read_file`、`write_file`、`patch`、`search_files`
  - `memory`：`memory`
  - `skills`：`skills_list`、`skill_view`
  - `other`：`echo`
- `model_tools.get_tool_definitions()` 支持可选 `tool_names`。
- `AIAgent` 改为直接调用 `registry.get_definitions()`。

### 修改文件

- `src/learn_hermes_agent/tools/registry.py`
- `src/learn_hermes_agent/tools/file_tools.py`
- `src/learn_hermes_agent/tools/memory.py`
- `src/learn_hermes_agent/tools/skills.py`
- `src/learn_hermes_agent/model_tools.py`
- `src/learn_hermes_agent/agent/core.py`
- `docs/00-overview.md`
- `docs/02-roadmap.md`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- `docs/plans/2026-07-21-phase-10-post-file-tools-route-design.md`
- `docs/plans/2026-07-21-phase-10-batch-3-tool-registry-v2-design.md`
- `docs/plans/2026-07-21-phase-10-batch-3-tool-registry-v2-plan.md`

### 对照的 Hermes 源码

- `tools/registry.py`
  - `ToolEntry.toolset`、`check_fn` 和 registry generation。
  - 按工具名称和可用性生成模型工具定义。
  - toolset 元数据与查询意图。
- `agent/tool_executor.py`
  - 确认 `check_fn` 不是审批或执行权限边界。
  - 模型只能执行本轮暴露工具的范围限制留到下一批 executor。

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"registry-v2\"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

另用内联断言验证：

- 八个内置工具全部可生成 schema。
- `generation == len(registry.names()) == 8`。
- 可用、不可用、检查异常三类工具得到正确过滤。
- 检查异常记录 warning 后继续生成其他定义。
- 两个工具共享同一 `check_fn` 时，一次查询只调用一次。
- toolset 去重、排序、未知值和内置工具分类正确。
- file toolset 子集只返回 `patch`、`read_file`、`search_files`、`write_file`。
- `list_definitions()` 与 `get_definitions()` 的兼容结果一致。

实际结果：`compileall` 和全部 CLI 退出码为 0，内联总体不变量输出 `batch-3-ok`。Windows 外部进程传递 JSON 时需要保留反斜杠转义，否则双引号会在参数解析阶段被移除。

### 设计结论

- `toolset` 只是分类元数据，不是权限边界。
- `check_fn` 只判断工具当前环境是否可用，不负责审批、安全 preflight 或直接执行限制。
- 不可用工具仍保留在 Registry 中，并可被 `get()` 找到；Batch 4 必须负责限制模型只能调用本轮暴露的工具。
- generation 先建立 Registry 结构变更协议，本批不驱动长期缓存。
- 本批不实现 deregister、TTL/failure grace cache、dynamic schema、插件所有权、异步 dispatch、checkpoint 或 terminal。

### 下一步

进入 Phase 10 Batch 4：Minimal ToolExecutor。最新源码对齐后采用 Hermes 的模块级顺序执行函数结构：AIAgent 从实际 definitions 形成 `valid_tool_names`，executor 负责模型范围检查、参数解析、顺序执行和 tool result 追加，`model_tools` 继续保留 registry dispatch 与现有安全 preflight；不开始 checkpoint 或 terminal。

## 2026-07-21 Phase 10 Batch 4 Minimal ToolExecutor 进度更新

### 本次目标

按最新版 Hermes 的模块级执行器结构，从 `AIAgent` 提取最小顺序 tool-call 编排，并关闭模型调用本轮未暴露工具的范围缺口。

### 已完成

- 新增模块级 `_parse_tool_arguments()`：只允许 JSON object 进入实际分发；空参数规范化为 `{}`，非法 JSON 或非 object JSON 返回结构化 tool error。
- 新增模块级 `execute_tool_calls_sequential()`：
  - 按模型输出顺序处理 tool calls。
  - 使用 `AIAgent.valid_tool_names` 检查本轮模型工具范围。
  - 调用现有 `safe_handle_function_call()` 完成单工具安全分发。
  - 为每个 tool call 追加对应 `tool_call_id` 的 tool result。
- `AIAgent` 从实际发送给 Provider 的同一批 definitions 生成 `valid_tool_names`，不使用 Registry 全量名称，也不在执行时重新运行 `check_fn`。
- `AIAgent` 原有逐工具 for-loop 已替换为 `execute_tool_calls_sequential()`。
- 被 `check_fn=False` 隐藏的工具仍保留在 Registry 中，但不能被模型执行。
- `model_tools` 继续负责 Registry lookup、安全 preflight、handler dispatch、JSON 序列化和 CLI 兼容。

### 修改文件

- `src/learn_hermes_agent/agent/tool_executor.py`
- `src/learn_hermes_agent/agent/core.py`
- `docs/00-overview.md`
- `docs/02-roadmap.md`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

### 对照的 Hermes 源码

- `agent/conversation_loop.py`
  - 从实际工具 definitions 维护 `valid_tool_names`。
- `agent/tool_executor.py`
  - 模块级顺序执行函数、模型参数解析和 tool result 追加。
- `model_tools.py`
  - 保留单工具 Registry dispatch 和安全执行边界。

### 验证方式

已运行：

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"tool-executor\"}'
uv run learn-hermes-agent call-tool missing '{}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

另用内联断言验证：

- `None`、空字符串、字典、合法 JSON object 和非法参数得到预期解析结果。
- 不在 `valid_tool_names` 中的工具不会执行 handler。
- 非法参数不会执行 handler，并产生配对 tool error。
- 同批前序调用失败后，后续有效调用仍可执行。
- `check_fn=False` 的已注册工具不在 Provider definitions 中，也不能被模型调用。
- `.env` 读取仍被现有路径安全 preflight 以 `sensitive_name` 阻断。

实际结果：`compileall`、八工具 schema、`echo`、tool demo、执行器不变量和路径 preflight 均通过；未知工具 CLI 保持严格错误语义，输出 `Unknown tool: missing` 并以退出码 `1` 结束。

### 设计结论

- `valid_tool_names` 是本轮 Provider 实际可见工具的快照，不是 Registry 全量名称。
- 空的 `valid_tool_names` 表示没有工具可执行，不能解释成“不限制”。
- ToolExecutor 负责编排一批模型 tool calls；`model_tools` 和 Registry 继续负责单工具安全分发，两层职责不合并。
- 本批只实现模块级顺序执行，不实现 executor 类、并发、segmented execution、middleware、guardrails、checkpoint 或 terminal。

### 下一步

进入 Phase 10 Batch 5：Minimal Checkpoint。开始设计前先重新对齐最新版 Hermes 的 checkpoint 创建时机、保存范围和失败语义；本批仍不实现 terminal。

## 2026-07-22 Phase 10 Batch 5 Minimal Checkpoint 进度更新

### 本次目标

复刻最新版 Hermes 的透明文件系统 checkpoint 核心意图：使用共享 shadow Git store，在允许的文件写操作真正执行前保存 workspace 状态，并保持 checkpoint fail-open、安全策略 fail-closed。

### 已完成

- Task 1：新增默认关闭的 `checkpoints.enabled`、`max_snapshots`、checkpoint base 路径和 doctor 可观察输出。
- Task 2：新增 `tools/checkpoint_manager.py` 基础层：
  - 共享 bare shadow Git store。
  - 每 workspace 独立 ref 和 index。
  - Git 环境与用户全局/系统配置隔离。
  - 默认排除 `.git/`、`.learn_hermes/`、虚拟环境、缓存、构建产物、`.env*` 和日志。
- Task 3：实现 `CheckpointManager`：
  - 每 Provider/tool iteration、每 workspace 最多尝试一次快照。
  - 内容无变化时不创建重复 commit。
  - 列举 checkpoint。
  - 重写保留链并执行 GC，实现真实 `max_snapshots` 数量限制。
- Task 4：实现路径解析和 Manager 级恢复：
  - 项目根 marker 搜索受 `workspace_root` boundary 限制。
  - restore 文件路径拒绝空值、绝对路径和目录穿越。
  - commit 必须属于当前 workspace ref。
  - 恢复前创建 `pre-rollback` 快照。
- Task 5：`AIAgent` 持有 `CheckpointManager`，CLI 传入 checkpoint 配置，并在每个 Provider/tool iteration 开始时调用 `new_turn()`。
- Task 6：完成自动写前 checkpoint：
  - `model_tools` 增加可选 `before_dispatch` callback。
  - callback 仅在安全 preflight 通过后、handler 前执行。
  - 顺序 ToolExecutor 只为 `write_file` / `patch` 解析正确 workspace 并创建 checkpoint。
  - callback 或 checkpoint 异常采用 fail-open，不阻止工具 handler。
- Task 7：完成集中回归验证。
- 源码对齐基线：Hermes HEAD `477c08b44766ace8b890faa72bf82ecbcf2b3ba8`，checkpoint path fix `d7b36070e`。

### 已验证

- `compileall` 持续通过。
- 共享 store 不会在目标 workspace 创建或修改 `.git`。
- 首次快照、无变化去重、parent commit 链、列举和数量裁剪均通过临时目录不变量。
- 单文件恢复、pre-rollback 快照、非法 hash、路径穿越和跨 workspace commit 拒绝均通过。
- CLI `build_agent()` 的配置传递和两轮 tool-demo iteration 生命周期通过。
- 八个内置工具 schema、`echo`、完整 tool-demo 消息链和 `.env` fail-closed preflight 回归通过。
- 自动写入验收确认被阻止的写调用不触发 callback，允许调用在 handler 前保存旧内容，checkpoint 故障时 handler 仍执行。
- `git diff --check` 通过；工作树只显示既有无关的 `sandbox/`。

### 设计结论

- checkpoint 是 `AIAgent` 持有的透明基础设施，不是 Registry 工具，也不暴露给 LLM。
- 本学习版与最新版 Hermes 一样使用单一共享 object store；每 workspace 的 ref/index 提供逻辑隔离和对象去重。
- `new_turn()` 的实际生命周期是一次 Provider/tool iteration，不是整个用户输入。
- checkpoint 失败必须 fail-open；安全 preflight 仍然 fail-closed。
- 本批 restore 只验收 checkpoint 中已存在的文本文件；不使用 `git clean`，不定义 checkpoint 后新增文件的删除语义。
- 当前不实现 terminal、checkpoint CLI、diff、全局容量限制、自动维护、legacy migration 或并发 executor。

### 下一步

Phase 10 Batch 5 Minimal Checkpoint 已完成。下一步是 Phase 10 Batch 6 Local Foreground Terminal；开始前必须重新分析最新版 Hermes 的 terminal tool、approval ordering、local execution backend、timeout、cwd/workspace 和结果语义，并先形成独立设计与实施计划，不直接写代码。

## 2026-07-22 Phase 10 Batch 6 Local Foreground Terminal 进行中

### 本次目标

在现有 Registry v2、顺序 ToolExecutor、approval preflight 和 checkpoint 链上增加与最新版 Hermes 设计意图一致的本地 Bash 前台 terminal；当前先完成配置、Bash 可用性和有界输出基础，不提前实现 background、PTY、process 或远程 backend。

### 已完成

- 已写入并确认独立设计与实施计划：
  - `docs/plans/2026-07-22-phase-10-batch-6-local-foreground-terminal-design.md`
  - `docs/plans/2026-07-22-phase-10-batch-6-local-foreground-terminal-plan.md`
- Task 1 terminal 配置已完成：
  - 默认 `timeout_seconds=180`。
  - 默认 `max_output_chars=50000`。
  - timeout 规范化范围为 1 至 600，bool 不作为 int 接受。
  - doctor 可观察最终 terminal 配置。
- Task 2 local environment 基础已完成：
  - 新增 `tools/environments` 包和 `local.py`。
  - Windows Bash 候选顺序对齐最新版 Hermes：`HERMES_GIT_BASH_PATH`、Hermes portable Git、Git for Windows 标准目录、PATH。
  - `_bash_starts()` 使用 `/usr/bin/true` 和 `/usr/bin/cat` 验证外部 MSYS 程序，而不是只验证 Bash builtin。
  - 支持坏自定义候选回退、MSYS spawn 故障分类、Mandatory ASLR 查询和针对性诊断文本。
  - 新增线程安全的 40/60 有界输出 collector。
  - 新增常见 ANSI/CSI 清理和已知 secret 精确值替换 helper。
- Task 3 LocalEnvironment 前台进程生命周期已完成：
  - 子进程环境从父进程副本构建，移除指定 sensitive env、`VIRTUAL_ENV` 和 `CONDA_PREFIX`，不修改父进程环境。
  - 使用 Bash 参数数组、`stdin=DEVNULL`、stdout/stderr 合流和后台 UTF-8 增量读取。
  - Windows 使用新进程组和 `taskkill /T /F`，POSIX 使用新 session 和进程组信号。
  - timeout 使用 monotonic deadline，返回码固定为 124，并验证孙进程不会在超时后继续写文件。
  - 长 secret 使用 `[REDACTED]`，短 secret 使用等长 `*`，保证脱敏后结果仍受 `max_output_chars` 约束。
- Task 4 terminal tool 与 Registry 注册已完成：
  - schema 只暴露 `command`、`timeout` 和 `workdir`，不暴露 background 或 PTY 参数。
  - handler 统一返回 `output`、`exit_code`、`error`，并保留 hardline defense-in-depth。
  - 引号和转义感知的前台检查拒绝 `nohup`、`disown`、`setsid` 和独立后台 `&`，同时避免误判 shell 组合操作符。
  - `check_fn` 通过 `find_bash()` 健康探测控制模型可见性，Registry 将 terminal 归入 `terminal` toolset。
  - `model_tools` 的 approval preflight 已验证先于 handler：危险命令在 `ask` 模式返回 `approval_required`，hardline 在 `auto + yolo` 下仍为 `blocked`。
- Task 5 destructive-command helper 已完成：
  - 新增独立纯函数 `is_destructive_command()`，不执行命令，也不承担 approval 职责。
  - 识别常见文件变更命令、Git 工作区变更命令和覆盖重定向。
  - 覆盖重定向判断不会把 `>>`、`>&2` 或 `2>&1` 当成覆盖文件。

### 修改文件

- `src/learn_hermes_agent/config.py`
- `src/learn_hermes_agent/cli/main.py`
- `src/learn_hermes_agent/tools/environments/__init__.py`
- `src/learn_hermes_agent/tools/environments/local.py`
- `src/learn_hermes_agent/tools/terminal_tool.py`
- `src/learn_hermes_agent/tools/registry.py`
- `src/learn_hermes_agent/agent/tool_dispatch_helpers.py`
- `docs/04-progress-handoff.md`
- `docs/plans/2026-07-22-phase-10-batch-6-local-foreground-terminal-design.md`
- `docs/plans/2026-07-22-phase-10-batch-6-local-foreground-terminal-plan.md`

### 对照的 Hermes 源码

- `tools/environments/local.py`
  - `_find_bash()` 的候选优先级和健康候选选择。
  - `_bash_starts()` 的外部 MSYS 程序探测与进程内缓存。
  - Mandatory ASLR/MSYS spawn 故障分类和修复说明。
- `tools/terminal_tool.py`
  - local terminal 的 schema、handler、环境可用性和结果边界。
- `tools/environments/base.py`
  - 有界输出、timeout 退出码和进程清理意图。

### 验证方式

已运行并观察：

```text
task-1-ok
collector-state-ok
collector-append-ok
collector-render-ok
output-cleaning-ok
bash-probe-ok
bash-diagnostics-helpers-ok
mandatory-aslr=False
aslr-help-ok
find-bash-healthy-ok
find-bash-missing-ok
find-bash-fallback-ok
task-2-ok
execute-final-bound-ok
task-3-output-secret-truncation-ok
task-3-timeout-tree-ok
terminal-registry-schema-ok
terminal-real-execution-ok
terminal-background-block-ok
terminal-approval-preflight-ok
terminal-hardline-preflight-ok
task-5-classification-ok
```

`uv run learn-hermes-agent tools` 已确认 Bash 可用时共暴露九个工具，且 terminal schema 中不存在 background/PTY 参数。`uv run python -m compileall -q src` 退出码为 0。当前机器的 Git Bash 位于 `D:\develop\Git\bin\bash.exe`，不在 PATH；验证通过子进程临时设置 `HERMES_GIT_BASH_PATH` 完成，没有修改系统环境。后续真实 CLI 验证前需在当前 PowerShell 设置：

```powershell
$env:HERMES_GIT_BASH_PATH = 'D:\develop\Git\bin\bash.exe'
```

### 设计结论

- 学习项目按阶段复刻 Hermes，但 runtime 已承担的 Bash 候选和启动探测语义不能用项目自创的 `git.exe` 路径推导替代。
- 本批识别已有 Hermes portable Git 路径，但不复制 `install.ps1` 的下载和安装职责。
- `find_bash()` 必须按健康状态选候选，文件存在不等于 MSYS 外部程序可运行。
- Mandatory ASLR 只做查询和诊断，代码不得自动关闭系统安全策略。
- 有界输出在读取过程中保留头 40% 和尾 60%，不能先无限收集再裁剪。
- 输出脱敏不能扩大最终字符串；否则 collector 的严格上限会在后处理阶段失效。
- timeout 必须清理进程树而不是只终止 Bash 父进程，退出码统一为 124。
- terminal 的 `check_fn` 只决定工具是否暴露，不负责 approval；approval 仍由 `model_tools` 在 handler 前统一执行。
- 前台限定既要拒绝明确的后台语法，也要识别引号、转义和 `&&`、`&>`、`2>&1`、`|&`、`;&` 等非后台组合，避免明显误报。
- destructive classifier 与 approval classifier 语义独立：前者只为 best-effort checkpoint 判断潜在文件变更，误报或漏报都不能改变命令授权结果。

### 尚未完成

- destructive terminal checkpoint 接入。
- 集中回归和 Batch 6 closeout。

### 下一步

继续 Task 6：在现有 post-preflight `before_dispatch` callback 中，仅为 workspace 内的 destructive terminal 创建 best-effort checkpoint；仍不开始 background、PTY、process 或远程 backend。

## 后续进度模板

复制以下模板追加到本文件末尾：

```markdown
## YYYY-MM-DD 进度更新

### 本次目标

### 已完成

### 修改文件

### 对照的 Hermes 源码

### 验证方式

### 设计结论

### 下一步
```
