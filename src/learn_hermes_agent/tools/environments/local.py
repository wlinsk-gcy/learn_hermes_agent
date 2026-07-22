from __future__ import annotations

import re
import logging
import ntpath
import os
import shutil
import signal
import subprocess
from pathlib import Path
import codecs
import threading
from collections import deque

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"
# 识别 terminal 输出中的常见 ANSI 控制序列，例如颜色、加粗和光标控制
# 把面向真实终端的颜色和光标指令删除，只把干净文本返回给 LLM
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

_BASH_EXTERNAL_PROGRAM_PROBE = (
    "/usr/bin/true; /usr/bin/cat --version >/dev/null"
)
_bash_starts_cache: dict[str, bool] = {}
_bash_probe_details_cache: dict[str, str] = {}
"""
ASLR（Address Space Layout Randomization，地址空间布局随机化）是一项内存安全机制。

它会让程序、动态库、堆栈等每次运行时加载到不同的内存地址，从而增加攻击者利用内存漏洞的难度。

这里查询的是 Windows 的：

ForceRelocateImages

也就是 Mandatory ASLR。它会强制重定位某些没有主动支持 ASLR 的程序。

Git for Windows 的 MSYS 通过模拟 fork() 创建子进程，对内存地址布局有特殊要求。Mandatory ASLR 可能破坏这种布局，导致：

dofork:
child_copy:
0xc0000142
0xc0000005

因此 Hermes 的流程是：

找到 bash.exe
    → 启动 Bash
    → 执行外部 MSYS 程序 true/cat
    → 失败时检查是否像 MSYS/ASLR 故障
    → 给出针对 Git 程序的修复命令

Hermes 不会自动关闭 ASLR，因为这是系统安全策略。它只提供针对 Git Bash 程序的例外配置建议，而不是关闭系统全局防护。

你机器返回：

mandatory-aslr=False

这里只表示系统没有强制启用 ForceRelocateImages，不代表 Windows 的所有 ASLR 防护都被关闭。
"""
_mandatory_aslr_enabled_cache: bool | None = None


def _git_bash_aslr_help(
        bash: str,
        details: str = "",
) -> str:
    """只生成错误信息，不修改系统安全策略"""
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


def _windows_hide_flags() -> int:
    if not _IS_WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
                # 这里故意执行 /usr/bin/true 和 /usr/bin/cat，而不只是 exit 0：某些 Git Bash 可以启动内置命令，但因 MSYS/ASLR 问题无法启动外部程序
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


def _looks_like_msys_spawn_failure(details: str) -> bool:
    """识别典型 MSYS/ASLR 子进程启动错误"""
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


def _git_root_from_bash(bash: str) -> str:
    r"""同时支持 <git>\bin\bash.exe 和 <git>\usr\bin\bash.exe 两种布局"""
    bin_dir = ntpath.dirname(ntpath.normpath(bash))
    if ntpath.basename(bin_dir).lower() != "bin":
        return ntpath.dirname(bin_dir)

    parent = ntpath.dirname(bin_dir)
    if ntpath.basename(parent).lower() == "usr":
        return ntpath.dirname(parent)
    return parent


def _mandatory_aslr_enabled() -> bool | None:
    """
    查询Mandatory ASLR

    返回值含义：

    - True：系统强制启用 ASLR。
    - False：明确关闭或未配置。
    - None：无法查询，不能据此下结论。
    """
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


