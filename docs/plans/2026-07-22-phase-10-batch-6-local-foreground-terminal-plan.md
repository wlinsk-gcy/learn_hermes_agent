# Phase 10 Batch 6 Local Foreground Terminal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在现有 ToolRegistry、顺序 ToolExecutor、approval preflight 和 checkpoint 链上增加安全、可观察的本地 Bash 前台 terminal。

**Architecture:** terminal handler 负责参数和结果协议，LocalEnvironment 负责 Bash、子进程、timeout、进程树清理和有界输出。Approval 继续在 model_tools preflight 中先执行，ToolExecutor 只在 preflight 通过后为 destructive terminal 创建 best-effort checkpoint。

**Tech Stack:** Python 3.11+、subprocess、threading、pathlib、系统 Bash/Git Bash、当前 ToolRegistry / ToolExecutor / ToolExecutionContext / CheckpointManager、uv、CLI 与内联轻量不变量。

---

## 执行约束

- 按 Task 1 到 Task 8 顺序执行，一次只交付一个小任务。
- Python 代码默认只在对话中给出，由用户手抄；除非用户明确要求直接写入。
- 文档可由 Codex 直接维护。
- 不修改参考仓库 D:\python-develop\project\hermes-agent。
- 不新增测试文件；项目约定覆盖 writing-plans 技能的默认 TDD 要求。
- 验证使用 compileall、CLI、schema、内联不变量和临时目录副作用观察。
- 不自动提交；每个 Task 验证后由用户决定是否提交。
- 不实现 background、PTY、process、远程 backend、跨调用 shell 状态、交互 approval UI 或并发 executor。

## 执行状态（2026-07-22）

- [x] Task 1：增加 terminal 配置。
- [x] Task 2：建立 local environment 基础和有界输出。
- [x] Task 3：实现 LocalEnvironment 前台进程生命周期。
- [ ] Task 4：增加 terminal schema、handler 和 Registry 注册。
- [ ] Task 5：增加 destructive-command helper。
- [ ] Task 6：接入 destructive terminal checkpoint。
- [ ] Task 7：集中安全与回归验证。
- [ ] Task 8：更新路线与交接文档。

Task 2 执行时根据最新版 Hermes 源码修正了 Windows Bash 策略：不再从 PATH 中的 `git.exe` 自行推导安装目录，而是按 `HERMES_GIT_BASH_PATH`、Hermes portable Git、Git for Windows 标准目录和 PATH 的顺序收集候选，并用外部 MSYS 程序启动探测选择健康候选。Portable Git 的下载和安装仍不属于本批。

### Task 1：增加 terminal 配置

**Files:**

- Modify: src/learn_hermes_agent/config.py
- Modify: src/learn_hermes_agent/cli/main.py

**Step 1：增加默认配置**

在 DEFAULT_CONFIG 中、checkpoints 前增加：

~~~python
    "terminal": {
        "timeout_seconds": 180,
        "max_output_chars": 50_000,
    },
~~~

**Step 2：规范化 terminal 配置**

在 _normalize_config() 中、checkpoints_config 处理前增加：

~~~python
    terminal_config = config.get("terminal")
    if not isinstance(terminal_config, dict):
        terminal_config = {}

    default_terminal_config = DEFAULT_CONFIG["terminal"]

    terminal_timeout = terminal_config.get("timeout_seconds")
    if (
        isinstance(terminal_timeout, bool)
        or not isinstance(terminal_timeout, int)
        or terminal_timeout < 1
        or terminal_timeout > 600
    ):
        terminal_timeout = default_terminal_config["timeout_seconds"]

    max_output_chars = terminal_config.get("max_output_chars")
    if (
        isinstance(max_output_chars, bool)
        or not isinstance(max_output_chars, int)
        or max_output_chars <= 0
    ):
        max_output_chars = default_terminal_config["max_output_chars"]

    config["terminal"] = {
        "timeout_seconds": terminal_timeout,
        "max_output_chars": max_output_chars,
    }
~~~

**Step 3：doctor 输出规范化配置**

在 run_doctor() 的 checkpoint 输出前增加：

~~~python
    terminal = config["terminal"]
    print(f"terminal_timeout_seconds: {terminal['timeout_seconds']}")
    print(f"terminal_max_output_chars: {terminal['max_output_chars']}")
~~~

**Step 4：运行配置不变量**

~~~powershell
@'
import copy

from learn_hermes_agent.config import DEFAULT_CONFIG, _normalize_config

default = _normalize_config(copy.deepcopy(DEFAULT_CONFIG))
assert default["terminal"] == {
    "timeout_seconds": 180,
    "max_output_chars": 50_000,
}

invalid = _normalize_config({
    "terminal": {
        "timeout_seconds": True,
        "max_output_chars": 0,
    },
})
assert invalid["terminal"] == {
    "timeout_seconds": 180,
    "max_output_chars": 50_000,
}

custom = _normalize_config({
    "terminal": {
        "timeout_seconds": 30,
        "max_output_chars": 2_000,
    },
})
assert custom["terminal"] == {
    "timeout_seconds": 30,
    "max_output_chars": 2_000,
}

