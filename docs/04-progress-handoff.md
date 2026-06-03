# 进度与 Session 交接

## 当前日期

2026-06-04

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

尚未完成：

- Phase 3：尚未把 `assistant.tool_calls` 接入 `AIAgent.run_conversation()`。
- 尚未实现 `role=tool` 消息构造。
- 尚未实现 tool call id 配对。
- 尚未实现 session store、system prompt builder、真实 provider、文件工具、安全审批和 gateway。

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

从 Phase 3 开始，不要跳到 session store 或真实 provider。

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
   - `src/learn_hermes_agent/cli/main.py`
5. 先 review Phase 2 当前代码是否仍可运行。
6. 开始 Phase 3：让 fake provider 先返回 scripted `tool_calls`，agent 执行工具后再拿 final response。

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
