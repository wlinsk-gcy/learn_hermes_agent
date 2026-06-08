# 进度与 Session 交接

## 当前日期

2026-06-08

## 当前状态

已完成：

- 读取官方文档入口：https://hermes-agent.nousresearch.com/docs
- 读取官方开发文档中的架构、agent loop、provider runtime、session storage、gateway internals。
- 使用本地代码索引分析 `D:\python-develop\project\hermes-agent`。
- 确认本地源码规模：
  - 约 2951 个文件。
  - Python 文件约 2049 个。
  - 核心模块包括 `run_agent.py`、`agent/`、`tools/`、`model_tools.py`、`hermes_state.py`、`cli.py`、`hermes_cli/`、`gateway/`、`acp_adapter/`、`tui_gateway/`、`plugins/`。
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

尚未完成：

- 尚未实现真实 provider、文件工具、安全审批和 gateway。
- 尚未实现 session resume；当前最小版仍是每次 `chat` 新建 session 并持久化消息。
- 尚未实现 memory、skills、context compression、system prompt cache invalidation。
- 尚未实现 `config set`、`config edit` 等配置写入命令。

## 已确认的关键设计结论

1. Hermes 的核心是 `AIAgent`，不是 CLI 或 Gateway。
2. `run_agent.py` 中的 `AIAgent.run_conversation()` 已经转发到 `agent/conversation_loop.py`，真实 loop 在后者。
3. 工具承重链路是：

   ```text
   tools/registry.py
     -> tools/*.py
     -> model_tools.py
     -> run_agent.py / cli.py / gateway / acp / batch
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