print("task-1-ok")
'@ | uv run python -

uv run learn-hermes-agent doctor
~~~

预期：输出 task-1-ok；doctor 显示 180 和 50000。

### Task 2：建立 local environment 基础和有界输出

**Files:**

- Create: src/learn_hermes_agent/tools/environments/__init__.py
- Create: src/learn_hermes_agent/tools/environments/local.py

**Step 1：创建 environments 包**

~~~python
"""Execution environments for built-in tools."""
~~~

**Step 2：创建 shell 查找和 bounded collector**

在 local.py 写入：

~~~python
from __future__ import annotations

import codecs
import logging
import ntpath
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

_BASH_EXTERNAL_PROGRAM_PROBE = (
    "/usr/bin/true; /usr/bin/cat --version >/dev/null"
)
_bash_starts_cache: dict[str, bool] = {}
_bash_probe_details_cache: dict[str, str] = {}
_mandatory_aslr_enabled_cache: bool | None = None


def _windows_hide_flags() -> int:
    if not _IS_WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _looks_like_msys_spawn_failure(details: str) -> bool:
    lowered = details.lower()
    return any(
        marker in lowered
        for marker in (
            "dofork:",
            "child_copy:",
            "0xc0000142",
            "0xc0000005",
        )
    )


def _mandatory_aslr_enabled() -> bool | None:
    global _mandatory_aslr_enabled_cache

    if _mandatory_aslr_enabled_cache is not None:
        return _mandatory_aslr_enabled_cache

    try:
        powershell = (
            shutil.which("powershell.exe")
            or "powershell.exe"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "(Get-ProcessMitigation -System).Aslr."
                    "ForceRelocateImages.ToString()"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_windows_hide_flags(),
        )
        if result.returncode != 0:
            return None

        value = (result.stdout or "").strip().upper()
        if value == "ON":
            _mandatory_aslr_enabled_cache = True
            return True
        if value in {"OFF", "NOTSET"}:
            _mandatory_aslr_enabled_cache = False
            return False
    except Exception as exc:
        logger.debug(
            "Could not query Windows Mandatory ASLR state: %s",
            exc,
        )
    return None


def _git_root_from_bash(bash: str) -> str:
    bin_dir = ntpath.dirname(ntpath.normpath(bash))
    if ntpath.basename(bin_dir).lower() != "bin":
        return ntpath.dirname(bin_dir)

    parent = ntpath.dirname(bin_dir)
    if ntpath.basename(parent).lower() == "usr":
        return ntpath.dirname(parent)
    return parent


def _git_bash_aslr_help(
    bash: str,
    details: str = "",
) -> str:
    git_root = _git_root_from_bash(bash)
    escaped_root = git_root.replace("'", "''")
    detail_line = (
        f"\nGit Bash probe output: {details[:500]}"
        if details
        else ""
    )
    return (
        f"Git Bash at {bash} cannot launch required MSYS child "
        "processes while Windows Mandatory ASLR "
        "(ForceRelocateImages) is enabled, or its output matches "
        f"that Git-for-Windows failure class.{detail_line}\n"
        "Reinstalling Git will not change the Windows mitigation "
        "policy. Open PowerShell as Administrator and run:\n"
        f"$gitRoot = '{escaped_root}'\n"
        'Get-Item "$gitRoot\\bin\\bash.exe", '
        '"$gitRoot\\usr\\bin\\*.exe" '
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "Set-ProcessMitigation -Name $_.FullName "
        "-Disable ForceRelocateImages }\n"
        "Then restart Hermes. If the override is blocked or later "
        "re-applied, ask your Windows administrator to allow this "
        "per-program exception."
    )


def _bash_starts(bash: str) -> bool:
    cached = _bash_starts_cache.get(bash)
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            [
                bash,
                "--noprofile",
                "--norc",
                "-c",
                _BASH_EXTERNAL_PROGRAM_PROBE,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_windows_hide_flags(),
        )
        ok = result.returncode == 0
        if not ok:
            combined = (
                f"{result.stdout or ''}{result.stderr or ''}"
            )
            _bash_probe_details_cache[bash] = (
                combined.strip()[:2000]
            )
            logger.debug(
                "bash probe failed for %s: %s",
                bash,
                combined.strip()[:200],
            )
    except Exception as exc:
        _bash_probe_details_cache[bash] = str(exc)[:2000]
        logger.debug("bash probe error for %s: %s", bash, exc)
        ok = False

    _bash_starts_cache[bash] = ok
    return ok