def find_bash() -> str:
    """在 Windows 优先寻找 Git Bash 的标准安装位置；其他系统优先寻找 Bash，并保留 /bin/sh 作为最后降级"""
    if not _IS_WINDOWS:
        # POSIX分支
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

    candidates: list[Path] = []

    custom = os.environ.get("HERMES_GIT_BASH_PATH")
    if custom:
        custom_path = Path(custom)
        if custom_path.is_file():
            candidates.append(custom_path)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        portable_root = Path(local_app_data) / "hermes" / "git"
        for candidate in (
                portable_root / "bin" / "bash.exe",
                portable_root / "usr" / "bin" / "bash.exe",
        ):
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)

    standard_candidates = [
        Path(
            os.environ.get(
                "ProgramFiles",
                r"C:\Program Files",
            )
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
    ]

    if local_app_data:
        standard_candidates.append(
            Path(local_app_data)
            / "Programs"
            / "Git"
            / "bin"
            / "bash.exe"
        )

    for candidate in standard_candidates:
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)

    found = shutil.which("bash")
    if found:
        found_path = Path(found)
        if found_path not in candidates:
            candidates.append(found_path)

    for candidate_path in candidates:
        candidate = str(candidate_path)
        if _bash_starts(candidate):
            if (
                    candidate != custom
                    and custom
                    and Path(custom).is_file()
            ):
                logger.warning(
                    "HERMES_GIT_BASH_PATH=%s fails to start; "
                    "using %s instead",
                    custom,
                    candidate,
                )
            return candidate

    if candidates:
        probe_details = "\n".join(
            detail
            for candidate_path in candidates
            if (
                detail := _bash_probe_details_cache.get(
                    str(candidate_path)
                )
            )
        )

        if (
                _mandatory_aslr_enabled() is True
                or _looks_like_msys_spawn_failure(probe_details)
        ):
            raise RuntimeError(
                _git_bash_aslr_help(
                    str(candidates[0]),
                    probe_details,
                )
            )
        # 如果所有候选都启动失败但不像 ASLR 故障，Hermes 仍返回第一个候选，让真正执行命令时暴露原始 Bash 错误
        return str(candidates[0])

    raise RuntimeError(
        "Git Bash not found. Hermes Agent requires Git for Windows "
        "on Windows.\n"
        "Install it from: https://git-scm.com/download/win\n"
        "Or set HERMES_GIT_BASH_PATH to your bash.exe location."
    )


class _BoundedOutputCollector:
    """
    有界输出收集器

    """

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(1, int(max_chars))
        self._head_limit = int(self.max_chars * 0.4)  # 前 40% 保存输出开头。
        self._tail_limit = self.max_chars - self._head_limit  # 后 60% 保存输出结尾

        self._head: list[str] = []
        self._tail: deque[str] = deque()

        self._head_chars = 0
        self._tail_chars = 0
        self._total_chars = 0

        self._lock = threading.Lock()  # 用于后续读取线程和主线程之间同步

    @property
    def total_chars(self) -> int:
        with self._lock:
            return self._total_chars  # 记录未经裁剪的总字符数

    def append(self, text: str) -> None:
        """开头写满后不再变化；结尾使用 deque 持续淘汰最旧内容，内存始终受上限约束"""
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
        """suffix 用于保留 timeout 等重要说明。循环最多四次，是因为提示文字包含动态省略字符数，需要重新计算它自身占用的长度"""
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
    """删除颜色、光标控制等 ANSI 转义序列"""
    return _ANSI_ESCAPE_RE.sub("", value)


def _redact_known_values(
        value: str,
        secret_values: tuple[str, ...],
) -> str:
    """将已知 secret 的精确值替换为 [REDACTED]"""
    result = value
    for secret in secret_values:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def _build_subprocess_env(
        sensitive_env_names: set[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    env = os.environ.copy()

    blocked = {
        name.upper()
        for name in sensitive_env_names
        if name
    }
    # subprocess 不继承指定 API key、VIRTUAL_ENV 和 CONDA_PREFIX
    blocked.update({"VIRTUAL_ENV", "CONDA_PREFIX"})
    # 保存被移除变量的值，供输出层再次精确脱敏
    secret_values: set[str] = set()

    for key in list(env):
        if key.upper() not in blocked:
            continue

        value = env.pop(key, None)
        if value:
            secret_values.add(value)
    # secret 按长度降序排列，避免短值先替换后破坏长值匹配
    return (
        env,
        tuple(
            sorted(
                secret_values,
                key=len,
                reverse=True,
            )
        ),
    )


class LocalEnvironment:
    def __init__(self, bash_path: str) -> None:
        self.bash_path = bash_path

    # 后台输出读取
    @staticmethod
    def _drain_output(
            proc: subprocess.Popen[bytes],
            collector: _BoundedOutputCollector,
    ) -> None:
        """这里使用增量解码器，是因为 UTF-8 多字节字符可能刚好被两个 read(4096) 分开，不能对每个 chunk 独立 decode()"""
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

                collector.append(
                    decoder.decode(chunk)
                )
        except (OSError, ValueError):
            pass
        finally:
            try:
                tail = decoder.decode(b"", final=True)
                if tail:
                    collector.append(tail)
            except UnicodeDecodeError:
                pass

    # 进程树清理
    @staticmethod
    def _kill_process_tree(
            proc: subprocess.Popen[bytes],
    ) -> None:
        """Windows 使用 taskkill /T /F 清理整个子进程树；POSIX 使用进程组信号。两边失败时都会降级到 proc.kill()"""
        if _IS_WINDOWS:
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
                    creationflags=_windows_hide_flags(),
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
