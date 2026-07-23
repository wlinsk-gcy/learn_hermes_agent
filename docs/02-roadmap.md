# 复刻路线图

## 总体策略

路线采用“可运行闭环优先，逐层逼近 1:1”。

每一阶段都要回答三个问题：

1. 这一层在 Hermes 中解决什么问题？
2. 我们复刻哪些接口和行为？
3. 如何不用大规模测试也能观察它是正确的？

当前进度截至 2026-07-23：

- Phase 0 至 Phase 9 已完成当前路线中的最小版本。
- Phase 10 Batch 1、Batch 2A 至 2E、Batch 3、Batch 4、Batch 5 和 Batch 6 已完成。
- F1A Session Identity / Persistent CWD 已完成；下一步是 F1B Persistent Environment Snapshot 的 Hermes 源码重新对齐和独立设计。

## Phase 0：项目基础与约定

状态：已完成。

目标：把当前仓库变成可用的 uv Python 项目，并建立目录规范。

实现内容：

- 初始化 `pyproject.toml`。
- 设定包名，例如 `learn_hermes_agent`。
- 建立基础目录：
  - `src/learn_hermes_agent/`
  - `src/learn_hermes_agent/agent/`
  - `src/learn_hermes_agent/tools/`
  - `src/learn_hermes_agent/cli/`
  - `src/learn_hermes_agent/state/`
  - `src/learn_hermes_agent/providers/`
- 建立 `hermes` 风格配置目录规则，但先使用项目内临时目录，避免污染真实 `~/.hermes`。

验收：

- `uv run python -m learn_hermes_agent --help` 能运行。
- 文档和代码目录一致。

## Phase 1：最小 AIAgent 和 provider 抽象

状态：已完成最小版。当前只实现 fake provider；OpenAI-compatible provider 延后到 Phase 9 的 provider runtime 扩展。

目标：跑通“用户输入 -> provider -> assistant 输出”的最小闭环。

实现内容：

- `AIAgent` 类。
- `run_conversation(user_message, conversation_history=None)`。
- OpenAI message 格式。
- `ProviderTransport` 协议。
- `FakeProviderTransport`，用于本地无 API key 验证。

核心学习点：

- Hermes 为什么把 provider 差异压到 transport 层。
- agent loop 为什么只处理规范化后的 assistant message。

验收：

- 纯 fake provider 可返回固定回答。
- CLI 可发一轮消息并显示回复。
- `run_conversation()` 返回包含 user message 和 assistant message 的消息列表。

## Phase 2：工具注册表和工具 schema

状态：已完成最小版。当前只实现 `echo`，文件读取类工具暂不实现，避免在安全边界建立前过早加入文件系统能力。

目标：复刻 Hermes 的工具自注册机制。

实现内容：

- `ToolRegistry`。
- `ToolEntry`。
- `registry.register(...)`。
- `discover_builtin_tools()`。
- `get_tool_definitions()`。
- `handle_function_call()`。
- 低风险内置工具：
  - `echo`

核心学习点：

- 工具不是硬编码 if/else，而是 registry + schema + handler。
- toolset 是能力分组，不是目录名。

验收：

- 启动时自动发现工具。
- CLI 或 debug 命令能列出 tool definitions。
- 手动调用 `handle_function_call("echo", {"text": "hi"})` 返回 JSON 字符串。

## Phase 3：工具调用循环

状态：已完成最小版。

目标：实现真正的 tool calling agent loop。

实现内容：

- provider 返回 assistant `tool_calls`。
- agent 校验 tool name。
- agent 校验 JSON arguments。
- append assistant tool_calls message。
- 执行工具。
- append role=tool result。
- 回到下一次 provider call。
- 没有 tool_calls 时结束。

核心学习点：

- tool_call 和 tool_result 必须通过 `tool_call_id` 成对出现。
- tool 执行结果是对话上下文的一部分。
- invalid tool name / invalid JSON 必须反馈给模型，而不是直接崩溃。

验收：

- fake provider 能模拟“先调用 echo，再输出最终回答”。
- messages 中能看到 user -> assistant(tool_calls) -> tool -> assistant。

## Phase 4：Session Store

目标：复刻 Hermes 的持久会话基础。

实现内容：