def find_bash() -> str:
    if not _IS_WINDOWS:
        return (
            shutil.which("bash")
            or (
                "/usr/bin/bash"
                if Path("/usr/bin/bash").is_file()
                else None
            )
            or (
                "/bin/bash"
                if Path("/bin/bash").is_file()
                else None
            )
            or os.environ.get("SHELL")
            or "/bin/sh"
        )

    candidates: list[str] = []

    custom = os.environ.get("HERMES_GIT_BASH_PATH")
    if custom and Path(custom).is_file():
        candidates.append(custom)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        portable_root = Path(local_app_data) / "hermes" / "git"
        for candidate_path in (
            portable_root / "bin" / "bash.exe",
            portable_root / "usr" / "bin" / "bash.exe",
        ):
            candidate = str(candidate_path)
            if candidate_path.is_file() and candidate not in candidates:
                candidates.append(candidate)

    for candidate_path in (
        Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        )
        / "Git"
        / "bin"
        / "bash.exe",
        Path(
            os.environ.get(
                "ProgramFiles(x86)",
                r"C:\Program Files (x86)",
            )
        )
        / "Git"
        / "bin"
        / "bash.exe",
        (
            Path(local_app_data)
            / "Programs"
            / "Git"
            / "bin"
            / "bash.exe"
            if local_app_data
            else None
        ),
    ):
        if candidate_path is None:
            continue
        candidate = str(candidate_path)
        if candidate_path.is_file() and candidate not in candidates:
            candidates.append(candidate)

    found = shutil.which("bash")
    if found and found not in candidates:
        candidates.append(found)

    for candidate in candidates:
        if _bash_starts(candidate):
            if (
                candidate != custom
                and custom
                and Path(custom).is_file()
            ):
                logger.warning(
                    "HERMES_GIT_BASH_PATH=%s fails to start; using %s "
                    "instead",
                    custom,
                    candidate,
                )
            return candidate

    if candidates:
        probe_details = "\n".join(
            detail
            for candidate in candidates
            if (
                detail := _bash_probe_details_cache.get(candidate)
            )
        )
        if (
            _mandatory_aslr_enabled() is True
            or _looks_like_msys_spawn_failure(probe_details)
        ):
            raise RuntimeError(
                _git_bash_aslr_help(candidates[0], probe_details)
            )
        return candidates[0]

    raise RuntimeError(
        "Git Bash not found. Hermes Agent requires Git for Windows "
        "on Windows.\n"
        "Install it from: https://git-scm.com/download/win\n"
        "Or set HERMES_GIT_BASH_PATH to your bash.exe location."
    )


class _BoundedOutputCollector:
    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(1, int(max_chars))
        self._head_limit = int(self.max_chars * 0.4)
        self._tail_limit = self.max_chars - self._head_limit
        self._head: list[str] = []
        self._tail: deque[str] = deque()
        self._head_chars = 0
        self._tail_chars = 0
        self._total_chars = 0
        self._lock = threading.Lock()

    @property
    def total_chars(self) -> int:
        with self._lock:
            return self._total_chars

    def append(self, text: str) -> None:
        if not text:
            return

        with self._lock:
            self._total_chars += len(text)
            start = 0

            if self._head_chars < self._head_limit:
                take = min(
                    self._head_limit - self._head_chars,
                    len(text),
                )
                if take:
                    self._head.append(text[:take])
                    self._head_chars += take
                    start = take

            remaining = text[start:]
            if not remaining or self._tail_limit <= 0:
                return

            if len(remaining) >= self._tail_limit:
                self._tail.clear()
                self._tail.append(
                    remaining[-self._tail_limit:]
                )
                self._tail_chars = self._tail_limit
                return

            self._tail.append(remaining)
            self._tail_chars += len(remaining)

            while self._tail_chars > self._tail_limit:
                excess = self._tail_chars - self._tail_limit
                first = self._tail[0]
                if len(first) <= excess:
                    self._tail.popleft()
                    self._tail_chars -= len(first)
                else:
                    self._tail[0] = first[excess:]
                    self._tail_chars -= excess

    def render(self, *, suffix: str = "") -> str:
        with self._lock:
            if len(suffix) >= self.max_chars:
                return suffix[-self.max_chars:]

            head = "".join(self._head)
            tail = "".join(self._tail)
            available = self.max_chars - len(suffix)

            if self._total_chars <= available:
                return head + tail + suffix

            notice = ""
            for _ in range(4):
                content_budget = max(
                    0,
                    available - len(notice),
                )
                head_chars = int(content_budget * 0.4)
                tail_chars = content_budget - head_chars
                omitted = max(
                    0,
                    self._total_chars
                    - head_chars
                    - tail_chars,
                )
                updated = (
                    "\n\n... [OUTPUT TRUNCATED - "
                    f"{omitted} chars omitted out of "
                    f"{self._total_chars} total] ...\n\n"
                )
                if updated == notice:
                    break
                notice = updated

            content_budget = max(
                0,
                available - len(notice),
            )
            head_chars = int(content_budget * 0.4)
            tail_chars = content_budget - head_chars
            rendered_tail = (
                tail[-tail_chars:] if tail_chars else ""
            )
            return (
                head[:head_chars]
                + notice[:available]
                + rendered_tail
                + suffix
            )


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def _redact_known_values(
    value: str,
    secret_values: tuple[str, ...],
) -> str:
    result = value
    marker = "[REDACTED]"

    for secret in secret_values:
        if not secret:
            continue

        replacement = (
            marker
            if len(secret) >= len(marker)
            else "*" * len(secret)
        )
        result = result.replace(secret, replacement)

    return result
~~~

**Step 3：验证 shell 和 collector**

~~~powershell
@'
from learn_hermes_agent.tools.environments.local import (
    _BoundedOutputCollector,
    find_bash,
)

