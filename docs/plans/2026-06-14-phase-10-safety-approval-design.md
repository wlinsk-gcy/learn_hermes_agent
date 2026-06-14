# Phase 10 Safety / Approval / Execution Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit code files.

**Goal:** 对齐 Hermes Agent 的安全、审批和执行环境设计，先建立学习项目自己的最小安全策略层，再逐步接入文件工具、terminal 工具和 checkpoint。Phase 10 的重点不是先让模型能执行命令，而是先保证“哪些工具可以执行、哪些操作必须审批、哪些路径必须拒绝、哪些修改需要可回滚”有统一入口。

**Architecture:** 采用“统一策略层 + 工具执行前拦截 + 分批接入执行器”的设计。`AIAgent` 负责创建本轮 tool execution context；`model_tools` 在 dispatch 前执行安全/审批/checkpoint preflight；具体工具 handler 只做领域操作。文件安全放在 `agent/file_safety.py`，命令审批放在 `tools/approval.py`，terminal/file tools 后续按批次接入。

**Tech Stack:** Python 3.11+、当前 `ToolRegistry`、当前 `AIAgent`、当前 `SessionStore`、当前 argparse CLI。默认不新增测试文件；验证优先使用 CLI 手工观察、schema/接口一致性检查、轻量不变量和 `compileall`。

---

## 目标和约束

目标：

- 复刻 Hermes 的安全边界设计，而不是直接复制完整实现。
- 让高风险能力在进入工具 handler 前先经过统一策略判断。
- 为后续 `read_file` / `write_file` / `patch` / `terminal` / `checkpoint` 留出稳定接口。
- 保持当前学习项目仍然可运行，已有 `echo`、`memory`、`skills`、provider runtime 不被破坏。

约束：

- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不做无边界大重构。
- 不默认新增测试文件。
- 不在本设计阶段开始实现 terminal 执行。
- 代码实现阶段默认仍由 Codex 输出代码片段，除非用户明确要求写入代码文件。

## Hermes 对齐结论

本轮已对齐的参考模块：

- `tools/approval.py`
  - 统一处理危险命令检测、审批模式、per-session approval、yolo/session-yolo、hardline block。
  - 关键设计：不可绕过的 hardline block 必须先于 yolo；普通危险命令可以进入审批流。
- `agent/file_safety.py`
  - 统一维护读写敏感路径规则。
  - 关键设计：文件安全是 defense-in-depth，不是完整安全边界，因为 terminal 能绕过文件工具。
- `tools/file_tools.py`
  - `read_file` 有设备文件阻断、二进制阻断、敏感读阻断、重复读去重、超大内容限制。
  - `write_file` / `patch` 有敏感写路径拒绝、跨 profile 软保护、外部修改检测、最终返回 `files_modified`。
- `tools/terminal_tool.py`
  - terminal 执行前调用统一 guard；被拒绝时返回结构化 JSON，而不是直接抛异常。
  - local/docker/ssh/modal 等执行环境差异被 terminal backend 隔离。
- `agent/tool_executor.py`
  - 先判断工具是否会被执行，再做 checkpoint preflight。
  - file-mutating tools 和 destructive terminal commands 执行前创建 checkpoint。
- `tools/checkpoint_manager.py`
  - checkpoint 是 agent 拥有的透明基础设施，不是暴露给 LLM 的普通工具。

对学习项目的结论：

- Phase 10 不能从 terminal executor 开始，否则会把审批、路径、checkpoint、命令执行全部耦合到一起。
- 先补策略和上下文对象，后续工具只调用这些策略。
- 学习项目只需要最小可观察版本，不需要照搬 Hermes 的 smart approval、Tirith、ACP/Gateway async permission、Docker/SSH backend、完整 git object checkpoint store。

## Decision Brief

### Option A: 直接实现 terminal 工具

内容：新增 `terminal` tool，使用本机 shell 执行命令，并在 handler 内部做危险命令判断。

优点：

- 最快看到“模型调用终端”的效果。
- 表面上最接近 Phase 10 的执行环境目标。

缺点：

- 审批逻辑会被塞进 terminal handler，后续 `write_file`、`patch`、Gateway/CLI approval 都难复用。
- 没有 checkpoint 时先开放 shell，风险和学习成本都偏高。
- 容易误以为 Phase 10 的核心是 subprocess，而不是安全边界。

结论：不推荐。

### Option B: 先实现文件工具

