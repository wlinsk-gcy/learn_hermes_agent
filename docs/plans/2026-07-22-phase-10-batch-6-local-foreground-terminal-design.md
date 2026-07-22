# Phase 10 Batch 6 Local Foreground Terminal 设计

## 目标

按最新版 Hermes 的 terminal、approval 和 local execution backend 设计意图，为学习项目增加最小的本地前台命令执行闭环。

本批只实现 Bash 前台命令、timeout、有界输出、敏感环境变量过滤、现有审批 preflight 和 destructive-command checkpoint。不会扩展到后台进程、PTY、远程 backend、跨调用 shell 状态、交互审批 UI 或并发执行。

## 对齐的 Hermes 版本

本设计对齐参考仓库：

```text
D:\python-develop\project\hermes-agent
HEAD 477c08b44766ace8b890faa72bf82ecbcf2b3ba8
```

主要源码：

- `tools/terminal_tool.py`
  - `TERMINAL_SCHEMA` 定义模型可见参数。
  - `terminal_tool()` 负责 backend 选择、guard、workdir、前后台分流和统一结果组装。
  - `check_terminal_requirements()` 作为 Registry `check_fn`。
- `tools/environments/base.py`
  - `execute()` 统一包装 cwd、timeout 和命令执行。
  - `_wait_for_process()` 在读取过程中限制模型输出，处理 timeout、interrupt 和子进程清理。
- `tools/environments/local.py`
  - `LocalEnvironment` 直接在宿主机运行 Bash。
  - Windows 使用 Git Bash，不改用 PowerShell。
  - stdout 与 stderr 合并，timeout 时清理进程组。
  - 子进程环境会移除 Hermes 持有的 Provider 凭据和虚拟环境标记。
- `tools/approval.py`
  - hardline floor 先于 yolo 和 approval mode。
  - CLI、Gateway/ask 和 ACP 通过不同 callback/queue 表面获取用户决定。
  - 无可用审批 callback 时返回 pending approval，不执行命令。
- `agent/tool_dispatch_helpers.py`
  - `_is_destructive_command()` 独立判断 terminal 命令是否可能修改文件。
- `agent/tool_executor.py`
  - destructive terminal 在真正执行前创建 best-effort checkpoint。
- `tools/registry.py`
  - terminal 注册为 `toolset="terminal"`，通过 `check_fn` 控制可见性。

最新版 Hermes 已包含多 backend、后台进程、PTY、process registry、通知、session cwd、env snapshot、插件 hook、输出脱敏和并发执行。本学习批次只提取 local foreground 承重子集，不复制这些外围能力。

## 现有项目基础

Batch 6 开始前，学习项目已经具备：

- `ToolExecutionContext`，包含 cwd、workspace root、approval mode 和 yolo 状态。
- `model_tools._preflight_tool_call()` 中预留的 terminal command approval。
- ToolRegistry v2 的 `toolset`、`check_fn` 和 generation。
- 只允许执行本轮 Provider definitions 中工具的顺序 ToolExecutor。
- ToolExecutor 经 `before_dispatch` callback 接入的 post-preflight / pre-handler checkpoint。
- 默认关闭的共享 shadow Git checkpoint manager。

因此本批不重新发明审批和 dispatch，而是在现有承重链上接入 terminal backend。

## 约束

- 不修改参考仓库。
- Python 代码由用户按 Task 手工抄写，Codex 只直接维护文档。
- 不新增测试文件；使用 `compileall`、CLI、schema、内联不变量和文件副作用观察。
- 不实现 background、PTY、process tool、通知或流式输出 UI。
- 不实现 Docker、SSH、Singularity、Modal、Daytona 等 backend。
- 不实现跨调用 cwd、export、alias、function 或 shell snapshot。
- 不实现 CLI/Gateway/ACP approval callback、session/permanent allowlist 或审批恢复。
- 不实现并发、segmented executor、middleware、guardrail 或 terminal interrupt UI。

## Decision Brief

### 方案 A：完整复制最新版 Hermes terminal 栈

一次加入 BaseEnvironment、所有 remote backend、process registry、PTY、通知、session cwd、approval callback 和插件 hook。

优点：文件和接口最接近当前 Hermes。

风险：实现范围远超一个学习 batch；大量外围模块尚不存在，必须引入占位接口或同时复刻 Gateway/ACP/TUI，难以形成可解释的小闭环。