- SQLite `sessions` 表。
- SQLite `messages` 表。
- WAL mode。
- `create_session()`。
- `add_message()` / `replace_session_messages()`。
- `get_session_messages()`。
- 持久化 `system_prompt`。
- FTS5 可用时创建全文索引；不可用时优雅降级。

核心学习点：

- Gateway/ACP 每轮可能创建新 agent，所以 session DB 是恢复上下文的关键。
- system prompt 持久化和 prefix cache 稳定性相关。

验收：

- CLI 退出再进入，可读取历史 session。
- SQLite 中能查到 messages。
- FTS5 可用时能搜索历史内容。

## Phase 5：System Prompt Builder

目标：复刻 Hermes 的 prompt 分层。

实现内容：

- stable/context/volatile 三层。
- 读取 `AGENTS.md`、`SOUL.md` 等 context 文件。
- context 文件注入前做简单 prompt injection 扫描。
- session 内缓存 system prompt。
- context compression 后提供 invalidation 接口。

核心学习点：

- prompt cache 命中依赖字节稳定。
- memory 和 context 不能随意每轮重建。

验收：

- 同一 session 多轮 system prompt 字节不变。
- 新 session 可重新构建。
- 修改 context 文件只在新 session 或显式 reload 后生效。

## Phase 6：经典 CLI 和 Slash Commands

目标：实现可日常使用的交互 CLI。

实现内容：

- `hermes` 命令入口。
- 简单 REPL。
- `/help`、`/new`、`/model`、`/tools`、`/sessions`、`/exit`。
- 中心化 `CommandRegistry`。
- CLI 从 `config.yaml` 读取 provider/model/toolsets。

核心学习点：

- CLI 是入口层，不应拥有 agent 内核逻辑。
- slash command registry 应为 Gateway/TUI 复用。

验收：

- CLI 可连续多轮对话。
- `/new` 创建新 session。
- `/tools` 展示当前可用工具。

## Phase 7：上下文压缩与预算

目标：理解 Hermes 如何避免长会话爆上下文。

实现内容：

- `IterationBudget`。
- 粗略 token 估算。
- `ContextCompressor` 接口。
- 简单摘要压缩策略。
- parent_session_id 链。
- compression lock 的简化实现。

核心学习点：

- 压缩不是删历史，而是创建可追踪的新 session 边界。
- 工具 schema 也占上下文。

验收：

- 达到阈值后触发压缩。
- 压缩后继续对话不丢最后几轮上下文。
- session DB 中能看到 parent-child 关系。

## Phase 8：Memory 和 Skills

目标：复刻 Hermes 的学习闭环。

实现内容：

- `MEMORY.md` / `USER.md` 风格内置 memory store。
- `memory` 工具：add / replace / view。
- memory 注入 system prompt。
- `skills/` 目录。
- `SKILL.md` frontmatter。
- skill 索引和 skill command。
- `skill_manage` 的 create / patch / list。

核心学习点：

- memory 存稳定事实。
- skill 存可复用流程。
- skill 注入方式要避免破坏 prompt cache。

验收：

- agent 可写入 memory。
- 新 session 能看到 memory。
- skill 可被列出、读取和作为 user message 注入。

## Phase 9：Provider Runtime 扩展

目标：把单一 OpenAI-compatible provider 扩展为 Hermes 风格 runtime。

实现内容：

- provider 配置解析。
- runtime provider resolver。
- OpenAI-compatible streaming。
- Anthropic adapter。
- Gemini adapter。
- Codex Responses adapter 的简化版。
- usage normalization。
- fallback chain。

核心学习点：

- agent loop 不应该知道每个 provider 的原始响应结构。
- provider 差异通过 normalize_response 收敛。

验收：

- 至少两个 provider 可切换。
- streaming 和非 streaming 都能返回同一规范结构。
- fallback 能在 fake provider 失败时切换。

## Phase 10：安全、审批和执行环境

状态：Batch 1 Safety / Approval Primitives、Batch 2 File Tools With Safety、Batch 3 ToolRegistry v2、Batch 4 Minimal ToolExecutor、Batch 5 Minimal Checkpoint 与 Batch 6 Local Foreground Terminal 已完成。

目标：复刻 Hermes 工具安全边界。

已完成：

