# F1B Persistent Environment Snapshot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让同一 `runtime_key` 的连续本地前台 terminal 调用持久化 exported environment，同时保持 session 隔离、敏感变量过滤和 timeout 不提交语义。

**Architecture:** `LocalEnvironment` 拥有 login Shell 快照、候选文件和清理逻辑，`terminal_tool.py` 仿照 Hermes 按 `runtime_key` 缓存 environment。Shell 生成唯一候选快照，Python 仅在命令完成且未 timeout 时通过 `os.replace()` 原子提交。

**Tech Stack:** Python 3.11、标准库 `subprocess` / `threading` / `tempfile` / `atexit`、Git Bash、现有 `ToolExecutionContext` 与 runtime cwd。

---

## 执行约定

- 代码由用户手工抄写；Codex 一次只提供一个小步骤。
- Codex 不直接修改 `src/`，除非用户再次明确授权。
- 不新增测试文件。
- 每个 Task 使用 compile、CLI 手工运行、schema 检查或一次性 Python 命令验证。
- 不开始后台 terminal、PTY、remote environment、Provider 或 Gateway idle cleanup。
- 每完成一个 Task，再进入下一个 Task。

### Task 1: Shell 路径、初始化文件与敏感变量辅助函数

**Files:**

- Modify: `src/learn_hermes_agent/tools/environments/local.py`

**Step 1: 增加标准库导入**

在现有 import 区增加：

```python
import shlex
import tempfile
```

**Step 2: 增加 Windows -> Git Bash 路径转换**

在 `_msys_to_windows_path()` 后增加：

```python
def _windows_to_msys_path(path: str) -> str:
    """把 Windows 盘符路径转换成 Git Bash 可解析的 /c/... 形式。"""
    if not _IS_WINDOWS or not path:
        return path

    match = re.match(r"^([a-zA-Z]):[\\/]*(.*)$", path)
    if match is None:
        return path

    drive = match.group(1).lower()
    tail = (match.group(2) or "").replace("\\", "/").lstrip("/")
    return f"/{drive}/{tail}" if tail else f"/{drive}/"


def _bash_safe_path(path: str) -> str:
    if not _IS_WINDOWS or not path:
        return path

    converted = _windows_to_msys_path(path)
    return converted.replace("\\", "/")


def _quote_bash_path(path: str | Path) -> str:
    return shlex.quote(_bash_safe_path(str(path)))
```

这些函数只负责把 Python native path 安全插入 Bash 脚本，不改变 Python 文件 API
使用的路径。

**Step 3: 增加敏感变量名规范化**

```python
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _snapshot_sensitive_env_names(
        sensitive_env_names: set[str],
) -> tuple[str, ...]:
    names = set(sensitive_env_names)
    names.update({"VIRTUAL_ENV", "CONDA_PREFIX"})
    return tuple(
        sorted(
            name
            for name in names
            if _ENV_NAME_RE.fullmatch(name)
        )
    )


def _snapshot_unset_script(
        sensitive_env_names: set[str],
) -> list[str]:
    return [
        f"unset -- {name} 2>/dev/null || true"
        for name in _snapshot_sensitive_env_names(
            sensitive_env_names
        )
    ]
```

不得把未经校验的配置字符串直接插入 Shell。

**Step 4: 增加默认 Shell 初始化文件解析**

当前项目没有 Hermes 的 `terminal.shell_init_files` 配置，因此本批只复制默认自动探测
语义，不新增配置项：

```python
def _resolve_shell_init_files() -> list[str]:
    if _IS_WINDOWS:
        return []

    resolved: list[str] = []
    for raw in (
            "~/.profile",
            "~/.bash_profile",
            "~/.bashrc",
    ):
        try:
            path = os.path.expandvars(
                os.path.expanduser(raw)
            )
        except Exception:
            continue

        if path and os.path.isfile(path):
            resolved.append(path)

    return resolved


def _prepend_shell_init(
        command: str,
        files: list[str],
) -> str:
    if not files:
        return command

    prelude = ["set +e"]
    for path in files:
        quoted = _quote_bash_path(path)
        prelude.append(
            f"[ -r {quoted} ] && . {quoted} "
            "2>/dev/null || true"
        )

    return "\n".join((*prelude, command))
```