### 方案 B：阶段式功能等价的 local foreground 子集

保留 Hermes 的模块边界和关键不变量，只实现本地 Bash 前台执行、审批顺序、checkpoint、timeout、进程树清理、有界输出和敏感环境变量过滤。

优点：与当前 Registry、ToolExecutor、approval 和 checkpoint 链直接衔接；每项行为可独立观察；后续可在稳定接口上增加状态持久化和其他 backend。

风险：本批不会拥有 Hermes 完整 terminal 体验，`ask` 模式也没有当前调用内的交互批准能力。

### 方案 C：在 handler 中直接使用 `subprocess.run(..., shell=True)`

优点：代码最短。

风险：Windows/POSIX shell 语义不一致；无法可靠清理子进程树；输出先无限进入内存；容易泄漏 Provider 凭据；不符合 Hermes backend 分层。

## 决策

采用方案 B。

这是阶段式 1:1：本批对齐 Hermes local foreground 的职责、协议和安全顺序，后续批次再增加 approval surface、持久 shell 状态、background/process 和 remote backend。

## 模块边界

新增：

```text
src/learn_hermes_agent/tools/terminal_tool.py
src/learn_hermes_agent/tools/environments/__init__.py
src/learn_hermes_agent/tools/environments/local.py
src/learn_hermes_agent/agent/tool_dispatch_helpers.py
```

职责：

- `terminal_tool.py`
  - 定义 terminal schema 和描述。
  - 校验并规范化 `command`、`timeout` 和 `workdir`。
  - 派生需要从子进程环境中移除的 Provider secret 名称。
  - 调用 `LocalEnvironment` 并返回统一 dict。
  - 注册 `toolset="terminal"` 和 `check_terminal_requirements`。
  - 在 handler 内保留 hardline defense-in-depth。
- `environments/local.py`
  - 查找 Bash。
  - 构建清理后的子进程环境。
  - 创建和等待本地进程。
  - 读取、解码并有界保留输出。
  - timeout 时终止进程树。
- `tool_dispatch_helpers.py`
  - 保存与 approval 风险不同的 destructive-command 判断。

现有文件调整：

- `config.py`：增加 terminal 默认配置和规范化。
- `tools/registry.py`：发现并注册 terminal。
- `agent/tool_executor.py`：在现有 `before_dispatch` callback 中接入 destructive terminal checkpoint。
- `cli/main.py`：doctor 显示 terminal 配置。
- `model_tools.py`：继续拥有 terminal approval preflight；本批原则上不改变其职责。

本批不增加抽象 `BaseEnvironment`。只有一个 backend 时，先保持 `LocalEnvironment` 的窄接口；第二个 backend 进入路线时，再从真实重复中提取共享 base，而不是提前复制 Hermes 当前全部抽象。

## 工具 Schema

模型可见参数只有：

```json
{
  "type": "object",
  "properties": {
    "command": {"type": "string"},
    "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
    "workdir": {"type": "string"}
  },
  "required": ["command"]
}
```

规则：

- `command` 必须是非空字符串。
- `timeout` 省略时使用配置默认值；`bool` 不能作为整数接受。
- `timeout` 合法范围为 1 至 600 秒。
- `workdir` 省略时使用当前进程 cwd；当前 CLI 创建的 `ToolExecutionContext.cwd` 与它一致。
- 相对 `workdir` 以当前进程 cwd 为基准；`expanduser()` 和 `resolve()` 后必须存在且是目录。
- `workdir` 只作为 `Popen(cwd=...)` 传递，不拼接进 shell command。
- schema 不提前暴露 `background`、`pty`、`notify_on_complete`、`watch_patterns`、`task_id`、`session_id` 或内部 `force`。

## 配置

新增：

```yaml
terminal:
  timeout_seconds: 180
  max_output_chars: 50000
```

规则：

- `timeout_seconds` 必须是 1 至 600 的整数，且不能是 bool；否则回退到 180。
- `max_output_chars` 必须是正整数，且不能是 bool；否则回退到 50000。
- 前台最大 timeout 600 与最新版 Hermes 的默认 hard cap 对齐。
- 本批不增加 terminal enabled 开关；与 Hermes local backend 一样，环境可用性由 Bash `check_fn` 决定，运行授权由 approval policy 决定。

## Shell 与可用性

命令使用参数数组启动：

```text
[bash_path, "-c", command]
```