内容：先新增 `read_file`、`write_file`、`patch`，同时做路径安全和写文件审批。

优点：

- 文件工具比 terminal 更可控。
- 可以先观察路径解析、敏感路径拒绝和 `files_modified` 返回结构。

缺点：

- 写文件审批和 checkpoint 仍然需要统一上下文。
- 如果先在文件工具内部写审批逻辑，后续 terminal 仍要重复一套。

结论：可以作为第二批，但不适合作为第一批。

### Option C: 先实现安全/审批策略层

内容：先建立 `ToolExecutionContext`、`ApprovalPolicy`、命令风险判断、路径读写策略、工具执行前 preflight 返回结构。第一批不执行真实 terminal，也不新增写文件工具。

优点：

- 对齐 Hermes 的核心架构：策略集中，工具执行器调用策略。
- 风险最低，便于逐步观察。
- 后续 file tools、terminal、checkpoint 都能复用同一入口。

缺点：

- 第一批看不到新的强执行能力。
- 需要先写一些看似“不会做事”的纯策略代码。

**Recommendation:** 选择 Option C。

原因：当前项目已经有 tool registry、agent loop、session store、provider runtime。Phase 10 的下一个承重点应该是工具执行前的安全上下文，而不是新增一个高风险执行器。

## 分批范围

### Batch 1: Safety / Approval Primitives

目标：建立最小策略层，不开放新高风险工具。

建议模块：

- `src/learn_hermes_agent/agent/tool_context.py`
  - `ToolExecutionContext`
  - 字段建议：`session_id`、`task_id`、`cwd`、`approval_mode`、`yolo_enabled`
- `src/learn_hermes_agent/tools/approval.py`
  - `ApprovalDecision`
  - `ApprovalPolicy`
  - `CommandRisk`
  - `check_command_approval(command, context)`
  - hardline block 规则
  - dangerous command 规则
- `src/learn_hermes_agent/agent/file_safety.py`
  - `resolve_workspace_path(path, root)`
  - `check_read_path(path, context)`
  - `check_write_path(path, context)`
  - denylist：`.env`、SSH key、config、state db、`.git` 内部控制文件、系统敏感路径
- `src/learn_hermes_agent/model_tools.py`
  - 后续扩展 `handle_function_call(..., context=None)`
  - dispatch 前先跑 preflight

不实现：

- `terminal` tool
- `write_file` tool
- checkpoint
- CLI 交互审批 prompt
- yolo slash command
- Gateway/ACP permission

验收方式：

- 当前 `tools` / `call-tool` / `chat --tool-demo` 行为不变。
- 危险命令策略函数能把 `rm -rf /`、`sudo ...`、`Remove-Item -Recurse C:\` 判为 blocked 或 approval required。
- 普通命令如 `echo hello` 判为 allowed。
- 写入 `.learn_hermes/config.yaml`、`.env`、`.ssh/id_rsa` 判为 blocked。
- 不同 `session_id` 的 approval context 不共享状态。

### Batch 2: File Tools With Safety

目标：接入低风险读文件和受控写文件。

建议新增工具：

- `read_file`
  - 支持 `path`、`offset`、`limit`
  - 默认限制单次读取字符数
  - 阻断敏感读路径和明显二进制文件
- `write_file`
  - 支持 `path`、`content`
  - 写前调用 `check_write_path`
  - 返回 `resolved_path` 和 `files_modified`
- `patch`
  - 先只支持简单 replace 模式
  - 不先实现复杂 V4A patch parser

Hermes 对齐点：

- `read_file` 的安全检查先于真实读取。
- `write_file` / `patch` 返回 `files_modified`，给 checkpoint 和 UI 观察使用。
- 文件工具内部仍做 defense-in-depth，但审批入口应在 dispatch 前。

暂不实现：

- fuzzy patch
- 外部修改检测
- 多文件 patch
- LSP diagnostics
- read dedup tracker

### Batch 3: Terminal Local Backend

目标：最小 local terminal tool。

建议工具：

- `terminal`
  - 参数：`command`、`workdir`、`timeout_seconds`、`force`
  - 默认 local backend
  - 执行前调用 `check_command_approval`
  - 返回 `output`、`exit_code`、`status`

审批原则：

- hardline block 永远不可被 `force` 或 yolo 绕过。
- `force=True` 只表示用户已确认本次命令，不表示永久允许。
- `approval_mode=deny` 时危险命令直接拒绝。
- `approval_mode=ask` 在学习项目 CLI 中先返回 `approval_required`，后续再做交互 prompt。
- `approval_mode=auto` 只允许非 hardline 的危险命令通过，并在结果里标记 `approved_by_policy`。

暂不实现：

- sudo 密码缓存
- background process
- PTY
- Docker/SSH/Modal/Daytona backend
- streaming terminal output
- persistent shell session

### Batch 4: Minimal Checkpoint

目标：文件修改和破坏性 terminal 命令前有最小可回滚点。

推荐先做轻量版本：

- `CheckpointManager`
  - agent 拥有，不作为 tool 暴露。
  - 每个 agent turn 内最多创建一个 checkpoint。
  - 对当前 workspace 做简单 snapshot 元数据记录。
  - 第一版可以只记录 affected files 的 before content，不需要共享 git object store。

触发点：

- `write_file`
- `patch`
- dangerous/destructive `terminal`

暂不实现：

- Hermes 的共享 git object store
- 跨 workspace checkpoint index
- `/rollback` Gateway 命令
- 大文件/二进制完整优化

## 建议配置

后续可在 `DEFAULT_CONFIG` 增加：

```yaml
security:
  approval_mode: ask
  yolo: false
  workspace_root: "."
  terminal:
    backend: local
    timeout_seconds: 30
  checkpoint:
    enabled: true
    max_snapshots: 20