Windows login Bash 自己处理 profile；额外 source native Windows 路径容易重复加载或触发
MSYS 路径问题，因此与 Hermes 一致不自动追加。

**Step 5: 轻量验证**

Run:

```powershell
uv run python -m compileall src
```

Expected: `local.py` 编译成功。

Run:

```powershell
uv run python -c "from learn_hermes_agent.tools.environments.local import _snapshot_sensitive_env_names; print(_snapshot_sensitive_env_names({'OPENAI_API_KEY', 'bad-name'}))"
```

Expected: 包含 `OPENAI_API_KEY`、`VIRTUAL_ENV`、`CONDA_PREFIX`，不包含
`bad-name`。

### Task 2: LocalEnvironment 快照所有权与幂等清理

**Files:**

- Modify: `src/learn_hermes_agent/tools/environments/local.py`

**Step 1: 扩展构造函数**

保持旧调用暂时兼容，增加初始 cwd、敏感变量和 snapshot state：

```python
class LocalEnvironment:
    def __init__(
            self,
            bash_path: str,
            *,
            cwd: Path | None = None,
            sensitive_env_names: set[str] | None = None,
    ) -> None:
        self.bash_path = bash_path
        self.cwd = (
            cwd if cwd is not None else Path.cwd()
        ).expanduser().resolve()
        self._session_id = uuid4().hex[:12]
        self._snapshot_dir = (
            Path(tempfile.gettempdir())
            / "learn_hermes_agent"
            / "terminal"
        )
        self._snapshot_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._snapshot_path = (
            self._snapshot_dir
            / f"snapshot-{self._session_id}.sh"
        )
        self._snapshot_ready = False
        self._prefer_nonlogin = False
        self._snapshot_timeout = 30
        self._initial_sensitive_env_names = set(
            sensitive_env_names or ()
        )
```

不要把 snapshot 放进 workspace，避免 file tools、Git 状态和模型上下文看到内部状态。
Task 2 只建立所有权，不调用尚未实现的 `init_session()`，保证该 Task 完成后现有
terminal 仍可运行。

**Step 2: 增加候选路径与删除辅助方法**

```python
    def _new_snapshot_candidate(self) -> Path:
        return self._snapshot_path.with_name(
            f"{self._snapshot_path.name}.tmp.{uuid4().hex}"
        )

    @staticmethod
    def _remove_file(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
```

**Step 3: 增加候选内容检查和原子提交**

```python
    @staticmethod
    def _candidate_contains_sensitive_name(
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> bool:
        blocked = set(
            _snapshot_sensitive_env_names(
                sensitive_env_names
            )
        )
        if not blocked:
            return False

        try:
            lines = candidate.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            return True

        prefix = "declare -x "
        for line in lines:
            if not line.startswith(prefix):
                continue
            name = line[len(prefix):].split("=", 1)[0]
            if name in blocked:
                return True

        return False

    def _promote_snapshot_candidate(
            self,
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> bool:
        if not candidate.is_file():
            return False

        if self._candidate_contains_sensitive_name(
                candidate,
                sensitive_env_names,
        ):
            self._remove_file(candidate)
            logger.warning(
                "Refused terminal snapshot containing "
                "a sensitive environment variable"
            )
            return False

        try:
            candidate.chmod(0o600)
            os.replace(
                candidate,
                self._snapshot_path,
            )
        except OSError as exc:
            logger.warning(
                "Could not publish terminal snapshot: %s",
                exc,
            )
            self._remove_file(candidate)
            return False

        return True
```

`unset` 是正常防线；候选内容检查用于处理 readonly 变量无法 unset 等异常情况。

**Step 4: 增加幂等 cleanup**

```python
    def cleanup(self) -> None:
        self._remove_file(self._snapshot_path)

        try:
            candidates = tuple(
                self._snapshot_dir.glob(
                    f"{self._snapshot_path.name}.tmp.*"
                )
            )
        except OSError:
            candidates = ()

        for candidate in candidates:
            self._remove_file(candidate)

        self._snapshot_ready = False
```

不要增加 `__del__`；解释器退出顺序不稳定，统一由 terminal cache 的 `atexit`
生命周期调用。

**Step 5: 轻量验证**

Run:

```powershell
uv run python -m compileall src
```

Expected: 编译通过；现有 `LocalEnvironment(find_bash())` 调用仍合法。

