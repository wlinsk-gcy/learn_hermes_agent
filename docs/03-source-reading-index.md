# 源码阅读索引

## 阅读原则

不要按文件大小从头读。Hermes 的大文件很多，直接读会迷失。推荐按承重链路读：

```text
入口 -> AIAgent -> conversation loop -> provider -> tools -> session -> prompt -> CLI/Gateway -> memory/skills -> plugins
```

## 第一组：全局定位

1. `README.md`
   - 读产品定位：self-improving agent、memory、skills、gateway、cron、delegation、terminal backends。
2. `pyproject.toml`
   - 读入口：
     - `hermes = "hermes_cli.main:main"`
     - `hermes-agent = "run_agent:main"`
     - `hermes-acp = "acp_adapter.entry:main"`
   - 读依赖策略：核心依赖收窄，provider 和高级能力多放在 extras/lazy deps。
3. `AGENTS.md`
   - 读 Hermes 自身开发指南，尤其项目结构、file dependency chain、pitfalls。

## 第二组：核心 Agent Loop

1. `run_agent.py`
   - `AIAgent`
   - `chat()`
   - `run_conversation()` 转发器
   - `_build_assistant_message()`
   - `_execute_tool_calls()`
   - provider/client 初始化
2. `agent/conversation_loop.py`
   - 真正的 `run_conversation(agent, ...)`
   - system prompt 恢复/构建
   - API request 构造
   - retry/fallback
   - tool_calls 分支
   - final response 分支
   - session 持久化和 background review
3. `agent/chat_completion_helpers.py`
   - assistant message 构建
   - max iterations 处理
   - response 规范化辅助
4. `agent/iteration_budget.py`
   - iteration budget 机制。

## 第三组：工具系统

1. `tools/registry.py`
   - `ToolRegistry`
   - `ToolEntry`
   - `discover_builtin_tools()`
   - `registry.register(...)`
2. `model_tools.py`
   - `get_tool_definitions()`
   - `handle_function_call()`
   - toolset 过滤
   - Tool Search bridge
3. `agent/tool_executor.py`
   - `execute_tool_calls_sequential()`
   - `execute_tool_calls_concurrent()`
   - 工具结果如何 append 到 messages。
4. 代表性工具：
   - `tools/file_tools.py`
   - `tools/file_operations.py`
   - `tools/terminal_tool.py`
   - `tools/memory_tool.py`
   - `tools/skill_manager_tool.py`
   - `tools/session_search_tool.py`
   - `tools/tool_search.py`

## 第四组：Provider Runtime

1. `agent/transports/base.py`
   - provider transport 基础协议。
2. `agent/transports/chat_completions.py`
   - OpenAI-compatible chat completions。
3. `agent/transports/anthropic.py`
   - Anthropic messages。
4. `agent/transports/codex.py`
   - Codex Responses。
5. `agent/transports/types.py`
   - 规范化响应类型。
6. `agent/model_metadata.py`
   - context length、模型能力和 metadata。
7. `hermes_cli/runtime_provider.py`
   - provider/runtime 解析。
8. `hermes_cli/models.py`
   - model/provider 选择。

## 第五组：Prompt 和上下文

1. `agent/system_prompt.py`
   - stable/context/volatile 分层。
   - prompt cache 不变量。
2. `agent/prompt_builder.py`
   - context files。
   - skills index。
   - prompt injection scan。
3. `agent/context_references.py`
   - `@file` / `@diff` / `@folder` 风格上下文引用。
4. `agent/context_compressor.py`
   - 上下文压缩策略。
5. `agent/conversation_compression.py`
   - session split 和压缩流程。

## 第六组：Session Store

1. `hermes_state.py`
   - `SessionDB`
   - SQLite schema
   - WAL
   - FTS5 / trigram FTS
   - compression locks
   - token usage
   - session listing/search
2. `tools/session_search_tool.py`
   - agent 如何检索过去 session。
3. `agent/insights.py`
   - usage/insights 数据如何消费。

## 第七组：CLI

1. `hermes_cli/main.py`
   - 顶层命令入口。
   - profile override 必须早于其他 import。
2. `cli.py`
   - `HermesCLI`
   - `_init_agent()`
   - `chat()`
   - `process_command()`
3. `hermes_cli/commands.py`
   - 中心化 slash command registry。
4. `hermes_cli/config.py`
   - `DEFAULT_CONFIG`
   - `load_config()`
   - config migration/merge/env expansion。
5. `hermes_cli/tools_config.py`
   - 工具配置 UI。

## 第八组：Gateway

1. `gateway/run.py`
   - `GatewayRunner`
   - message processing
   - active session queue
   - approval command bypass
   - background process notifications
2. `gateway/session.py`
   - gateway session 管理。
3. `gateway/session_context.py`
   - ContextVar session 绑定。
4. `gateway/platforms/base.py`
   - 平台 adapter 基类。
5. 先读一个平台：
   - `gateway/platforms/api_server.py`
   - 再读 `gateway/platforms/telegram.py`

## 第九组：Memory 和 Skills

1. `agent/memory_manager.py`
2. `agent/memory_provider.py`
3. `tools/memory_tool.py`
4. `agent/skill_utils.py`
5. `agent/skill_commands.py`
6. `agent/skill_preprocessing.py`
7. `tools/skill_manager_tool.py`
8. `skills/`
9. `optional-skills/`

## 第十组：Plugins

1. `hermes_cli/plugins.py`
   - `PluginManager`
   - `PluginContext`
   - manifest discovery
   - hook invocation
2. `plugins/model-providers/`
3. `plugins/memory/`
4. `plugins/context_engine/`
5. `plugins/platforms/`
6. `plugins/observability/`

## 第十一组：外围入口

1. `acp_adapter/`
   - `server.py`
   - `session.py`
   - `tools.py`
   - `edit_approval.py`
2. `tui_gateway/`
   - `server.py`
   - `transport.py`
   - `ws.py`
3. `ui-tui/`
   - TypeScript Ink UI。
4. `cron/`
   - `scheduler.py`
   - `jobs.py`
5. `batch_runner.py`
6. `trajectory_compressor.py`

## 源码阅读 checkpoints

每读完一组，需要在 `docs/04-progress-handoff.md` 更新：

- 已读文件。
- 关键设计结论。
- 对复刻阶段的影响。
- 下一个要实现或阅读的模块。
