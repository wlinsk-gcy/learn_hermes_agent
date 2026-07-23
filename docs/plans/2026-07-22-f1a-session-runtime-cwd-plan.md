# F1A Session Runtime CWD Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立按 session 隔离的运行时 cwd，使 terminal、file tools、安全 preflight 和 checkpoint 在连续工具调用中使用同一个逻辑工作目录。

**Architecture:** 使用 `ContextVar` 在统一 dispatch 范围绑定有效 `ToolExecutionContext`，并使用 thread-safe session cwd store 保存跨调用状态。LocalEnvironment 通过命令尾部 cwd marker 获取 `cd` 后目录；不改变现有 ToolHandler 签名。

**Tech Stack:** Python 3.11+、`contextvars`、`dataclasses`、`pathlib`、现有 Bash/Git Bash LocalEnvironment、`uv`。

---

## 执行约束

- 用户手工抄写代码；Codex 每次只输出一个 Task 的小步骤。
- Codex 不直接修改 `src/`，除非用户再次明确要求。
- 不新增测试文件。
- 不开始 F1B、Provider、approval UI、background/process、PTY、remote backend。
- 每个 Task 完成后先做轻量不变量验证，再进入下一 Task。

### Task 1：建立当前 dispatch context 绑定

**Files:**

- Modify: `src/learn_hermes_agent/agent/tool_context.py`
- Modify: `src/learn_hermes_agent/model_tools.py`

**Step 1：为 ToolExecutionContext 增加稳定 runtime key**

优先使用 `session_id`，否则使用 `task_id`，最后回退到 `default`。不要改变现有字段默认值。

**Step 2：增加 ContextVar helper**

在 `tool_context.py` 增加：

```python
_CURRENT_TOOL_CONTEXT: ContextVar[ToolExecutionContext | None] = (
    ContextVar(
        "learn_hermes_current_tool_context",
        default=None,
    )
)


@contextmanager
def bind_tool_execution_context(
    context: ToolExecutionContext | None,
) -> Iterator[None]:
    token = _CURRENT_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TOOL_CONTEXT.reset(token)


def get_current_tool_execution_context(
) -> ToolExecutionContext | None:
    return _CURRENT_TOOL_CONTEXT.get()
```

补充 `ContextVar`、`contextmanager` 和 `Iterator` imports。

**Step 3：只在 handler 执行范围绑定 context**

`model_tools.handle_function_call()` 保持 preflight 和 `before_dispatch` 顺序不变，然后使用：

```python
with bind_tool_execution_context(context):
    result = entry.handler(arguments)
```

**Step 4：验证绑定和 reset**

运行内联脚本，确认：

- `session_id` 优先于 `task_id`。
- handler 内能读取当前 context。
- handler 返回或抛异常后，外层 context 恢复为 `None`。

### Task 2：增加 session cwd store 和有效 context 解析

**Files:**

- Create: `src/learn_hermes_agent/agent/runtime_cwd.py`
- Modify: `src/learn_hermes_agent/model_tools.py`

**Step 1：实现 thread-safe cwd record**

提供：

```python
record_session_cwd(session_key, cwd)
get_session_cwd(session_key)
clear_session_cwd(session_key)
copy_session_cwd(source_key, target_key)
```

写入时使用 `Path(...).expanduser().resolve()`，只接受真实目录。无效 cwd 不覆盖已有值。

**Step 2：实现 resolve_tool_execution_context()**

当 context 非空时：

1. 查找 `context.runtime_key` 已记录 cwd。
2. 没有记录时，用 `context.cwd` 初始化该 session。
3. 有记录时，通过 `dataclasses.replace(context, cwd=recorded)` 返回新对象。
4. 不修改原 frozen dataclass，也不修改 `workspace_root`。

**Step 3：统一 dispatch 使用有效 context**

在 `handle_function_call()` 参数解析后计算 `execution_context`，并让以下三处都使用它：

```text
_preflight_tool_call
before_dispatch
bind_tool_execution_context / handler
```

**Step 4：验证 session 隔离**

使用两个临时目录和两个 context，确认记录 A 不改变 B，clear A 不删除 B，`workspace_root` 保持原值。

### Task 3：让 LocalEnvironment 报告命令结束 cwd

**Files:**

- Modify: `src/learn_hermes_agent/tools/environments/local.py`

**Step 1：增加 Windows/MSYS 路径转换 helper**

对齐 Hermes `_msys_to_windows_path()`：支持 `/c/...`、`/cygdrive/c/...` 和 `/mnt/c/...`，非 Windows 或普通 POSIX 路径不变。

**Step 2：包装命令并保留退出码**

每次 execute 生成随机 marker，包装语义为：

```bash
<original command>
__learn_hermes_ec=$?
printf '\n<marker>%s<marker>\n' "$(pwd -P)"
exit $__learn_hermes_ec
```

不得使用固定 marker，避免用户命令输出伪造解析边界。

**Step 3：解析并移除 marker**

在 ANSI 清理和 secret redaction 前解析 collector output：