### Task 3: Login Shell 初始化与失败回退

**Files:**

- Modify: `src/learn_hermes_agent/tools/environments/local.py`

**Step 1: 抽取 Bash 启动方法**

让 bootstrap 和普通 execute 使用同一套 Popen 参数：

```python
    def _run_bash(
            self,
            command: str,
            *,
            cwd: Path,
            env: dict[str, str],
            login: bool,
    ) -> subprocess.Popen[bytes]:
        if login:
            command = _prepend_shell_init(
                command,
                _resolve_shell_init_files(),
            )
            args = [
                self.bash_path,
                "-l",
                "-c",
                command,
            ]
        else:
            args = [
                self.bash_path,
                "-c",
                command,
            ]

        creationflags = _windows_hide_flags()
        if _IS_WINDOWS:
            creationflags |= getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
            start_new_session=not _IS_WINDOWS,
            creationflags=creationflags,
        )
```

Task 4 再让 `execute()` 改用该方法，本步骤先只增加方法。

**Step 2: 增加 bootstrap 等待方法**

```python
    def _wait_internal_process(
            self,
            proc: subprocess.Popen[bytes],
            timeout: int,
    ) -> int:
        try:
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_process_tree(proc)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return 124

        return (
            proc.returncode
            if isinstance(proc.returncode, int)
            else -1
        )
```

**Step 3: 增加 bootstrap 脚本构造**

```python
    def _build_bootstrap_script(
            self,
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> str:
        quoted_candidate = _quote_bash_path(candidate)
        quoted_cwd = _quote_bash_path(self.cwd)

        parts = [
            "set +e",
            "umask 077",
            f"builtin cd -- {quoted_cwd} || exit 126",
            *_snapshot_unset_script(
                sensitive_env_names
            ),
            f"export -p > {quoted_candidate} || exit 1",
            (
                "__learn_hermes_fns=$(declare -F "
                "| awk '{print $3}' "
                "| grep -vE '^_[^_]') || true"
            ),
            (
                '[ -n "$__learn_hermes_fns" ] '
                "&& declare -f $__learn_hermes_fns "
                f">> {quoted_candidate} 2>/dev/null "
                "|| true"
            ),
            f"alias -p >> {quoted_candidate}",
            (
                "echo 'shopt -s expand_aliases' "
                f">> {quoted_candidate}"
            ),
            f"echo 'set +e' >> {quoted_candidate}",
            f"echo 'set +u' >> {quoted_candidate}",
        ]
        return "\n".join(parts)
```

**Step 4: 实现 init_session()**

```python
    def init_session(
            self,
            sensitive_env_names: set[str],
    ) -> None:
        env, _ = _build_subprocess_env(
            sensitive_env_names
        )
        candidate = self._new_snapshot_candidate()

        try:
            proc = self._run_bash(
                self._build_bootstrap_script(
                    candidate,
                    sensitive_env_names,
                ),
                cwd=self.cwd,
                env=env,
                login=True,
            )
            returncode = self._wait_internal_process(
                proc,
                self._snapshot_timeout,
            )
            if returncode != 0:
                raise RuntimeError(
                    "snapshot bootstrap failed with "
                    f"exit code {returncode}"
                )
            if not self._promote_snapshot_candidate(
                    candidate,
                    sensitive_env_names,
            ):
                raise RuntimeError(
                    "snapshot bootstrap did not publish "
                    "a valid snapshot"
                )
        except Exception as exc:
            self._snapshot_ready = False
            self._remove_file(candidate)
            self._prefer_nonlogin = (
                self._probe_nonlogin(env)
            )
            logger.warning(
                "Terminal snapshot initialization failed: %s",
                exc,
            )
            return

        self._snapshot_ready = True
```

**Step 5: 实现 non-login fallback 探测**

```python
    def _probe_nonlogin(
            self,
            env: dict[str, str],
    ) -> bool:
        try:
            proc = self._run_bash(
                "true",
                cwd=self.cwd,
                env=env,
                login=False,
            )
            return (
                self._wait_internal_process(
                    proc,
                    min(15, self._snapshot_timeout),
                )
                == 0
            )
        except OSError:
            return False
```

**Step 6: 从构造函数启动初始化**

在 Task 2 构造函数的 `_snapshot_timeout` 赋值后增加：

