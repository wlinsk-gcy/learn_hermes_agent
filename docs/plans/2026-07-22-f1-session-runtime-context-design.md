# F1 Session Runtime Context 设计

## 状态

- 日期：2026-07-22
- 路线：已确认
- 参考 Hermes HEAD：`477c08b44766ace8b890faa72bf82ecbcf2b3ba8`
- 实施顺序：F1A Session Identity / Persistent CWD -> F1B Persistent Environment Snapshot -> Provider

## 目标

让同一 session 的连续工具调用共享逻辑工作目录，并保证 terminal、file safety、file tools 和 checkpoint 使用同一个 cwd；不同 session 的 cwd 不得互相泄漏。

F1 完整范围还包括 shell 环境变量持久化。为了控制风险，将 F1 拆成两个批次：

1. F1A：session key、dispatch 上下文绑定、跨调用 cwd 和跨工具 cwd 一致性。
2. F1B：复刻 Hermes 的环境变量 snapshot，持久化 `export` 等 shell 环境变化。

## 最新 Hermes 设计证据

### 运行目录的两个层次

`agent/runtime_cwd.py` 使用 `ContextVar` 保存当前执行上下文的逻辑 cwd。其解析顺序是：

```text
session context cwd
  -> TERMINAL_CWD
  -> process cwd
```

`tools/terminal_tool.py` 另外维护按 raw session/task key 隔离的 cwd record：

- `record_session_cwd()`
- `get_session_cwd()`
- `clear_session_cwd()`
- `_resolve_command_cwd()`

显式 `workdir` 优先；否则使用 session 已记录 cwd；第一次调用才回退到入口提供的默认 cwd。

### cwd 是共享工具状态

最新版 Hermes 的 `get_session_cwd()` 不只被 terminal 使用，也被以下模块使用：

- `tools/file_tools.py`
- `tools/code_execution_tool.py`
- `tools/delegate_tool.py`
- `tools/terminal_tool.py`

因此 cwd 不是 terminal 私有配置，而是 session runtime contract。

### shell 如何报告 `cd` 后的目录

`tools/environments/base.py` 会在命令后追加：

```bash
printf '<cwd-marker>%s<cwd-marker>' "$(pwd -P)"
```

同时保存原命令退出码。环境层解析并移除 marker，再更新 environment cwd。Windows Git Bash 返回 `/c/...` 时，`tools/environments/local.py` 会转换为 `C:\...` 并确认目录存在。

### 环境变量如何跨调用保存

Hermes 不依赖子进程修改父进程环境。它在每次命令前 source session snapshot，在命令后用 `export -p` 原子更新 snapshot。该机制与 cwd marker 相互独立，所以本项目把它放入 F1B。

## 当前学习项目差距

1. `ToolExecutionContext` 已有 `session_id`、`task_id` 和 `cwd`，但没有稳定的 runtime key 规则。
2. interactive CLI 每个用户 turn 都重建 context；`task_id` 会变化，只有 `session_id` 稳定。
3. `model_tools` 只把 context 用于 preflight，没有把它绑定到 handler 执行范围。
4. file handlers 会重新创建默认 context，因此 handler 内二次安全检查可能与 dispatch preflight 使用不同 cwd。
5. terminal 每次创建新 `LocalEnvironment`，默认 cwd 总是进程 cwd。
6. 当前 shell 命令不会返回 `cd` 后的目录。
7. checkpoint 的 terminal cwd 解析仍以 `Path.cwd()` 为默认基准。

## Architecture Decision Brief

### 方案 A：扩大所有 ToolHandler 签名

把 handler 从 `handler(arguments)` 改成 `handler(arguments, context)`。

优点：依赖显式，容易阅读。

缺点：需要同时修改全部内置工具、Registry 类型和兼容调用；对 F1A 来说改动面过大，也会提前影响后续插件 handler 协议。

### 方案 B：使用普通模块全局变量保存当前 context

优点：实现最少。

缺点：并发 session 会串状态；未来 Gateway、ACP 和 concurrent ToolExecutor 无法安全复用。

### 方案 C：ContextVar 绑定 + session-keyed cwd store

`model_tools` 在 handler 执行期间用 `ContextVar` 绑定本次有效 context；cwd store 使用稳定 session key 保存跨调用状态。

优点：

- 保持现有 `handler(arguments)` 兼容。
- 对齐 Hermes 的 context-local 隔离意图。
- session 持久状态和当前调用上下文职责分离。
- 后续可供 approval、Gateway、ACP 和 Provider callback 复用。