- Batch 1：`ToolExecutionContext`、命令审批策略、文件路径安全策略和 dispatch 前 preflight。
- Batch 2A：带行号和分页的最小 `read_file`。
- Batch 2B：路径受控、完整覆盖写入的 `write_file`。
- Batch 2C：阻止把 `read_file` 行号展示文本直接写回文件。
- Batch 2D：单文件精确字符串替换版 `patch`。
- Batch 2E：最小 `search_files`，支持正则、结果限制、统一 preflight 和候选文件级安全过滤。
- Batch 3：ToolRegistry v2：
  - `ToolEntry` 增加兼容默认值 `toolset="other"` 和可选 `check_fn`。
  - `ToolRegistry` 增加只读 `generation`，成功注册或覆盖后递增。
  - `get_definitions()` 支持按名称和 `check_fn` 过滤；异常采用 fail-closed。
  - 增加 toolset 查询，内置工具归入 `file`、`memory`、`skills` 和 `other`。
  - `AIAgent` 与 `model_tools.get_tool_definitions()` 已迁移到新主接口。
- Batch 4：Minimal ToolExecutor：
  - `AIAgent.valid_tool_names` 从实际发送给 Provider 的同一批工具 definitions 生成。
  - 新增模块级 `execute_tool_calls_sequential()`，负责模型工具范围检查、参数解析、顺序执行和 tool result 追加。
  - 被 `check_fn` 隐藏的已注册工具不能再被模型通过 tool call 执行。
  - `model_tools` 继续负责 Registry dispatch、安全 preflight 和 CLI 兼容。
- Batch 5：Minimal Checkpoint：
  - 新增默认关闭的 checkpoint 配置和独立存储路径。
  - 使用单一共享 shadow Git object store，并以每 workspace 独立 ref/index 隔离状态。
  - 实现快照、每 iteration 去重、列举、真实数量裁剪和 GC 后目录修复。
  - 实现受 workspace 边界限制的项目根发现、commit 归属校验和 Manager 级恢复。
  - `AIAgent` 持有 `CheckpointManager`，并在每个 Provider/tool iteration 开始时重置去重状态。
  - `model_tools` 在安全 preflight 通过后、handler 前调用可选 `before_dispatch` callback。
  - 顺序 ToolExecutor 只为 `write_file` / `patch` 创建 checkpoint；checkpoint 异常 fail-open。
  - 对齐 Hermes HEAD `477c08b44` 和 checkpoint path fix `d7b36070e`。
- Batch 6：Local Foreground Terminal：
  - 注册 `toolset="terminal"`，通过 Bash 健康探测 `check_fn` 控制模型可见性；九工具 definitions 已验证。
  - 使用本地 Bash/Git Bash 执行前台命令，支持 1 至 600 秒 timeout、进程树清理、stdout/stderr 合流和严格有界输出。
  - 清理 ANSI 控制序列，移除 Provider secret 环境变量，并保证结果脱敏不突破输出上限。
  - approval preflight 继续先于 handler 和 checkpoint；hardline 不能被 `auto` 或 `yolo` 绕过。
  - workspace 内 destructive terminal 在执行前创建 best-effort checkpoint；workspace 外命令不创建学习项目 checkpoint。
  - 对齐 Hermes HEAD `477c08b44` 的 terminal、local environment、destructive classifier 和 checkpoint ordering。
- F1A：Session Identity / Persistent CWD：
  - `ToolExecutionContext.runtime_key` 优先使用稳定 `session_id`，并通过 `ContextVar` 将 effective context 绑定到当前 dispatch。
  - 新增线程安全的进程内 session cwd record；每次 dispatch 在 preflight、callback 和 handler 前解析同一个 effective cwd。
  - `LocalEnvironment` 使用随机 marker 回报命令结束 cwd，保留原退出码，并在 timeout 时拒绝 cwd 更新。
  - terminal、四个 file handler 和 destructive terminal checkpoint 使用同一个 session cwd；terminal 可离开 workspace，但 file tools 仍受 `workspace_root` 阻断。
  - `/new` 清理旧 session cwd；compression continuation 先继承 cwd，再清理父 session key。

已确认的下一步顺序：