```python
        self.init_session(
            self._initial_sensitive_env_names
        )
```

**Step 7: 手工验证 bootstrap**

Run:

```powershell
uv run python -c "from pathlib import Path; from learn_hermes_agent.tools.environments.local import LocalEnvironment, find_bash; e=LocalEnvironment(find_bash(), cwd=Path.cwd(), sensitive_env_names=set()); print(e._snapshot_ready, e._snapshot_path.is_file()); e.cleanup(); print(e._snapshot_path.exists())"
```

Expected:

```text
True True
False
```

若第一行不是 `True True`，先检查 warning 和 Bash login 启动结果，不进入 Task 4。

### Task 4: 命令前 source、命令后候选快照与 Python 提交

**Files:**

- Modify: `src/learn_hermes_agent/tools/environments/local.py`

**Step 1: 替换 command wrapper**

将 `_wrap_command_with_cwd_marker()` 扩展为接收 cwd、正式快照、候选快照和敏感变量：

```python
def _wrap_command_with_runtime_state(
        command: str,
        *,
        cwd: Path,
        snapshot_path: Path | None,
        candidate_path: Path | None,
        sensitive_env_names: set[str],
) -> tuple[str, str]:
    marker = f"__LEARN_HERMES_CWD_{uuid4().hex}__"
    escaped = command.replace("'", "'\\''")
    parts: list[str] = []

    if snapshot_path is not None:
        quoted_snapshot = _quote_bash_path(
            snapshot_path
        )
        parts.append(
            f"source {quoted_snapshot} "
            ">/dev/null 2>&1 || true"
        )

    parts.append(
        f"builtin cd -- {_quote_bash_path(cwd)} "
        "|| exit 126"
    )
    parts.append(f"eval '{escaped}'")
    parts.append("__learn_hermes_ec=$?")
    parts.append("umask 077")

    if candidate_path is not None:
        parts.extend(
            _snapshot_unset_script(
                sensitive_env_names
            )
        )
        quoted_candidate = _quote_bash_path(
            candidate_path
        )
        parts.append(
            f"export -p > {quoted_candidate} "
            f"2>/dev/null || rm -f {quoted_candidate} "
            "2>/dev/null || true"
        )

    parts.append(
        f"printf '\\n{marker}%s{marker}\\n' "
        '"$(pwd -P)"'
    )
    parts.append("exit $__learn_hermes_ec")
    return "\n".join(parts), marker
```

不要在 Shell 中把候选文件 `mv` 成正式文件。

**Step 2: execute() 生成本次候选文件**

在 `execute()` 构造 wrapper 前增加：

```python
        self.cwd = cwd
        snapshot_path = (
            self._snapshot_path
            if self._snapshot_ready
            else None
        )
        candidate_path = (
            self._new_snapshot_candidate()
            if self._snapshot_ready
            else None
        )
        wrapped_command, cwd_marker = (
            _wrap_command_with_runtime_state(
                command,
                cwd=cwd,
                snapshot_path=snapshot_path,
                candidate_path=candidate_path,
                sensitive_env_names=(
                    sensitive_env_names
                ),
            )
        )
```

删除原 `_wrap_command_with_cwd_marker()` 调用。

**Step 3: execute() 使用统一 Bash 启动方法**

将直接 `subprocess.Popen(...)` 替换为：

```python
        login = (
            not self._snapshot_ready
            and not self._prefer_nonlogin
        )
        try:
            proc = self._run_bash(
                wrapped_command,
                cwd=cwd,
                env=env,
                login=login,
            )
        except BaseException:
            self._remove_file(candidate_path)
            raise
```

快照可用时使用普通 `bash -c`，因为 wrapper 会 source 快照。初始化失败时按探测结果
选择 login 或 non-login fallback。

**Step 4: 根据真实 timeout 状态提交**

在进程等待完成、`timed_out` 已确定后，渲染输出前增加：

```python
        if candidate_path is not None:
            if timed_out:
                self._remove_file(candidate_path)
            else:
                self._promote_snapshot_candidate(
                    candidate_path,
                    sensitive_env_names,
                )
```

非零 return code 也提交；只有 timeout、启动异常、候选缺失或候选校验失败不提交。

**Step 5: 同步 environment 自身 cwd**

解析 marker 并应用 timeout 保护后增加：

