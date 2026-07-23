# F1B Persistent Environment Snapshot 设计

## 状态

- 日期：2026-07-23
- 状态：已确认
- 前置批次：F1A Session Runtime Context 已完成
- 参考 Hermes HEAD：`477c08b44766ace8b890faa72bf82ecbcf2b3ba8`
- 参考模块：
  - `tools/terminal_tool.py`
  - `tools/environments/base.py`
  - `tools/environments/local.py`

## 目标

让同一 `runtime_key` 的连续 terminal 调用复用本地 Shell 环境，使通过
`export` 和 `unset` 产生的环境变化跨调用保留，同时保证：

- 不同 session 的环境互相隔离。
- Provider 密钥等敏感变量不进入快照。
- timeout、启动失败或快照写入失败时保留上一份有效快照。
- F1A 已完成的 cwd、file tools、checkpoint 和 approval 语义不变。

## 范围

F1B 实现：

- 按 `runtime_key` 缓存 `LocalEnvironment`。
- 登录 Shell 初始化和启动探测。
- 私有 Shell 快照文件。
- 命令执行前静默 source 快照。
- 命令完成后生成唯一候选快照。
- Python 根据执行结果原子提交候选快照。
- `/new`、session key 迁移和进程退出时清理环境。

F1B 不实现：

- 后台 terminal 任务和 ProcessRegistry。
- PTY、stdin、read/close terminal。
- Docker、SSH、Modal 等远程环境。
- Gateway 空闲环境回收线程。
- Provider Runtime 改造。
- 普通局部变量持久化。
- 跨 Python 进程恢复。
- 运行过程中新增函数、alias 和 Shell 选项的持久化保证。

## Hermes 设计意图

Hermes 不尝试让子进程修改 Python 父进程的 `os.environ`。它为每个活动
environment 保存一份 Shell 脚本快照：

1. 创建 environment 时启动 login Bash。
2. 捕获 exported variables、公共函数、aliases 和必要 Shell 选项。
3. 每次执行命令前 source 快照。
4. 命令完成后通过 `export -p` 更新快照。
5. 使用唯一临时文件和原子替换，避免并发读取半写入文件。

`LocalEnvironment` 是快照的所有者，`terminal_tool.py` 负责按 session/task
身份缓存 environment。这使 cwd 和 exported environment 都成为连续 terminal
调用的 session 状态，而不是进程级普通全局状态。

## Architecture Decision Brief

### 方案 A：完全复制 Hermes 的 Shell 内提交

Shell 在命令完成后生成临时快照，并立即通过 `mv` 替换正式快照。

优点：

- 最接近 Hermes 当前源码。
- Shell 内生成和提交路径直接。

风险：

- 本项目已在 Windows Git Bash 验证到：timeout 杀死用户子进程后，外层 Bash
  wrapper 仍可能继续运行。
- 如果 wrapper 继续执行 `mv`，已经向调用方报告 timeout 的命令仍可能错误提交
  环境变化。

### 方案 B：Hermes 快照模型 + Python 控制最终提交

Shell 负责生成唯一候选快照，Python 只在命令完成且未 timeout 时使用
`os.replace()` 提交。

优点：

- 保留 Hermes 的 environment 所有权、login bootstrap、source、`export -p`
  和原子替换设计。
- timeout、启动失败和缺失候选文件不会覆盖上一份有效快照。
- 候选文件和正式文件位于同一目录时，`os.replace()` 可提供原子替换。

代价：

- 最终提交边界从 Shell 移到 Python，不是逐行复制 Hermes。

### 方案 C：每次创建 LocalEnvironment，只单独保存快照路径

该方案继续为每次调用创建 environment，把快照路径存入另一个 runtime store。

问题：

- 重复执行 login bootstrap。
- environment 与其快照由不同模块管理，清理和失败回退边界不清晰。
- 偏离 Hermes 的长生命周期 environment 设计。

### 决策

采用方案 B。

这是针对已验证 Windows timeout 行为的最小适配。若后续进程树终止语义能够保证
wrapper 不会继续运行，可回退到 Hermes 的 Shell 内 `mv`；terminal schema 和
调用方接口无需变化。

## 组件边界

### LocalEnvironment

文件：`src/learn_hermes_agent/tools/environments/local.py`

每个实例拥有：

- 随机 environment session id。
- 正式快照路径。
- cwd marker。
- `_snapshot_ready`。
- login Shell 可用性探测结果。
- 初始化、执行和幂等清理逻辑。

`LocalEnvironment` 继续负责平台相关 Bash 启动、Windows 路径转换、timeout、
进程树终止、输出清理和敏感值脱敏。

### terminal environment cache

文件：`src/learn_hermes_agent/tools/terminal_tool.py`

仿照 Hermes，在 terminal 模块维护：

```text
runtime_key -> LocalEnvironment
```

使用：

- 全局缓存锁保护映射。
- 按 `runtime_key` 的创建锁防止重复初始化。
- 获取创建锁后的二次检查防止竞态。

环境只属于 terminal，因此不新增 `agent/runtime_environment.py`。通用 agent
层继续只负责 `ToolExecutionContext` 和 runtime cwd。

### ToolExecutionContext

F1A 已实现的 context 继续提供：

- `runtime_key`
- `cwd`
- `workspace_root`
- safety / approval 所需信息

F1B 不把快照路径或 `LocalEnvironment` 放入 context。

## 初始化数据流

首次使用某个 `runtime_key`：

```text
terminal_tool
  -> 获取按 key 创建锁
  -> 二次检查缓存
  -> 创建 LocalEnvironment
  -> 启动 login Bash
  -> 加载可用 Shell 初始化文件
  -> 生成初始候选快照
  -> Python 原子提交正式快照
  -> 发布到 environment cache
```