- 找不到完整 marker：`cwd=None`，output 不变。
- 找到：移除注入换行和 marker，只保留用户命令输出。
- 转换 Windows path，并确认 `Path(cwd).is_dir()`。

**Step 4：在内部 result 增加 cwd**

`execute()` 返回：

```python
{
    "output": output,
    "returncode": returncode,
    "cwd": resolved_cwd_or_none,
}
```

terminal tool 最终对模型的公开 result 暂不增加字段。

**Step 5：手工验证**

确认：

- `printf exact` 仍只返回 `exact`。
- `cd child` 返回成功且内部 cwd 是 child。
- `false` 保持非零退出码。
- timeout 时没有伪造 cwd 更新。

### Task 4：terminal 接入 session cwd

**Files:**

- Modify: `src/learn_hermes_agent/tools/terminal_tool.py`

**Step 1：修改 workdir 解析接口**

`_resolve_workdir()` 接收当前 `ToolExecutionContext | None`，解析顺序为：

```text
explicit workdir
  -> context.cwd（已由 dispatch 解析为 recorded cwd）
  -> Path.cwd()
```

显式相对 workdir 以 context cwd 为基准。

**Step 2：读取 bound context**

`terminal_tool()` 使用 `get_current_tool_execution_context()`，不要重新创建 context。

**Step 3：记录命令结束 cwd**

LocalEnvironment 返回有效 cwd 时，调用：

```python
record_session_cwd(context.runtime_key, result_cwd)
```

没有 context、timeout、marker 缺失或 cwd 无效时不更新。

**Step 4：保持公开 result 不变**

仍只返回：

```text
output / exit_code / error
```

不修改 terminal schema，不增加 background 或 PTY。

### Task 5：让 file tools 和 checkpoint 使用同一个 cwd

**Files:**

- Modify: `src/learn_hermes_agent/tools/file_tools.py`
- Modify: `src/learn_hermes_agent/agent/tool_executor.py`

**Step 1：增加 file handler context helper**

file tools 优先使用：

```python
get_current_tool_execution_context()
```

只有直接调用 handler、当前没有 dispatch context 时，才回退到：

```python
create_tool_execution_context(load_config())
```

`read_file`、`write_file`、`patch`、`search_files` 四处统一调用 helper。

**Step 2：修正 terminal checkpoint 默认 cwd**

`_ensure_checkpoint()` 处理 terminal 时，将默认执行目录从：

```python
Path.cwd().resolve()
```

改为：

```python
tool_context.cwd.resolve()
```

显式相对 workdir 也以该目录为基准。

**Step 3：验证 workspace 边界**

terminal 可以 `cd` 到 workspace 外，但随后相对 `read_file` / `write_file` 必须被 `outside_workspace` 阻断。不得扩大文件工具权限。

### Task 6：补齐 CLI session 生命周期

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1：/new 清理旧 cwd record**

在切换到新 session id 前清理旧 key。

**Step 2：compression continuation 继承 cwd**

创建 child session 后：

1. copy old session cwd 到 child session。
2. clear old session cwd。

这只迁移进程内记录，不修改 SessionStore schema。

**Step 3：验证 task_id 变化不影响 cwd**

interactive CLI 每轮创建新 context，但相同 `session_id` 应继续使用同一 cwd。

### Task 7：集中验证与范围检查

**Files:**

- Read: `src/learn_hermes_agent/agent/tool_context.py`
- Read: `src/learn_hermes_agent/agent/runtime_cwd.py`
- Read: `src/learn_hermes_agent/model_tools.py`
- Read: `src/learn_hermes_agent/tools/environments/local.py`
- Read: `src/learn_hermes_agent/tools/terminal_tool.py`
- Read: `src/learn_hermes_agent/tools/file_tools.py`
- Read: `src/learn_hermes_agent/agent/tool_executor.py`
- Read: `src/learn_hermes_agent/cli/main.py`

**Step 1：运行 compileall**

```powershell
uv run python -m compileall -q src
```

预期退出码 0。

**Step 2：运行 session cwd 不变量脚本**

使用 `TemporaryDirectory` 建立两个 workspace，验证：

- A 执行 `cd child` 后后续 terminal 和 file tool 都从 child 解析。
- B 仍停留在自己的初始 cwd。
- marker 不出现在 output。
- A 离开 workspace 后 file tool 被阻断。

**Step 3：运行既有 CLI 回归**

```powershell
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{"text":"f1a-regression"}'
uv run learn-hermes-agent call-tool terminal '{"command":"printf f1a-terminal"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

**Step 4：检查范围**

```powershell
rg -n "background|pty|process_registry|docker|ssh|modal|daytona|singularity" src/learn_hermes_agent
git diff --check
git status --short
```

确认没有新增测试文件，没有实现 F1B 或 Provider。

### Task 8：更新进度文档

**Files:**

- Modify: `AGENTS.md`
- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`

集中验证通过后记录 F1A 已完成，下一步为 F1B Persistent Environment Snapshot。F1B 完成后才开始 Provider 源码重分析和独立设计。