```python
        if resolved_cwd is not None:
            self.cwd = resolved_cwd
```

F1A runtime cwd store 仍是跨工具事实来源；`LocalEnvironment.cwd` 只保存该 environment
最近一次可确认目录。

**Step 6: 手工验证单 environment**

使用一次性 Python 脚本在同一个 `LocalEnvironment` 上依次执行：

1. `export F1B_VALUE=hello`
2. `printf '%s' "$F1B_VALUE"`
3. `unset F1B_VALUE`
4. 再次读取

Expected: 第二步输出 `hello`，第四步为空。

再验证：

```bash
export F1B_VALUE=kept
false
```

Expected: return code 非零，但下一次仍读到 `kept`。

最后验证 timeout：

```bash
export F1B_VALUE=bad
sleep 10
```

设置短 timeout。Expected: 下一次仍为 `kept`，不是 `bad`。

### Task 5: terminal_tool 按 runtime_key 缓存环境

**Files:**

- Modify: `src/learn_hermes_agent/tools/terminal_tool.py`

**Step 1: 增加缓存依赖和状态**

增加 imports：

```python
import atexit
import threading
```

在模块常量区增加：

```python
_ACTIVE_ENVIRONMENTS: dict[str, LocalEnvironment] = {}
_ENV_LOCK = threading.Lock()
_CREATION_LOCKS: dict[str, threading.Lock] = {}
_CREATION_LOCKS_LOCK = threading.Lock()
```

**Step 2: 增加按 key 创建锁**

```python
def _get_creation_lock(
        runtime_key: str,
) -> threading.Lock:
    with _CREATION_LOCKS_LOCK:
        lock = _CREATION_LOCKS.get(runtime_key)
        if lock is None:
            lock = threading.Lock()
            _CREATION_LOCKS[runtime_key] = lock
        return lock
```

**Step 3: 增加 get-or-create**

```python
def _get_or_create_environment(
        *,
        runtime_key: str,
        bash_path: str,
        cwd: Path,
        sensitive_env_names: set[str],
) -> LocalEnvironment:
    with _ENV_LOCK:
        environment = _ACTIVE_ENVIRONMENTS.get(
            runtime_key
        )
    if environment is not None:
        return environment

    creation_lock = _get_creation_lock(runtime_key)
    with creation_lock:
        with _ENV_LOCK:
            environment = _ACTIVE_ENVIRONMENTS.get(
                runtime_key
            )
        if environment is not None:
            return environment

        environment = LocalEnvironment(
            bash_path,
            cwd=cwd,
            sensitive_env_names=sensitive_env_names,
        )
        with _ENV_LOCK:
            _ACTIVE_ENVIRONMENTS[
                runtime_key
            ] = environment
        return environment
```

构造 environment 时不能持有 `_ENV_LOCK`，因为 login bootstrap 可能等待几十秒。

**Step 4: 增加显式清理**

```python
def clear_terminal_environment(
        runtime_key: str | None,
) -> None:
    key = str(runtime_key or "default")
    creation_lock = _get_creation_lock(key)

    with creation_lock:
        with _ENV_LOCK:
            environment = _ACTIVE_ENVIRONMENTS.pop(
                key,
                None,
            )

    if environment is not None:
        environment.cleanup()
```

先从缓存移除，再在锁外清理文件。

**Step 5: 增加 compression key 迁移**

```python
def move_terminal_environment(
        source_key: str | None,
        target_key: str | None,
) -> None:
    source = str(source_key or "default")
    target = str(target_key or "default")
    if source == target:
        return

    replaced: LocalEnvironment | None = None
    with _ENV_LOCK:
        environment = _ACTIVE_ENVIRONMENTS.pop(
            source,
            None,
        )
        if environment is None:
            return

        replaced = _ACTIVE_ENVIRONMENTS.pop(
            target,
            None,
        )
        _ACTIVE_ENVIRONMENTS[target] = environment

    if (
            replaced is not None
            and replaced is not environment
    ):
        replaced.cleanup()
```

迁移同一个对象，不复制包含敏感状态的快照文件。

**Step 6: 增加进程退出清理**

```python
def cleanup_terminal_environments() -> None:
    with _ENV_LOCK:
        environments = tuple(
            _ACTIVE_ENVIRONMENTS.values()
        )
        _ACTIVE_ENVIRONMENTS.clear()

    for environment in environments:
        environment.cleanup()


atexit.register(cleanup_terminal_environments)
```

