# Phase 10 Batch 5 Minimal Checkpoint 设计

## 目标

按最新版 Hermes 的 checkpoint 设计意图，为 `write_file` 和 `patch` 建立透明、可恢复、默认关闭的文件系统快照能力，并把创建时机接入 Batch 4 已形成的 ToolExecutor 执行边界。

本批只实现 Manager API、自动快照和手工恢复验证，不实现 `/rollback`、`checkpoints` CLI、terminal checkpoint 或聊天记录回滚。

## 对齐的 Hermes 版本

本设计对齐参考仓库：

```text
D:\python-develop\project\hermes-agent
HEAD 477c08b44
```

主要源码：

- `tools/checkpoint_manager.py`
  - `CheckpointManager` 由 agent 持有，不作为模型工具注册。
  - 使用独立 shadow Git store，不触碰用户项目的 `.git`。
  - `new_turn()` 重置按工作目录去重的状态。
  - `ensure_checkpoint()` 默认 fail-open，返回 bool，不向上抛出快照异常。
  - 最新版使用单一共享 object store、每 workspace 独立 ref 和 index。
- `agent/conversation_loop.py`
  - 每次 Provider/tool iteration 开始时调用 `agent._checkpoint_mgr.new_turn()`。
- `agent/tool_executor.py`
  - `write_file` / `patch` 真正执行前调用 checkpoint manager。
  - blocked tool 不应占用 checkpoint 去重槽。
  - 文件路径必须通过文件工具相同的 task/session cwd 解析链后再确定工作目录。
- `agent/agent_init.py`
  - agent 创建并持有 manager。
- `hermes_cli/config.py`
  - 顶层 `checkpoints` 配置默认 `enabled: false`。

最新版 Hermes 的 `d7b36070e` 修复专门强调：checkpoint 不能按 Hermes 进程 cwd 猜测文件位置，必须与文件工具使用同一个 task cwd。学习项目已经通过 `ToolExecutionContext.cwd` 和 `workspace_root` 表达这层边界，本批直接复用。

## Decision Brief

### 方案 A：只保存目标文件的 before-content

优点：实现最短，不依赖 Git。

风险：只能覆盖已知文件路径，未来 destructive terminal 可能修改或删除未知文件；Batch 6 前必须重做存储模型。

### 方案 B：每个 workspace 一个 shadow Git 仓库

优点：完整 workspace 快照，实现比共享 store 简单。

风险：这是 Hermes v1 的存储结构；多个 workspace 或 worktree 会重复保存相同 objects，后续迁移共享 store 会改变持久化布局。

### 方案 C：共享 shadow Git store 的最小子集

所有 workspace 共用 Git object database，每个 workspace 使用独立 ref 和 index。只实现本批需要的快照、列举、恢复和每 workspace 数量限制。

优点：与最新版 Hermes 的核心存储结构一致；对象天然去重；Batch 6 接入 terminal 时不需要更换 checkpoint 基础设施。

代价：Git 子进程、ref、index 和恢复校验使实现量高于文件副本方案。

## 决策

采用方案 C。

不复制 Hermes 当前 checkpoint manager 的全部维护能力，只复刻当前承重接口和存储不变量。

## 存储结构

默认存储在：

```text
.learn_hermes/checkpoints/
  store/
    HEAD
    config
    objects/
    refs/hermes/<workspace_hash>
    indexes/<workspace_hash>
    info/exclude
```

其中：

- `workspace_hash` 来自规范化 workspace 路径的 SHA-256 前 16 位。
- 所有 workspace 共用 `objects/`。
- 每个 workspace 使用自己的 ref 和 index，避免暂存状态互相污染。
- `info/exclude` 至少排除 `.git/`、`.learn_hermes/`、虚拟环境、缓存、构建输出和 `.env*`。
- Git 命令通过 `GIT_DIR`、`GIT_WORK_TREE`、`GIT_INDEX_FILE` 指向 shadow store。
- Git 子进程不继承用户 global/system Git config，避免 `commit.gpgsign`、hooks 或 credential helper 干扰后台快照。

本批不写入或修改 workspace 自己的 `.git`、index、branch 或 commit history。

## 配置

新增 Hermes 风格顶层配置：

```yaml
checkpoints:
  enabled: false
  max_snapshots: 20
```

规则：

- 默认关闭，避免用户未明确选择时产生磁盘写入和 Git 子进程开销。
- 非 bool `enabled` 回退为 `false`。
- `max_snapshots` 必须是正整数，否则回退为 `20`。
- `doctor` 输出规范化后的两个值。
- 本批不增加 `--checkpoints` 参数；启用方式只通过 `config.yaml`。

## Manager 接口

新增：

```text
src/learn_hermes_agent/tools/checkpoint_manager.py
```

最小公开接口：

```python
class CheckpointManager:
    def new_turn(self) -> None: ...

    def ensure_checkpoint(
        self,
        working_dir: str,
        reason: str = "auto",
    ) -> bool: ...

    def list_checkpoints(
        self,
        working_dir: str,
    ) -> list[dict[str, object]]: ...

    def restore(
        self,
        working_dir: str,
        commit_hash: str,
        file_path: str | None = None,
    ) -> dict[str, object]: ...

    def get_working_dir_for_path(
        self,
        file_path: str,
        *,
        boundary: str | None = None,
    ) -> str: ...
```

构造参数保留：

- `enabled=False`
- `max_snapshots=20`
- 可选 `checkpoint_base`，只用于明确存储位置和轻量临时目录验证。

`restore()` 先验证 hash 形状，再确认目标 commit 属于当前 workspace ref。恢复前创建 `pre-rollback` 快照，以便撤销恢复操作。`file_path` 必须是 workspace 内相对路径。

## Turn 与去重语义