bash = find_bash()
assert isinstance(bash, str) and bash, bash

collector = _BoundedOutputCollector(100)
collector.append("A" * 80)
collector.append("B" * 80)
output = collector.render()

assert len(output) <= 100
assert output.startswith("A")
assert output.endswith("B")
assert "OUTPUT TRUNCATED" in output

print("task-2-ok")
'@ | uv run python -

uv run python -m compileall -q src
~~~

预期：输出 task-2-ok；compileall 退出码为 0。

### Task 3：实现 LocalEnvironment 前台进程生命周期

**Files:**

- Modify: src/learn_hermes_agent/tools/environments/local.py

**Step 1：增加环境清理 helper**

放在 _redact_known_values() 后：

~~~python
def _build_subprocess_env(
    sensitive_env_names: set[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    env = os.environ.copy()
    blocked = {
        name.upper()
        for name in sensitive_env_names
        if name
    }
    blocked.update({"VIRTUAL_ENV", "CONDA_PREFIX"})

    secret_values: set[str] = set()
    for key in list(env):
        if key.upper() not in blocked:
            continue
        value = env.pop(key, None)
        if value:
            secret_values.add(value)

    return (
        env,
        tuple(sorted(secret_values, key=len, reverse=True)),
    )
~~~

**Step 2：实现 LocalEnvironment**

继续追加：

~~~python
class LocalEnvironment:
    def __init__(self, bash_path: str) -> None:
        self.bash_path = bash_path

    def execute(
        self,
        command: str,
        *,
        cwd: Path,
        timeout: int,
        max_output_chars: int,
        sensitive_env_names: set[str],
    ) -> dict[str, object]:
        env, secret_values = _build_subprocess_env(
            sensitive_env_names
        )
        collector = _BoundedOutputCollector(
            max_output_chars
        )

        creationflags = _windows_hide_flags()
        if _IS_WINDOWS:
            creationflags |= getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        proc = subprocess.Popen(
            [self.bash_path, "-c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
            start_new_session=not _IS_WINDOWS,
            creationflags=creationflags,
        )

        reader = threading.Thread(
            target=self._drain_output,
            args=(proc, collector),
            daemon=True,
        )
        reader.start()

        deadline = time.monotonic() + timeout
        timed_out = False

        try:
            poll_sleep = 0.01
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._kill_process_tree(proc)
                    break
                time.sleep(poll_sleep)
                poll_sleep = min(poll_sleep * 1.5, 0.2)
        except BaseException:
            self._kill_process_tree(proc)
            raise

        if timed_out:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Terminal process did not exit after tree kill: %s",
                    proc.pid,
                )

        reader.join(timeout=2)
        if reader.is_alive() and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
            reader.join(timeout=0.5)

        suffix = ""
        returncode = proc.returncode
        if timed_out:
            returncode = 124
            suffix = (
                f"\n[Command timed out after {timeout}s]"
            )

        output = collector.render(suffix=suffix)
        output = _strip_ansi(output)
        output = _redact_known_values(
            output,
            secret_values,
        )

        return {
            "output": output,
            "returncode": (
                returncode
                if isinstance(returncode, int)
                else -1
            ),
        }

    @staticmethod
    def _drain_output(
        proc: subprocess.Popen[bytes],
        collector: _BoundedOutputCollector,
    ) -> None:
        stream = proc.stdout
        if stream is None:
            return

        decoder = codecs.getincrementaldecoder(
            "utf-8"
        )(errors="replace")
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                collector.append(decoder.decode(chunk))
        except (OSError, ValueError):
            pass
        finally:
            try:
                tail = decoder.decode(b"", final=True)
                if tail:
                    collector.append(tail)
            except UnicodeDecodeError:
                pass

    @staticmethod
    def _kill_process_tree(
        proc: subprocess.Popen[bytes],
    ) -> None:
        if _IS_WINDOWS:
            flags = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )
            try:
                completed = subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(proc.pid),
                        "/T",
                        "/F",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                    creationflags=flags,
                )
                if completed.returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass

            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
            return

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
            return

        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
~~~

**Step 3：验证输出、退出码、secret、裁剪和 timeout**

~~~powershell
@'
import os
import shlex
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.tools.environments.local import (
    LocalEnvironment,
    find_bash,
)

bash = find_bash()
assert bash is not None
env = LocalEnvironment(bash)

with TemporaryDirectory() as raw:
    root = Path(raw)

    result = env.execute(
        "printf out; printf err >&2; exit 7",
        cwd=root,
        timeout=10,
        max_output_chars=50_000,
        sensitive_env_names=set(),
    )
    assert result["returncode"] == 7
    assert "out" in result["output"]
    assert "err" in result["output"]

    os.environ["OPENAI_API_KEY"] = "task-three-secret"
    try:
        hidden = env.execute(
            'printf "$OPENAI_API_KEY"',
            cwd=root,
            timeout=10,
            max_output_chars=50_000,
            sensitive_env_names={"OPENAI_API_KEY"},
        )
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
    assert "task-three-secret" not in hidden["output"]

    large = env.execute(
        "python -c 'print(\"A\" * 60000)'",
        cwd=root,
        timeout=10,
        max_output_chars=1_000,
        sensitive_env_names=set(),
    )
    assert len(large["output"]) <= 1_000
    assert "OUTPUT TRUNCATED" in large["output"]

    delayed = root / "late.txt"
    timeout_result = env.execute(
        "sleep 2; printf late > "
        + shlex.quote(delayed.as_posix()),
        cwd=root,
        timeout=1,
        max_output_chars=50_000,
        sensitive_env_names=set(),
    )
    assert timeout_result["returncode"] == 124
    time.sleep(2)
    assert not delayed.exists()