不使用 `shell=True`。

查找顺序：

- POSIX：PATH 中的 `bash`，然后 `/usr/bin/bash`、`/bin/bash`；最后可使用当前 `SHELL` 或 `/bin/sh` 兼容降级。
- Windows：按最新版 Hermes 的顺序检查 `HERMES_GIT_BASH_PATH`、`%LOCALAPPDATA%\hermes\git` 下的 PortableGit/MinGit、Git for Windows 常见目录和 PATH 中的 `bash`。
- Windows 候选不能只检查文件存在；必须用外部 MSYS 程序执行探测选择第一个健康候选，并保留 Mandatory ASLR/MSYS spawn 故障诊断。
- 本批只识别已有 portable Git，不复制 `install.ps1` 的下载和安装职责。

`check_terminal_requirements()` 只判断 shell 是否可用，不执行审批，不创建进程，不承担权限边界。返回 False 或抛异常时，Registry 按既有 fail-closed 规则隐藏 terminal definition。

## 本地执行与进程生命周期

`LocalEnvironment.execute()` 接收：

```python
command: str
cwd: Path
timeout: int
max_output_chars: int
sensitive_env_names: set[str]
```

返回：

```python
{
    "output": str,
    "returncode": int,
}
```

进程参数：

- `stdout=subprocess.PIPE`
- `stderr=subprocess.STDOUT`
- `stdin=subprocess.DEVNULL`
- `cwd=str(cwd)`
- `env=sanitized_env`
- POSIX 使用 `start_new_session=True`。
- Windows 使用独立进程组和隐藏窗口 flag。

每次调用创建新 Bash；本批不持久化上次调用的 cwd、environment、alias 或 function。

timeout 使用 `time.monotonic()`。到期后：

- POSIX 终止整个 process group，必要时从 SIGTERM 升级到 SIGKILL。
- Windows 优先调用 `taskkill /PID <pid> /T /F` 清理进程树，失败时回退 `proc.kill()`。
- 返回退出码 124，并在保留输出尾部附加 `[Command timed out after Ns]`。

命令自然结束时返回真实 exit code；非零退出码是命令结果，不转化为 Python 异常。

## 前台限定

本批没有 process registry，不能允许常见 shell 后台逃逸。terminal handler 在启动前拒绝明显的前台违规形态：

- 单独的 `&`，但不误伤 `&&`。
- `nohup`。
- `disown`。
- `setsid`。

返回结构化错误并说明 background 尚未实现。这是窄 guard，不声称是完整 shell parser；完整后台管理只能通过后续 background/process 批次实现。

## 有界输出

不能先无限 `communicate()` 再裁剪。读取线程必须在 drain stdout/stderr 时使用有界 collector：

```text
max_output_chars = 50000
head = 40%
tail = 60%
```

collector 记录总字符数，只保留头尾窗口。超限时渲染：

```text
... [OUTPUT TRUNCATED - N chars omitted out of M total] ...
```

输出使用 UTF-8 增量解码和 `errors="replace"`，避免一个非法字节导致整个工具失败。stderr 合并进 stdout，尽量保留终端出现顺序。

返回模型前移除 ANSI 控制序列，避免格式控制字符被模型复制到文件工具调用中。

## 敏感环境变量

子进程环境从 `os.environ.copy()` 创建，绝不修改父进程环境。

至少移除：

- 当前 Provider 的 `api_key_env`。
- 所有 fallback Provider 的 `api_key_env`。
- `OPENAI_API_KEY`。
- `OPENROUTER_API_KEY`。
- `ANTHROPIC_API_KEY`。
- `VIRTUAL_ENV`。
- `CONDA_PREFIX`。

其中前两类从规范化 config 动态派生。虚拟环境标记被移除，避免 agent 自己的 uv/venv 状态污染其他项目命令。

记录被移除的非空 secret 值。terminal result 返回前，如输出包含这些值，长值替换为 `[REDACTED]`，短于该 marker 的值替换为等长 `*`。替换不得扩大字符串，否则脱敏可能让最终结果重新突破 `max_output_chars`。这只是最小防护，不等价于 Hermes 完整 `redact_terminal_output()`。

## Approval 语义

最新版 Hermes 的审批表面：

- CLI 注册 thread-local callback，同步等待 `once/session/always/deny`。
- Gateway/ask 使用 per-session queue 等待决定。
- ACP 将 callback 桥接为 permission request。
- 无 callback 的兼容路径返回 pending approval，不执行命令。