Hermes 的注释常使用 conversation turn，但当前实际调用位置在 agent loop 的每次 Provider iteration 开头。学习项目按实际行为对齐：

```text
Provider response with tool calls
  -> one executor batch
  -> at most one checkpoint per working directory
  -> tool results
  -> next Provider iteration
  -> reset dedup
```

因此：

- 同一 assistant response 中连续 `write_file`、`patch` 同一 workspace，只创建一个 pre-batch checkpoint。
- 下一次 Provider response 再修改文件，可以创建新 checkpoint。
- 不使用 session 全生命周期去重，否则长对话只能产生一个快照。

## 路径语义

新增 `_ensure_file_checkpoint()`，数据流为：

```text
function_args["path"]
  -> resolve_workspace_path(path, ToolExecutionContext)
  -> get_working_dir_for_path(resolved_path, boundary=workspace_root)
  -> ensure_checkpoint(working_dir, reason)
```

`boundary` 防止项目标记搜索越过 `workspace_root`。找不到项目标记时回退到 `workspace_root`，不回退到进程 cwd。

## 执行顺序

当前学习项目的安全 preflight 位于 `model_tools.handle_function_call()`，而 checkpoint 属于 ToolExecutor。直接在 `safe_handle_function_call()` 前创建快照，会让 `.env` 等最终被 preflight 阻断的调用也创建 checkpoint，违反既有 Phase 10 不变量。

因此增加一个最小 `before_dispatch` callback：

```text
ToolExecutor
  -> safe_handle_function_call(..., before_dispatch=checkpoint_callback)
      -> parse/lookup
      -> safety preflight
      -> blocked: return error, callback not called
      -> allowed: call best-effort callback
      -> registry handler dispatch
```

callback 只由 ToolExecutor 提供，只处理 `write_file` 和 `patch`。callback 异常必须被记录并吞掉，不能阻断 handler。

这保留了 Hermes 的职责意图：executor 决定何时需要 checkpoint，`model_tools` 继续拥有安全 preflight 和单工具 dispatch。

## Agent 生命周期

`AIAgent` 创建并持有 `_checkpoint_mgr`。每次 `while self.iteration_budget.consume()` 进入新 iteration 时先调用：

```python
self._checkpoint_mgr.new_turn()
```

CLI `build_agent()` 把规范化 checkpoint 配置传给 `AIAgent`。CheckpointManager 不是 Registry entry，不出现在 Provider tools schema 中。

## 错误处理

- 未启用：`ensure_checkpoint()` 返回 `False`，不创建目录。
- Git 不存在：返回 `False`，记录 debug 日志，工具继续执行。
- store 初始化、add、write-tree、commit-tree、update-ref 失败：返回 `False`，不向 tool result 注入 checkpoint 错误。
- 目录过宽、无效或超出 workspace 边界：跳过 checkpoint。
- 没有文件变化：不创建重复 commit。
- 无效 restore hash、hash 不属于当前 workspace、越界 file path：返回结构化失败结果，不执行 Git checkout。
- checkpoint 错误和安全错误语义不同：checkpoint fail-open，安全 preflight fail-closed。

## 本批不实现

- `/rollback`、`checkpoints` CLI 或 slash command。
- 对话消息回滚。
- terminal checkpoint。
- concurrent 或 segmented executor。
- checkpoint diff。
- global size cap、单文件 size cap、自动 prune marker、orphan/stale cleanup。
- legacy v1 store migration。
- Gateway/ACP/TUI checkpoint 配置和恢复入口。
- 新测试文件。

## 风险与控制

- 风险：checkpoint base 位于 workspace 内，可能递归快照自身。
  - 控制：shadow store 的 exclude 必须包含 `.learn_hermes/`。
- 风险：相对路径按进程 cwd 解析，快照错误项目。
  - 控制：必须复用 `ToolExecutionContext` 路径解析链并受 `workspace_root` 限制。
- 风险：blocked tool 提前占用去重槽。
  - 控制：callback 只在 safety preflight 通过后调用。
- 风险：用户 Git 配置弹出签名或 hook 交互。
  - 控制：隔离 global/system config，并显式关闭 GPG signing。
- 风险：restore hash 指向共享 store 中另一 workspace。
  - 控制：restore 前验证目标 commit 属于当前 workspace ref。
- 风险：store 长期增长。
  - 控制：本批实现每 workspace `max_snapshots`；全局容量和 stale/orphan maintenance 后延。

## 回滚路径

- `checkpoints.enabled=false` 可完全停用运行时行为。
- ToolExecutor 可移除 `before_dispatch` callback，恢复 Batch 4 的直接分发链。
- `model_tools` 的 callback 参数具有默认值，CLI 和其他调用方保持兼容。
- 删除 `_checkpoint_mgr.new_turn()` 和 manager 构造参数不会改变工具 schema 或 handler 接口。
- shadow store 与项目 `.git` 隔离，停用功能不需要修复用户仓库。

## 验收标准

- `compileall` 通过，不新增测试文件。
- 默认关闭时不创建 checkpoint store。
- 启用后，首次 `write_file` / `patch` 前创建当前 workspace 快照。
- 同一 executor batch 同 workspace 只创建一个 checkpoint。
- 下一 Provider iteration 可以创建新 checkpoint。
- `.env` 等 preflight block 不创建 checkpoint，也不占用去重槽。
- checkpoint 内部异常不阻断实际工具执行。
- 相对路径按 `ToolExecutionContext.cwd` 解析，绝不回退到错误的进程 cwd。
- restore 能恢复已修改文件，并创建 pre-rollback snapshot。
- checkpoint 不出现在八个内置工具 definitions 中。
- 现有 `tools`、`call-tool echo`、tool demo 和文件安全 preflight 行为保持不变。
