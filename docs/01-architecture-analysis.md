# Hermes Agent 架构分析

## 一句话架构

Hermes Agent 是一个以 `AIAgent` 为核心的多入口 agent runtime。CLI、Gateway、ACP、TUI、Cron、Batch Runner 等入口都围绕同一套对话循环、provider runtime、tool registry、session store、prompt builder 和插件系统组织。

## 顶层模块

本地源码中的主要模块如下：

```text
run_agent.py              AIAgent 类和核心入口转发
agent/                    agent 内部模块：loop、provider、prompt、memory、compression、runtime helper
model_tools.py            工具 schema 生成和工具分发入口
tools/                    内置工具实现和 tools/registry.py
hermes_state.py           SQLite session store，含 FTS5 搜索
hermes_cli/               CLI 子命令、配置、provider 选择、插件加载
cli.py                    经典交互 CLI 编排
gateway/                  消息平台网关和平台 adapter
tui_gateway/              Python JSON-RPC 后端，服务 TypeScript Ink TUI
acp_adapter/              ACP server，给 VS Code / Zed / JetBrains 等编辑器接入
cron/                     定时任务调度
plugins/                  插件系统和内置插件
skills/ optional-skills/  内置技能和可选技能
providers/                provider 基础类型
```

## 核心运行链路

### 1. 入口创建 agent

CLI、Gateway、ACP、TUI 都会把用户输入转成一个 agent turn，并创建或复用 `AIAgent`。

典型入口：

- CLI：`hermes_cli.main:main` -> `cli.py`
- Agent 单次入口：`run_agent:main`
- ACP：`acp_adapter.entry:main`
- Gateway：`gateway/run.py`
- TUI：TypeScript Ink 前端通过 stdio JSON-RPC 调 `tui_gateway/server.py`

### 2. `AIAgent.run_conversation()` 驱动一轮对话

源码中 `run_agent.py` 的 `AIAgent.run_conversation()` 是转发器，真实大循环在 `agent/conversation_loop.py`。

一轮对话的大致顺序：

1. 确保 session DB 行存在。
2. 绑定当前 provider/model 到辅助客户端。
3. 绑定 session context、task id、interrupt 状态。
4. 复制历史消息，追加当前 user message。
5. 恢复或构建 system prompt。
6. 进行上下文压缩预检。
7. 进入主循环：
   - 构造 API 请求消息。
   - 注入 cached system prompt、ephemeral prompt、prefill message。
   - 规范化消息，修复 role/tool_call 协议问题。
   - 调 provider transport。
   - 处理 streaming、usage、fallback、retry、truncation。
   - 如果模型返回 tool_calls，校验并执行工具，然后追加 tool result，继续下一轮。
   - 如果没有 tool_calls，得到 final response，结束。
8. 持久化 session、token usage、trajectory。
9. 触发 memory/skill 的后台 review 或插件 hook。

外层循环条件包含 `max_iterations` 和 `IterationBudget`，并支持一次预算 grace call。

### 3. 工具协议是 OpenAI message 格式

Hermes 使用 OpenAI 风格消息：

```python
{"role": "system", "content": "..."}
{"role": "user", "content": "..."}
{"role": "assistant", "content": None, "tool_calls": [...]}
{"role": "tool", "tool_call_id": "...", "content": "..."}
{"role": "assistant", "content": "final answer"}
```

重点是不只是“调用函数”。每个 assistant tool_call 都必须有对应的 role=tool 结果，否则下一次 provider 请求会破坏消息协议。

Hermes 在工具分支中做了这些事：

- 自动修复部分 hallucinated tool name。
- 校验 tool name 是否在 `valid_tool_names` 中。
- 校验 tool arguments 是否是 JSON。
- 对 invalid JSON 做最多 3 次恢复。
- 将 assistant tool_calls 消息追加到历史。
- 执行工具并为每个 tool_call 追加 tool result。
- 工具执行后根据真实 token usage 或粗略估算决定是否压缩上下文。

### 4. 工具注册与分发

承重链路：

```text
tools/registry.py
  -> tools/*.py 自注册
  -> model_tools.get_tool_definitions()
  -> model_tools.handle_function_call()
  -> AIAgent tool loop
```

`tools/registry.py` 提供单例 `registry`。工具模块在 import 时调用 `registry.register(...)` 注册：

- 工具名
- toolset
- JSON schema
- handler
- env 检查函数
- 是否 async
- 结果大小限制
- 动态 schema override

`model_tools.handle_function_call()` 是工具分发的公共入口，还负责：

- 参数按 schema coercion。
- Tool Search bridge 的解包和作用域限制。
- 插件 `pre_tool_call` / `post_tool_call` / `transform_tool_result` hook。
- ACP edit approval。
- guardrail 与错误包装。