```

说明：

- `approval_mode=ask` 是最贴近 Hermes 默认交互语义的学习配置。
- `yolo=false` 是默认安全值。
- `workspace_root` 默认当前项目目录，避免文件工具无边界读写。
- `terminal.backend` 先固定 local，Docker/SSH 后延。

## 工具执行链路

目标链路：

```text
AIAgent.run_conversation()
  -> build ToolExecutionContext(session_id, task_id, cwd, approval policy)
  -> safe_handle_function_call(..., context=context)
  -> handle_function_call(..., context=context)
  -> preflight safety / approval / checkpoint
  -> ToolRegistry dispatch
  -> tool handler
  -> JSON tool result
```

关键原则：

- preflight 必须在 handler 执行前完成。
- 被 block 的工具不应触发 checkpoint。
- checkpoint 只在确认工具会执行后触发。
- 工具返回 JSON 字符串，继续保持当前 conversation loop 协议。

## 风险和失败模式

- 风险：把审批逻辑写进每个工具。
  - 规避：`tools/approval.py` 和 `agent/file_safety.py` 作为统一策略入口。
- 风险：`force=True` 变成万能绕过。
  - 规避：hardline block 永远不可绕过。
- 风险：路径解析依赖进程 cwd，导致写到错误目录。
  - 规避：context 明确携带 `cwd` / `workspace_root`，所有相对路径都通过同一函数解析。
- 风险：CLI、Gateway、ACP 后续各写一套审批。
  - 规避：approval policy 返回结构化 decision，UI 层只负责展示和收集用户选择。
- 风险：checkpoint 在工具被拒绝时也创建。
  - 规避：先执行 block evaluation，再 checkpoint preflight。

## 回滚路径

- Batch 1 只新增纯策略模块，若设计不合适，可以不接入 dispatch，现有行为不受影响。
- Batch 2 之后如果文件工具风险过高，可以从 registry 中暂时移除 `write_file` / `patch`，保留 `read_file`。
- Batch 3 之后如果 terminal 风险过高，可以通过 config 禁用 `terminal` tool。
- checkpoint 是透明基础设施，失败时应 fail open，不应阻断工具执行；但安全审批失败必须 fail closed。

## Acceptance Criteria

Phase 10 设计完成的验收：

- 本文档明确 Hermes 对齐模块和学习项目简化范围。
- 本文档明确不从 terminal executor 开始的原因。
- 本文档明确 Batch 1 到 Batch 4 的边界。
- 后续实现计划能按 Batch 1 先实现纯策略层，而不是直接开放 shell。

Batch 1 实现完成后的验收：

- `uv run python -m compileall -q src` 通过。
- `uv run learn-hermes-agent tools` 仍能列出现有工具。
- `uv run learn-hermes-agent call-tool echo '{"text":"hello"}'` 仍能运行。
- 手工调用命令审批策略时，普通命令 allowed，危险命令 approval required，hardline 命令 blocked。
- 手工调用文件路径策略时，workspace 内普通路径 allowed，敏感路径 blocked。