print("task-3-ok")
'@ | uv run python -
~~~

预期：约三秒后输出 task-3-ok。若 delayed 文件出现，说明 timeout 只杀了 shell，不能进入下一 Task。另需用短 secret 验证脱敏后的最终输出仍不超过 `max_output_chars`。

### Task 4：增加 terminal schema、handler 和 Registry 注册

**Files:**

- Create: src/learn_hermes_agent/tools/terminal_tool.py
- Modify: src/learn_hermes_agent/tools/registry.py

**Step 1：创建 terminal tool**

~~~python
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from learn_hermes_agent.config import load_config
from learn_hermes_agent.tools.approval import classify_command
from learn_hermes_agent.tools.environments.local import (
    LocalEnvironment,
    find_bash,
)
from learn_hermes_agent.tools.registry import (
    ToolEntry,
    ToolRegistry,
)

_STATIC_SENSITIVE_ENV_NAMES = {
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
}

_BACKGROUND_WRAPPER_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:nohup|disown|setsid)\b",
    re.IGNORECASE,
)


def _error_result(message: str) -> dict[str, object]:
    return {
        "output": "",
        "exit_code": -1,
        "error": message,
    }


def _strip_quoted_content(command: str) -> str:
    result: list[str] = []
    quote: str | None = None
    escaped = False

    for char in command:
        if escaped:
            result.append(" " if quote else char)
            escaped = False
            continue

        if char == "\\" and quote != "'":
            escaped = True
            result.append(" " if quote else char)
            continue

        if quote is not None:
            if char == quote:
                quote = None
            result.append(" ")
            continue

        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            continue

        result.append(char)

    return "".join(result)


def _contains_lone_background_ampersand(
    command: str,
) -> bool:
    unquoted = _strip_quoted_content(command)

    for index, char in enumerate(unquoted):
        if char != "&":
            continue

        previous = (
            unquoted[index - 1] if index > 0 else ""
        )
        following = (
            unquoted[index + 1]
            if index + 1 < len(unquoted)
            else ""
        )

        if previous in {"&", "<", ">"}:
            continue
        if following in {"&", ">"}:
            continue
        return True

    return False


def _foreground_error(command: str) -> str | None:
    unquoted = _strip_quoted_content(command)
    if _BACKGROUND_WRAPPER_RE.search(unquoted):
        return (
            "Background wrappers are not supported in this "
            "terminal batch."
        )
    if _contains_lone_background_ampersand(command):
        return (
            "Shell backgrounding with '&' is not supported "
            "in this terminal batch."
        )
    return None


def _provider_secret_env_names(
    config: dict[str, Any],
) -> set[str]:
    names = set(_STATIC_SENSITIVE_ENV_NAMES)
    model = config.get("model")
    if not isinstance(model, dict):
        return names

    api_key_env = model.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env:
        names.add(api_key_env)

    fallbacks = model.get("fallbacks")
    if isinstance(fallbacks, list):
        for fallback in fallbacks:
            if not isinstance(fallback, dict):
                continue
            fallback_env = fallback.get("api_key_env")
            if isinstance(fallback_env, str) and fallback_env:
                names.add(fallback_env)

    return names


def _resolve_workdir(raw_workdir: object) -> Path:
    if raw_workdir is None or raw_workdir == "":
        return Path.cwd().resolve()

    if not isinstance(raw_workdir, str):
        raise ValueError(
            "terminal workdir must be a string"
        )

    workdir = Path(raw_workdir).expanduser()
    if not workdir.is_absolute():
        workdir = Path.cwd() / workdir
    workdir = workdir.resolve()

    if not workdir.exists():
        raise ValueError(
            f"terminal workdir does not exist: {workdir}"
        )
    if not workdir.is_dir():
        raise ValueError(
            f"terminal workdir is not a directory: {workdir}"
        )

    return workdir


def check_terminal_requirements() -> bool:
    return find_bash() is not None