### 5. Provider runtime

Hermes 把 provider 差异封装到 transport / adapter：

- OpenAI-compatible chat completions
- Anthropic messages
- Gemini native / cloud code
- Bedrock converse
- Codex Responses
- Copilot ACP

关键抽象不是“调用哪个 SDK”，而是把不同 API 的响应规范化成同一种 assistant message / finish_reason / usage 结构，供 agent loop 统一处理。

复刻时应先实现 OpenAI-compatible provider，再扩展 Anthropic/Gemini/Codex 等。

### 6. System prompt 分层

`agent/system_prompt.py` 将 system prompt 分成三层：

- stable：身份、行为原则、工具/技能说明、平台提示等。
- context：工作目录相关上下文文件，如 AGENTS.md、SOUL.md、项目提示。
- volatile：memory、USER profile、外部 memory provider、日期等。

Hermes 的重要不变量：

- system prompt 在一个 session 中尽量只构建一次。
- 后续 turn 复用相同字节，保障 provider prefix cache。
- context compression 后才允许失效并重建。
- ephemeral prompt 和 plugin user context 不直接改 cached system prompt，而是在 API call 时注入。

复刻时这层要早做，否则后续 memory、skills、context compression 都会返工。

### 7. Session store

`hermes_state.py` 是 SQLite 存储：

- `sessions`：session metadata、source、model、system_prompt、parent_session_id、cwd、token usage 等。
- `messages`：role、content、tool_call_id、tool_calls、timestamp、active 等。
- `messages_fts`：FTS5 全文索引。
- `messages_fts_trigram`：trigram tokenizer，用于 CJK/子串检索。
- `compression_locks`：上下文压缩并发锁。

设计要点：

- WAL mode 支持多入口并发读写。
- session source 标记 CLI、Telegram、Discord、ACP 等来源。
- compression 会创建 parent-child session 链。
- system_prompt 持久化是 gateway 新 agent 每轮恢复 prefix cache 的关键。

### 8. CLI 与 Slash Command

经典 CLI 在 `cli.py`，配置和子命令在 `hermes_cli/`。

重要设计：

- `hermes_cli/commands.py` 维护中心化 `COMMAND_REGISTRY`。
- CLI help、Gateway help、Telegram menu、Slack mapping、autocomplete 都从同一注册表派生。
- `HermesCLI.process_command()` 负责 slash command 分发。
- 技能 slash command 不是塞进 system prompt，而是注入为 user message，以保护 prompt cache。

### 9. Gateway

`gateway/run.py` 管理消息平台入口。平台 adapter 位于 `gateway/platforms/`。

Gateway 的核心难点不是发消息，而是运行态控制：

- 同一 session 正在运行时的消息排队。
- `/stop`、`/new`、`/queue`、`/approve`、`/deny` 等控制命令必须绕过普通队列。
- approval prompt 和 agent interrupt 需要同时兼容不同平台。
- gateway 通常会为每条消息构建新的 `AIAgent`，因此 session DB 和 cached system prompt 恢复很重要。

### 10. Memory 与 Skills

Hermes 的“closed learning loop”来自两类持久能力：

- Memory：保存用户偏好、稳定事实、环境信息，不保存短期进度。
- Skills：保存可复用流程、调试经验、复杂任务步骤，并允许使用中自我修正。

实现位置：

- `agent/memory_manager.py`
- `agent/memory_provider.py`
- `tools/memory_tool.py`
- `tools/skill_manager_tool.py`
- `agent/skill_*`
- `skills/` 和 `optional-skills/`

复刻时应先实现内置 memory store，再实现 skill 文件系统和 skill command，最后再接外部 memory provider。

### 11. Plugins

`hermes_cli/plugins.py` 给插件提供 `PluginContext`，允许插件注册：

- tool
- slash command
- CLI command
- context engine
- memory provider
- image/video/web/browser/TTS/STT provider
- gateway platform
- hooks

插件 hook 会出现在 agent loop 和 tool dispatch 的关键点：

- `pre_api_request`
- `pre_llm_call`
- `post_llm_call`
- `transform_llm_output`
- `pre_tool_call`
- `post_tool_call`
- `transform_tool_result`

复刻时插件系统应该在核心稳定后实现，否则容易让内核过早复杂化。

## 复刻边界

第一阶段不追求完整复刻这些外围能力：

- 所有 provider
- 所有消息平台
- 所有工具
- 完整 TUI / Desktop
- 全部插件
- 全量安全策略
- 全量测试套件

但从一开始要保留这些扩展点的接口位置，避免后期重写：

- `ProviderTransport`
- `ToolRegistry`
- `SessionStore`
- `PromptBuilder`
- `CommandRegistry`
- `PluginManager`
- `GatewayAdapter`