当前学习项目没有这些 UI/queue 表面，因此本批保留 Batch 1 已建立的 `model_tools` preflight：

| 风险与策略 | 行为 |
| --- | --- |
| safe | 允许执行 |
| dangerous + ask | `approval_required`，不执行 |
| dangerous + deny | `blocked`，不执行 |
| dangerous + auto/yolo | 允许执行 |
| hardline | 永远 `blocked` |

这相当于 Hermes 无可用审批 callback 时的阶段性行为。后续增加 approval surface 时，危险命令可以等待用户决定；本批不在 terminal handler 内调用 `input()`，避免把 UI 绑定到工具层。

terminal handler 内再次运行 hardline 检查，作为绕过统一 dispatch 时的 defense-in-depth。普通 dangerous 审批不重复执行，因为 handler 当前没有 session-scoped `ToolExecutionContext`。

## Approval 与 Checkpoint 顺序

approval risk 与 destructive checkpoint 是不同分类：

- `sudo apt install` 可能需要审批，但不一定修改当前 workspace。
- `echo hello > result.txt` 可能不需要审批，但会覆盖 workspace 文件。

因此固定数据流为：

```text
Provider terminal tool call
  -> ToolExecutor definition scope check
  -> JSON object parse
  -> model_tools command approval preflight
      -> blocked / approval_required: return tool result
      -> allowed
  -> before_dispatch checkpoint callback
      -> destructive classifier
      -> optional best-effort checkpoint
  -> terminal handler hardline defense
  -> LocalEnvironment.execute()
  -> role=tool result
```

被阻断或待审批的命令不创建 checkpoint，也不占用 checkpoint 去重槽。

## Destructive Terminal Checkpoint

`agent/tool_dispatch_helpers.py` 增加 Hermes 风格 `_is_destructive_command()`，至少识别：

- `rm`、`rmdir`
- `cp`、`mv`、`install`
- `sed -i`
- `truncate`
- `dd`、`shred`
- `git reset`、`git clean`、`git checkout`
- 覆盖重定向 `>`，但不把 `>>` 当作覆盖

ToolExecutor 将 `_ensure_file_checkpoint()` 扩展为统一 `_ensure_checkpoint()`：

```text
write_file / patch
  -> 保留现有路径解析和 checkpoint

terminal
  -> command 必须是 destructive
  -> command_cwd = workdir or tool_context.cwd
  -> command_cwd 必须位于 workspace_root
  -> get_working_dir_for_path(..., boundary=workspace_root)
  -> ensure_checkpoint(..., "before terminal")
```

学习项目只保护 workspace。terminal workdir 在 workspace 外时仍可按 approval policy 执行，但不会创建越界 checkpoint。

checkpoint reason 不保存原始 command，避免 token、URL 参数或密码进入 shadow Git commit metadata。checkpoint 异常继续 fail-open；安全 preflight 继续 fail-closed。

与 Hermes 一样，按 command cwd 创建快照不能捕获命令显式修改其他目录的全部副作用。这是 heuristic checkpoint，不是事务系统。

## 结果协议

terminal handler 返回 dict，由现有 `model_tools.handle_function_call()` JSON 序列化：

```python
{
    "output": str,
    "exit_code": int,
    "error": str | None,
}
```

语义：

- 正常命令：真实 exit code，`error=None`。
- 命令自身失败：保留真实非零 exit code，`error=None`。
- timeout：`exit_code=124`，保留部分输出和 timeout 标记，`error=None`。
- 参数、workdir、shell 查找或进程启动失败：`exit_code=-1`，`error` 为稳定说明。
- approval preflight 被阻断：继续使用现有 `ApprovalDecision.to_dict()` 结构，不进入 handler result 协议。

handler 必须把预期运行错误转换为上述 dict，不能把 traceback 返回给模型。未预期异常仍由 `safe_handle_function_call()` 转换成通用 JSON error。

## Local Terminal 不是 Workspace Sandbox

与 Hermes local backend 一样，本批 terminal 是宿主机 shell。`workspace_root` 只用于：

- 文件工具路径边界。
- 默认 cwd 身份。
- checkpoint 保护范围。

它不能限制 shell command 读取或修改 workspace 外路径。安全性来自 command approval、hardline floor、环境凭据过滤和宿主机自身权限，而不是 workspace sandbox。真正的隔离需要后续 Docker/remote backend。