def terminal_tool(
    arguments: dict[str, Any],
) -> dict[str, object]:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return _error_result(
            "terminal requires a non-empty string argument: "
            "command"
        )

    risk = classify_command(command)
    if risk.level == "hardline":
        return _error_result(
            f"Hardline blocked: {risk.description}"
        )

    foreground_error = _foreground_error(command)
    if foreground_error is not None:
        return _error_result(foreground_error)

    config = load_config()
    terminal_config = config["terminal"]

    timeout = arguments.get(
        "timeout",
        terminal_config["timeout_seconds"],
    )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or timeout > 600
    ):
        return _error_result(
            "terminal timeout must be an integer "
            "between 1 and 600"
        )

    try:
        workdir = _resolve_workdir(
            arguments.get("workdir")
        )
    except ValueError as exc:
        return _error_result(str(exc))

    bash = find_bash()
    if bash is None:
        return _error_result(
            "terminal is unavailable: Bash was not found"
        )

    environment = LocalEnvironment(bash)
    try:
        result = environment.execute(
            command,
            cwd=workdir,
            timeout=timeout,
            max_output_chars=terminal_config[
                "max_output_chars"
            ],
            sensitive_env_names=(
                _provider_secret_env_names(config)
            ),
        )
    except OSError as exc:
        return _error_result(
            f"Failed to start terminal command: {exc}"
        )
    except Exception as exc:
        return _error_result(
            f"Terminal command failed: {exc}"
        )

    return {
        "output": str(result.get("output", "")),
        "exit_code": int(
            result.get("returncode", -1)
        ),
        "error": None,
    }


TERMINAL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "Bash command to execute in the local "
                "foreground environment."
            ),
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
            "description": (
                "Maximum seconds to wait for the command."
            ),
        },
        "workdir": {
            "type": "string",
            "description": (
                "Working directory for this command."
            ),
        },
    },
    "required": ["command"],
}


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="terminal",
            description=(
                "Execute one Bash command on the local host "
                "in the foreground. Background processes and "
                "interactive input are not supported."
            ),
            parameters=TERMINAL_PARAMETERS,
            handler=terminal_tool,
            toolset="terminal",
            check_fn=check_terminal_requirements,
        )
    )
~~~

**Step 2：加入 built-in discovery**

在 discover_builtin_tools() 中导入：

~~~python
    from learn_hermes_agent.tools.terminal_tool import (
        register_tools as register_terminal_tools,
    )
~~~

在现有 register 调用后增加：

~~~python
    register_terminal_tools(target)
~~~

**Step 3：验证 schema、注册和 handler**

~~~powershell
@'
import json

from learn_hermes_agent.model_tools import (
    get_tool_definitions,
    handle_function_call,
)

definitions = get_tool_definitions()
by_name = {
    item["function"]["name"]: item["function"]
    for item in definitions
}

assert "terminal" in by_name
properties = by_name["terminal"]["parameters"]["properties"]
assert set(properties) == {
    "command",
    "timeout",
    "workdir",
}
assert "background" not in properties
assert "pty" not in properties

safe = json.loads(handle_function_call(
    "terminal",
    {"command": "printf terminal-ok"},
))
assert safe["exit_code"] == 0, safe
assert safe["output"] == "terminal-ok", safe

background = json.loads(handle_function_call(
    "terminal",
    {"command": "sleep 10 &"},
))
assert background["exit_code"] == -1
assert "not supported" in background["error"]

hardline = json.loads(handle_function_call(
    "terminal",
    {"command": "rm -rf /"},
))
assert hardline["exit_code"] == -1
assert "Hardline blocked" in hardline["error"]

print("task-4-ok")
'@ | uv run python -

uv run learn-hermes-agent tools
~~~

预期：输出 task-4-ok；Bash 可用环境中 tools 包含九个工具。

### Task 5：增加 destructive-command helper

**Files:**

- Create: src/learn_hermes_agent/agent/tool_dispatch_helpers.py

**Step 1：创建独立 helper**

~~~python
from __future__ import annotations

import re