**Step 7: terminal_tool() 改为复用缓存**

先只计算一次：

```python
    sensitive_env_names = _provider_secret_env_names(
        config
    )
    runtime_key = (
        context.runtime_key
        if context is not None
        else "default"
    )
```

将：

```python
environment = LocalEnvironment(bash)
```

替换为：

```python
environment = _get_or_create_environment(
    runtime_key=runtime_key,
    bash_path=bash,
    cwd=workdir,
    sensitive_env_names=sensitive_env_names,
)
```

`execute()` 使用同一个 `sensitive_env_names`，不要再次调用配置解析函数。

**Step 8: schema 与 session 隔离验证**

Run:

```powershell
uv run python -m compileall src
uv run learn-hermes-agent tools
```

Expected: terminal schema 没有新增参数。

使用两个不同 `ToolExecutionContext.session_id` 通过
`handle_function_call()` 调用 terminal：

- session A export `F1B_ONLY_A=yes`
- session A 能读取
- session B 读取为空

### Task 6: 接入 /new 和 compression 生命周期

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: 导入 terminal 生命周期函数**

```python
from learn_hermes_agent.tools.terminal_tool import (
    clear_terminal_environment,
    move_terminal_environment,
)
```

该依赖方向是 CLI -> tools；`terminal_tool.py` 不得反向导入 CLI。

**Step 2: /new 清理旧环境**

在切换 `session_id` 前，与 `clear_session_cwd()` 放在一起：

```python
clear_terminal_environment(session_id)
clear_session_cwd(session_id)
session_id = new_session_id
```

**Step 3: compression continuation 迁移环境**

创建 child session 后、清理旧 cwd 前增加：

```python
move_terminal_environment(
    old_session_id,
    session_id,
)
```

最终顺序：

```text
创建 child
  -> 迁移 terminal environment
  -> 复制 cwd
  -> 清理 parent cwd
```

**Step 4: 生命周期手工验证**

- session 中 export 一个变量。
- `/new` 后读取为空。
- 旧 environment 的 snapshot 文件已删除。
- compression 产生 child key 后变量仍可读取。
- 不修改 SessionStore schema。

### Task 7: 全链路验证

**Files:**

- No source changes unless validation reveals a defect.

**Step 1: 编译和 CLI 基线**

Run:

```powershell
uv run python -m compileall src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent echo "f1b-smoke"
```

Expected: 全部成功，terminal schema 不变。

**Step 2: 持久化不变量**

通过 `handle_function_call()` 验证：

1. `export` 持久化。
2. local variable 不持久化。
3. `unset` 持久化。
4. 非零退出前的 `export` 持久化。
5. timeout 不提交。
6. session A/B 隔离。

**Step 3: cwd 与 file tools 回归**

验证：

- terminal `cd child` 后下一次 terminal 在 `child`。
- 相对 `read_file` 从同一个 `child` 解析。
- workspace 外 file tools 仍被 safety 拒绝。
- timeout 不更新 cwd。

**Step 4: 敏感信息与内部输出**

设置一个动态 Provider env name 后验证：

- 子命令看不到其继承值。
- 正式 snapshot 不包含变量名或值。
- terminal 输出不包含 `declare -x`。
- terminal 输出不包含 snapshot 路径或 cwd marker。

**Step 5: 文件与 Git 检查**

Run:

```powershell
git diff --check
git status --short
```

Expected:

- 没有 whitespace error。
- 只有本批预期修改和已有用户文件。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。

### Task 8: 更新进度文档

**Files:**

- Modify: `AGENTS.md`
- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`

**Step 1: 记录完成状态**

仅在 Task 7 全部通过后记录：

- F1B Persistent Environment Snapshot 已完成。
- 对齐的 Hermes HEAD。
- Python-controlled promotion 是 Windows timeout 适配。
- 实际修改文件和验证命令。
- 已确认的不变量和仍未实现范围。

**Step 2: 更新下一步**

将下一方向改为重新对齐最新 Hermes Provider 链路，再为 Provider 方向建立独立设计，
不在 F1B 中提前实现 Provider。

**Step 3: 文档检查**

Run:

```powershell
git diff --check
```

Expected: 没有 whitespace error。