## 错误处理

- Bash 不可用：`check_fn=False`，工具不向模型暴露。
- 非字符串或空 command：返回稳定参数错误，不启动进程。
- timeout 非法：返回稳定参数错误。
- workdir 不存在或不是目录：返回稳定参数错误。
- 明显 background 形态：返回稳定范围错误。
- secret 环境变量派生失败：至少使用静态 blocklist；不能因此把已知 Provider key 传给子进程。
- output reader 异常：清理进程并返回结构化执行错误。
- timeout 清理失败：仍返回 124，并记录 warning；不能假装进程已可靠停止。
- checkpoint 失败：记录 debug，命令继续。
- approval/hardline 失败：命令绝不启动。

## 本批不实现

- `background`、`process`、PTY 和 interactive stdin。
- session cwd、export、alias、function 或 environment snapshot。
- remote/container backend。
- streaming tool progress、activity heartbeat 或 interrupt callback。
- CLI/Gateway/ACP approval prompt、once/session/always persistence。
- smart approval、Tirith、用户自定义 deny/allowlist。
- 完整 command parser 或完整 background detection。
- 完整 terminal output secret scanner和插件 transform hook。
- terminal verification evidence、exit-code meaning、sudo password flow。
- concurrent/segmented ToolExecutor。
- checkpoint CLI、rollback UX 或 terminal 多目录事务恢复。
- 新测试文件。

## 风险与控制

- 风险：local terminal 可访问宿主机。
  - 控制：明确非沙箱；默认 ask；hardline 不可绕过；过滤 Provider secret。
- 风险：`shell=True` 造成平台差异和双重解析。
  - 控制：始终显式调用 Bash 参数数组。
- 风险：verbose command 在事后裁剪前耗尽内存。
  - 控制：读取阶段即使用 bounded head/tail collector。
- 风险：timeout 只杀 shell，不杀子进程。
  - 控制：独立进程组和 process-tree kill；用延迟文件不变量验证。
- 风险：命令通过 `&` 留下未管理后台进程。
  - 控制：拒绝常见后台形态；完整后台执行延后到 process registry。
- 风险：模型通过 `env` 获取 Provider key。
  - 控制：动态和静态环境变量 blocklist，并对已知 secret 值做结果替换。
- 风险：blocked command 仍产生 checkpoint。
  - 控制：复用 post-preflight `before_dispatch` callback。
- 风险：destructive regex 有误报和漏报。
  - 控制：它只决定 best-effort checkpoint，不决定命令授权；approval classifier 独立存在。
- 风险：直接调用 Registry handler 绕过 approval。
  - 控制：公共入口继续是 model_tools；handler 内重复 hardline floor。

## 回滚路径

- 从 `discover_builtin_tools()` 移除 terminal 注册后，模型立即失去命令执行能力。
- terminal 配置键保留也不会影响旧工具。
- 删除 ToolExecutor 的 terminal checkpoint 分支可恢复 Batch 5 行为。
- 新 environment 模块只有 terminal 使用，可以独立移除。
- 现有 approval、file tools、checkpoint manager 和 handler 协议不需要回滚。

## 验收标准

- `compileall` 通过，不新增测试文件。
- `doctor` 输出规范化 terminal 配置。
- 环境具备 Bash 时，definitions 从八个增加到九个并包含 terminal。
- terminal schema 只有 `command`、`timeout` 和 `workdir`。
- Bash 不可用时 terminal 被 `check_fn` 隐藏，其他工具不受影响。
- safe command 可执行，stdout/stderr 合并，真实 exit code 可观察。
- timeout 返回 124，且延迟子进程不能在超时后继续写文件。
- 超长输出在读取过程中有界，并保留 40% 头部和 60% 尾部。
- Provider API key 不出现在 child environment 或 tool result 中。
- ask/deny/auto/yolo 与 hardline 行为符合既有 `ApprovalDecision` 协议。
- blocked 和 approval-required command 不产生副作用或 checkpoint。
- destructive command 在 handler 前为 workspace 创建一次 checkpoint。
- checkpoint 失败不阻断已批准 terminal command。
- workspace 外 workdir 不创建越界 checkpoint。
- 常见 background 形态被拒绝。
- 原有八个工具、tool demo、doctor、CLI call-tool 和文件安全 preflight 保持可用。