_DESTRUCTIVE_PATTERNS = re.compile(
    r"""(?:^|\s|&&|\|\||;|\x60)(?:
        rm\s|rmdir\s|
        cp\s|install\s|
        mv\s|
        sed\s+-i|
        truncate\s|
        dd\s|
        shred\s|
        git\s+(?:reset|clean|checkout)\s
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_REDIRECT_OVERWRITE = re.compile(
    r"(?<![>&])>(?![>&])|(?:^|\s)&>(?!>)"
)


def is_destructive_command(command: str) -> bool:
    if not command:
        return False
    if _DESTRUCTIVE_PATTERNS.search(command):
        return True
    return _REDIRECT_OVERWRITE.search(command) is not None
~~~

学习项目使用公开名称 is_destructive_command，避免以下划线开头的跨模块导入；判断意图与 Hermes 的 _is_destructive_command 对齐。

**Step 2：运行分类不变量**

~~~powershell
@'
from learn_hermes_agent.agent.tool_dispatch_helpers import (
    is_destructive_command,
)

for command in (
    "rm file.txt",
    "cp a b",
    "mv a b",
    "sed -i s/a/b/ file.txt",
    "git reset --hard HEAD",
    "printf x > file.txt",
    "printf x 2> error.txt",
):
    assert is_destructive_command(command), command

for command in (
    "printf hello",
    "git status",
    "printf x >> file.txt",
    "printf error >&2",
    "command 2>&1",
):
    assert not is_destructive_command(command), command

print("task-5-ok")
'@ | uv run python -
~~~

预期：输出 task-5-ok。

### Task 6：接入 destructive terminal checkpoint

**Files:**

- Modify: src/learn_hermes_agent/agent/tool_executor.py

**Step 1：增加 imports**

~~~python
from pathlib import Path

from learn_hermes_agent.agent.tool_dispatch_helpers import (
    is_destructive_command,
)
~~~

**Step 2：把文件专用 callback 扩展为统一 callback**

将 _ensure_file_checkpoint() 替换为：

~~~python
def _ensure_checkpoint(
    agent: AIAgent,
    function_name: str,
    function_args: dict[str, Any],
    tool_context: ToolExecutionContext | None,
) -> None:
    if (
        tool_context is None
        or not agent._checkpoint_mgr.enabled
    ):
        return

    if function_name in {"write_file", "patch"}:
        file_path = function_args.get("path")
        if (
            not isinstance(file_path, str)
            or not file_path.strip()
        ):
            return

        resolved_path = resolve_workspace_path(
            file_path,
            tool_context,
        )
        working_dir = (
            agent._checkpoint_mgr
            .get_working_dir_for_path(
                str(resolved_path),
                boundary=str(
                    tool_context.workspace_root
                ),
            )
        )
        agent._checkpoint_mgr.ensure_checkpoint(
            working_dir,
            f"before {function_name}",
        )
        return

    if function_name != "terminal":
        return

    command = function_args.get("command")
    if (
        not isinstance(command, str)
        or not is_destructive_command(command)
    ):
        return

    raw_workdir = function_args.get("workdir")
    if raw_workdir is None or raw_workdir == "":
        command_cwd = tool_context.cwd
    elif isinstance(raw_workdir, str):
        command_cwd = Path(raw_workdir).expanduser()
        if not command_cwd.is_absolute():
            command_cwd = (
                tool_context.cwd / command_cwd
            )
        command_cwd = command_cwd.resolve()
    else:
        return

    workspace_root = tool_context.workspace_root.resolve()
    try:
        command_cwd.relative_to(workspace_root)
    except ValueError:
        return

    working_dir = (
        agent._checkpoint_mgr.get_working_dir_for_path(
            str(command_cwd),
            boundary=str(workspace_root),
        )
    )
    agent._checkpoint_mgr.ensure_checkpoint(
        working_dir,
        "before terminal",
    )
~~~

**Step 3：更新 before_dispatch lambda**

把 _ensure_file_checkpoint 改为 _ensure_checkpoint，并保持其余参数不变：

~~~python
                    before_dispatch=(
                        lambda name, args, context:
                        _ensure_checkpoint(
                            agent,
                            name,
                            args,
                            context,
                        )
                    ),
~~~

**Step 4：验证审批先于 checkpoint、workspace 边界和 fail-open**

~~~powershell
@'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.agent.tool_context import (
    ToolExecutionContext,
)
from learn_hermes_agent.agent.tool_executor import (
    _ensure_checkpoint,
)
from learn_hermes_agent.model_tools import (
    handle_function_call,
)
from learn_hermes_agent.tools.checkpoint_manager import (
    CheckpointManager,
)
from learn_hermes_agent.tools.registry import (
    ToolEntry,
    ToolRegistry,
)


class AgentStub:
    pass


with TemporaryDirectory() as raw:
    root = Path(raw)
    workspace = root / "workspace"
    outside = root / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[project]\n",
        encoding="utf-8",
    )
    (workspace / "tracked.txt").write_text(
        "before\n",
        encoding="utf-8",
    )

    agent = AgentStub()
    agent._checkpoint_mgr = CheckpointManager(
        enabled=True,
        checkpoint_base=root / "checkpoints",
    )
    context = ToolExecutionContext(
        cwd=workspace,
        workspace_root=workspace,
    )

    _ensure_checkpoint(
        agent,
        "terminal",
        {
            "command": "printf after > tracked.txt",
            "workdir": str(workspace),
        },
        context,
    )
    checkpoints = agent._checkpoint_mgr.list_checkpoints(
        str(workspace)
    )
    assert len(checkpoints) == 1, checkpoints
    assert checkpoints[0]["reason"] == "before terminal"

    agent._checkpoint_mgr.new_turn()
    _ensure_checkpoint(
        agent,
        "terminal",
        {
            "command": "printf outside > file.txt",
            "workdir": str(outside),
        },
        context,
    )
    assert agent._checkpoint_mgr.list_checkpoints(
        str(outside)
    ) == []

calls = []
registry = ToolRegistry()
registry.register(ToolEntry(
    name="terminal",
    description="terminal",
    parameters={"type": "object"},
    handler=lambda arguments: (
        calls.append(arguments)
        or {"executed": True}
    ),
))

callback_calls = []
blocked = json.loads(handle_function_call(
    "terminal",
    {"command": "rm -r data"},
    registry=registry,
    context=ToolExecutionContext(
        approval_mode="ask"
    ),
    before_dispatch=lambda *args: (
        callback_calls.append(args)
    ),
))
assert blocked["status"] == "approval_required"
assert calls == []
assert callback_calls == []

print("task-6-ok")
'@ | uv run python -
~~~

预期：输出 task-6-ok。approval_required 必须在 callback 前返回。

### Task 7：集中安全与回归验证

**Files:**

- Read: src/learn_hermes_agent/tools/terminal_tool.py
- Read: src/learn_hermes_agent/tools/environments/local.py
- Read: src/learn_hermes_agent/agent/tool_dispatch_helpers.py
- Read: src/learn_hermes_agent/agent/tool_executor.py
- Read: src/learn_hermes_agent/model_tools.py
- Read: src/learn_hermes_agent/config.py

**Step 1：验证 approval policy**

使用 stub handler，确保验证不会执行真实危险命令：

~~~powershell
@'
import json

from learn_hermes_agent.agent.tool_context import (
    ToolExecutionContext,
)
from learn_hermes_agent.model_tools import (
    handle_function_call,
)
from learn_hermes_agent.tools.registry import (
    ToolEntry,
    ToolRegistry,
)

executed = []
registry = ToolRegistry()
registry.register(ToolEntry(
    name="terminal",
    description="terminal",
    parameters={"type": "object"},
    handler=lambda arguments: (
        executed.append(arguments["command"])
        or {"executed": True}
    ),
))

ask = json.loads(handle_function_call(
    "terminal",
    {"command": "sudo echo hi"},
    registry=registry,
    context=ToolExecutionContext(
        approval_mode="ask"
    ),
))
assert ask["status"] == "approval_required"

deny = json.loads(handle_function_call(
    "terminal",
    {"command": "sudo echo hi"},
    registry=registry,
    context=ToolExecutionContext(
        approval_mode="deny"
    ),
))
assert deny["status"] == "blocked"

auto = json.loads(handle_function_call(
    "terminal",
    {"command": "sudo echo hi"},
    registry=registry,
    context=ToolExecutionContext(
        approval_mode="auto"
    ),
))
assert auto == {"executed": True}

yolo = json.loads(handle_function_call(
    "terminal",
    {"command": "sudo echo hi"},
    registry=registry,
    context=ToolExecutionContext(
        yolo_enabled=True
    ),
))
assert yolo == {"executed": True}

hardline = json.loads(handle_function_call(
    "terminal",
    {"command": "rm -rf /"},
    registry=registry,
    context=ToolExecutionContext(
        approval_mode="auto",
        yolo_enabled=True,
    ),
))
assert hardline["status"] == "blocked"

assert executed == [
    "sudo echo hi",
    "sudo echo hi",
]
print("approval-ok")
'@ | uv run python -
~~~

**Step 2：编译和 CLI 回归**

~~~powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"terminal-regression\"}'
uv run learn-hermes-agent call-tool terminal '{\"command\":\"printf terminal-cli\"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
~~~

预期：

- 所有命令退出码为 0。
- doctor 显示 terminal 配置。
- tools 在 Bash 可用时包含九个工具。
- terminal-cli 输出的 JSON 包含 exit_code 0 和 terminal-cli。
- echo 与完整 tool demo 消息链不变。

**Step 3：源码和范围检查**

~~~powershell
rg -n "background|pty|process_registry|docker|ssh|modal|daytona|singularity|shell=True" src/learn_hermes_agent
rg -n "terminal|LocalEnvironment|is_destructive_command|before terminal" src/learn_hermes_agent
git diff --check
git status --short
~~~

确认：

- schema 没有 background 或 pty。
- 没有 remote backend、process registry 或 shell=True。
- terminal checkpoint 只在现有 post-preflight callback 中执行。
- 没有新测试文件。
- sandbox/ 等既有无关文件不处理。

### Task 8：更新路线与交接文档

**Files:**

- Modify: AGENTS.md
- Modify: docs/00-overview.md
- Modify: docs/02-roadmap.md
- Modify: docs/04-progress-handoff.md
- Modify: docs/plans/2026-06-03-hermes-agent-learning-roadmap.md
- Modify: docs/plans/2026-07-21-phase-10-post-file-tools-route-design.md

集中验证全部通过后记录：

- Phase 10 Batch 6 Local Foreground Terminal 已完成。
- 对齐 Hermes HEAD 477c08b44766ace8b890faa72bf82ecbcf2b3ba8。
- terminal 使用本地 Bash/Git Bash，仅支持前台命令。
- approval 继续先于 checkpoint；hardline 不能被 auto/yolo 绕过。
- destructive terminal 在 workspace 内创建 best-effort checkpoint。
- timeout、进程树清理、有界输出、ANSI 清理和 Provider secret 环境过滤已接入。
- terminal toolset/check_fn/schema 和九工具 definitions 已验证。
- 尚未实现 approval UI、background/process、PTY、跨调用 cwd/env、remote backend、并发 executor 或 checkpoint CLI。
- 下一步必须重新对齐最新版 Hermes，再决定 approval surface 与 persistent local session 的先后顺序。

运行：

~~~powershell
rg -n "Batch 6|Local Foreground Terminal|approval|process|PTY|persistent|remote" AGENTS.md docs/00-overview.md docs/02-roadmap.md docs/04-progress-handoff.md docs/plans
git diff --check
git status --short
~~~

预期：所有状态一致指向 Batch 6 已完成和下一批重新设计；Python 实现范围没有越过本计划。