初始快照捕获：

- `export -p`
- 公共 Shell 函数
- `alias -p`
- `shopt -s expand_aliases`
- `set +e`
- `set +u`

公共函数先通过 `declare -F` 取得名称，过滤 `_` 开头的内部函数，再按名称输出完整
定义。不能按行过滤 `declare -f`，否则可能只删除函数头并留下损坏快照的函数体。

快照写入前设置 `umask 077`。初始化成功后 `_snapshot_ready=True`。

## 命令数据流

每次前台命令：

```text
静默 source 正式快照
  -> cd 到本次有效 cwd
  -> eval 用户命令
  -> 立即保存退出码
  -> 删除敏感环境变量
  -> export -p 写入唯一候选快照
  -> 输出 cwd marker
  -> 使用原退出码退出
```

source 必须同时隐藏 stdout 和 stderr，避免某些 Bash 版本把 `declare -x` 输出到
工具结果。

候选文件名必须对并发 writer 唯一，并与正式快照位于同一目录。候选路径由 Python
为本次 execute 生成并传入 wrapper，不使用共享固定临时文件名。

## 提交语义

Python 等待命令结束后按下表处理：

| 执行结果 | 快照 | cwd |
| --- | --- | --- |
| 退出码为 0 | 提交候选快照 | 更新 |
| 非零退出码 | 提交候选快照 | 更新 |
| timeout | 删除候选，保留旧快照 | 不更新 |
| 启动失败 | 不提交 | 不更新 |
| 候选缺失 | 保留旧快照 | 只接受有效 marker |

非零退出仍提交，因为环境变更可能在失败前已经完成，例如：

```bash
export MODE=debug
false
```

提交使用 `os.replace(candidate_path, snapshot_path)`。任何提交失败都保留原正式
快照，并清理本次候选文件。

## 持久化边界

后续调用只承诺持久化：

```bash
export NAME=value
unset NAME
```

普通局部变量不会进入 `export -p`。

Hermes 的初始快照会捕获函数、alias 和 Shell 选项，但后续更新只执行
`export -p`。因此 F1B 对齐其行为，不承诺运行过程中新增或修改的函数、alias
和 Shell 选项跨调用保留。

## 敏感信息

采用两层防护：

1. 创建子进程环境时继续通过现有 `_build_subprocess_env()` 删除 Provider 密钥。
2. bootstrap 和每次候选快照捕获前再次 `unset` 敏感变量。

第二层用于处理 login 脚本或用户命令重新引入的敏感变量。只有符合以下规则的名称
可以插入 Shell `unset` 语句：

```text
^[A-Za-z_][A-Za-z0-9_]*$
```

快照路径和内容不得进入模型输出，现有命令输出敏感值脱敏继续保留。

## 并发语义

创建环境时按 key 加锁；命令执行本身不强制串行，这一点与 Hermes 的并发 writer
设计一致。

每个执行使用独立候选文件，正式快照通过原子替换发布。因此 reader 只能看到完整旧
快照或完整新快照。相同 session 并发写入时采用：

```text
last completed valid writer wins
```

F1B 不承诺按命令发起顺序合并并发环境变化。

## 失败回退

login bootstrap 失败时：

1. 保持 `_snapshot_ready=False`。
2. 探测非登录 Bash。
3. 若非登录 Bash 可用，后续使用 `bash -c`。
4. 否则退回每次使用 login Bash。

回退模式下 terminal 仍可执行，但不保证跨调用环境持久化。

候选快照生成或提交失败只影响本次环境更新，不得覆盖命令原始退出码或使上一份快照
失效。

## 生命周期

- 首次调用创建并缓存 environment。
- 后续调用复用 environment。
- `/new` 移除旧 key，并在锁外调用 `cleanup()`。
- session 压缩产生新 key 时，在锁内迁移同一个 environment 对象，不复制快照。
- Python 退出时通过 `atexit` 清理全部 environment。
- `cleanup()` 幂等删除正式快照和遗留候选文件。
- Windows 快照使用用户级 `%LOCALAPPDATA%\learn_hermes_agent\cache\terminal`，对齐
  Hermes 将本地 terminal artifact 放入应用 home cache 的意图；POSIX 继续使用系统
  temp。`chmod(0o600)` 只作为 POSIX mode 不变量，Windows 不用它代替 NTFS ACL 判断。

F1B 不维护尚无消费者的 `_last_activity`，也不实现 Hermes 的后台 idle-cleanup
线程。两者留到长期运行 Gateway 阶段；当前 CLI 依赖显式 session 生命周期和
`atexit`。

## 验收标准

1. 同一 session 的 exported variable 跨 terminal 调用保留。
2. 普通局部变量不保留。
3. `unset` 跨调用生效。
4. 不同 `runtime_key` 环境隔离。
5. 非零退出前完成的 `export` 会提交。
6. timeout 命令不提交候选快照，也不更新 cwd。
7. terminal cwd 与 file tools、checkpoint 继续一致。
8. 输出不包含 cwd marker、`declare -x`、快照路径或敏感值。
9. `/new` 清理旧环境，新 session 不继承旧变量。
10. session key 迁移后继续使用原环境。
11. 快照初始化失败时 terminal 仍可执行。
12. terminal schema 和现有调用接口不变。

## 验证方式

默认不新增测试文件。使用：

- `uv run python -m compileall src`
- CLI `doctor`、`tools` 和手工 terminal 调用
- 一次性 Python 脚本检查跨调用不变量
- schema / handler 接口一致性检查
- 快照权限、敏感变量和清理行为观察
- `git diff --check`