风险：handler 若绕过统一 dispatch 直接调用，只能获得默认 context。必须保留现有安全 fallback。

### 决策

采用方案 C。

## F1A 设计

### Stable session key

学习项目使用以下优先级：

```text
session_id
  -> task_id
  -> "default"
```

interactive CLI 每个 turn 会生成新的 `task_id`，所以不能优先使用它。ACP/benchmark 等没有持久 session id 的调用仍可用 task id 隔离。

### Context binding

在 `agent/tool_context.py` 增加：

- `ToolExecutionContext.runtime_key`
- `bind_tool_execution_context()`
- `get_current_tool_execution_context()`

`model_tools.handle_function_call()` 必须在同一个有效 context 下完成：

```text
preflight
  -> before_dispatch/checkpoint
  -> handler
```

即使 handler 抛出异常，`ContextVar` 也必须通过 token reset 恢复。

### Session cwd store

新增 `agent/runtime_cwd.py`，提供：

- `record_session_cwd(session_key, cwd)`
- `get_session_cwd(session_key)`
- `clear_session_cwd(session_key)`
- `resolve_tool_execution_context(context)`

store 只保存在当前进程内，并使用 lock 保护。F1A 不把 cwd 写入 SQLite，也不承诺进程重启后恢复。

### Cwd precedence

terminal cwd 解析顺序：

```text
explicit workdir
  -> recorded session cwd
  -> ToolExecutionContext.cwd
  -> process cwd
```

显式相对 `workdir` 以当前有效 session cwd 为基准。terminal 仍可运行在 workspace 外；file safety 仍以 `workspace_root` 限制文件工具。

### Cwd marker

`LocalEnvironment.execute()` 为每次调用生成不可预测 marker，在原命令后输出 `pwd -P`，并保留原始退出码。

解析要求：

- marker 不得出现在返回给模型的 output 中。
- 没有 marker、命令 timeout、命令提前 `exit` 时不更新 cwd。
- Windows MSYS/Cygwin/WSL drive path 转换为 native path。
- 新 cwd 必须真实存在，否则保留旧记录。

### File tools and checkpoint

file handlers 优先读取 dispatch 当前绑定的 context，只有直接调用 handler 时才创建默认 context。

terminal checkpoint 使用已经解析后的有效 context cwd；不再自行回退到进程 cwd。这样 approval、checkpoint、handler 三者看到同一个目录。

### Lifecycle

- `/new` 清理旧 session 的 cwd record。
- compression continuation 把旧 session cwd 复制给新 session，再清理旧 key。
- 不实现跨进程恢复。

## F1B 边界

F1A 完成并验证后，单独设计 F1B：

- 按 session key 复用本地 environment state。
- 使用私有 snapshot 文件持久化 exported environment。
- 命令前 source，命令后 `export -p` 原子替换。
- snapshot 不进入模型输出，并限制文件权限。
- session teardown 清理 snapshot。
- Provider secret 不得进入初始 snapshot。

F1B 不加入后台进程、PTY、remote backend 或 ProcessRegistry。

## 非目标

- approval UI。
- background/process、PTY、stdin 和 read/close terminal。
- Docker、SSH、Modal 等 remote backend。
- Gateway、ACP 或 TUI。
- cwd 的 SQLite 持久化。
- 修改 Provider runtime。

## 验收标准

1. 同一 session 执行 `cd child` 后，下一次 terminal 默认运行在 `child`。
2. 不同 session 的 cwd 相互隔离。
3. 显式 `workdir` 覆盖 session cwd，并在命令完成后更新该 session 记录。
4. terminal 改变 cwd 后，file tools 的相对路径从相同 cwd 解析。
5. file tools 仍不能通过 `cd` 绕过 `workspace_root`。
6. terminal checkpoint 使用与 terminal handler 相同的 cwd。
7. marker 不泄漏到 output；原始命令退出码、timeout、ANSI 清理和 secret 过滤保持不变。
8. 不新增测试文件；使用 compileall、CLI 手工运行和轻量不变量验证。

## Provider 后续方向

F1A、F1B 完成后重新读取最新版 Hermes Provider 链路，再按以下候选顺序形成独立设计：

```text
normalized streaming contract
  -> Anthropic adapter
  -> Gemini native adapter
  -> Codex Responses adapter
  -> credential pool / provider failover hardening
```

该顺序只是候选，不在 F1 中提前实现。