1. F1B Persistent Environment Snapshot：先重新对齐最新版 Hermes，再设计命令前 source、命令后原子更新 export snapshot；仍不加入 background/process 或 PTY。
2. Provider 方向：F1B 完成后重新对齐最新版 Hermes 的 streaming contract、Anthropic、Gemini、Codex Responses 和 credential/failover 链路，再形成独立设计。

F1 总体设计与已完成的 F1A 计划见：

- `docs/plans/2026-07-22-f1-session-runtime-context-design.md`
- `docs/plans/2026-07-22-f1a-session-runtime-cwd-plan.md`

暂未实现：

- checkpoint CLI、rollback UX、diff、全局容量限制、自动维护和 legacy migration。
- approval UI、background/process、PTY、跨调用 env、跨进程 cwd 恢复和远程执行 backend。
- 并发工具执行、middleware、guardrails。
- Hermes 完整 registry 动态能力和完整 search/patch 高级模式。

核心学习点：

- 工具能力越强，越需要运行态身份和审批隔离。
- Gateway/ACP/CLI 对 approval 的交互方式不同，但底层策略应统一。

验收：

- 当前文件工具均通过统一路径策略，敏感路径和 workspace 外路径被阻断。
- `read_file`、`write_file`、`patch`、`search_files` 可通过 registry/dispatch 链路调用。
- Registry 可按 toolset 和可用性生成稳定、过滤后的模型工具定义。
- Agent 只能执行当前 Provider 请求实际暴露的工具；非法模型参数会形成配对的结构化 tool error。
- terminal 只暴露 `command`、`timeout`、`workdir`，安全命令可执行，timeout 返回 124 且清理进程树。
- hardline 命令继续优先于 yolo/auto 被阻断；被阻断或待审批命令不执行 handler 或 checkpoint callback。
- 同一 session 的 terminal `cd` 会影响后续 terminal 和相对 file tool；不同 session 互不影响。
- timeout、无效 marker 或无效目录不更新 session cwd；workspace 外相对 file tool 仍被阻断。

## Phase 11：Gateway

目标：复刻消息平台网关的核心，而不是一次性接全平台。

实现内容：

- `GatewayRunner`。
- `BasePlatformAdapter`。
- 先实现 `api_server` 或 `webhook` adapter。
- session key 映射。
- 运行中消息排队。
- `/stop`、`/new`、`/status`。
- approval command bypass。

核心学习点：

- Gateway 是 agent runtime 的异步入口。
- 控制命令必须绕过普通消息队列。

验收：

- HTTP 请求可触发 agent turn。
- 同一 session 运行时新消息会排队或中断。
- `/stop` 能停止当前 turn。

## Phase 12：Plugins 和 Lazy Dependencies

目标：复刻 Hermes 可扩展能力。

实现内容：

- plugin manifest。
- plugin discovery。
- `PluginContext`。
- 注册工具、slash command、provider、hook。
- hook 点：
  - pre_api_request
  - pre_llm_call
  - post_llm_call
  - transform_llm_output
  - pre_tool_call
  - post_tool_call
  - transform_tool_result
- lazy deps 配置。

核心学习点：

- 插件扩展能力，但不能破坏核心协议。
- override 必须显式。

验收：

- 一个 demo 插件能注册工具。
- 一个 demo 插件能拦截并转换 LLM 输出。

## Phase 13：ACP、TUI、Cron、Batch

目标：复刻外围入口和研究能力。

实现内容：

- ACP server 简化实现。
- TUI gateway JSON-RPC 后端。
- Cron scheduler。
- Batch runner。
- Trajectory 保存与压缩。

核心学习点：

- Hermes 的强大来自同一内核被多个入口复用。
- Batch/trajectory 是研究和训练数据生成路径。

验收：

- ACP/TUI/Gateway/CLI 至少共享同一个 agent runtime。
- Cron 可按配置触发一个 prompt。
- batch runner 可并行执行多个 prompt 并保存结果。

## 1:1 复刻的定义

这里的 1:1 分四层：

1. 接口等价：命令、配置、工具 schema、消息结构尽量一致。
2. 行为等价：同类输入下有同类状态变化和错误恢复。
3. 架构等价：模块职责和依赖方向一致。
4. 边缘等价：处理中断、压缩、fallback、invalid JSON、prompt cache、gateway queue 等复杂场景。

前 6 个阶段追求架构和核心行为等价。后续阶段逐步覆盖边缘等价。
