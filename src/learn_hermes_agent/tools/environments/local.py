from __future__ import annotations

import time
import re
import logging
import ntpath
import os
import shlex  # 把快照路径安全地嵌入 Bash 命令
import shutil
from uuid import uuid4
import signal
import subprocess
import tempfile  # 获取系统临时目录，快照不会写入项目 workspace
from pathlib import Path
import codecs
import threading
from collections import deque

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"


def _msys_to_windows_path(cwd: str) -> str:
    """
    这里只转换明确带单字母盘符的路径，避免把 /home、/tmp 等普通 POSIX 路径错误转换。

    例如：
    /c/Users/admin          → C:\\Users\\admin
    /cygdrive/d/project     → D:\\project
    /mnt/e/code             → E:\\code
    /c                      → C:\\
    C:\\Users\\admin        → 保持不变
    /home/admin             → 保持不变
    """
    if not _IS_WINDOWS or not cwd:
        return cwd

    match = re.match(
        r"^/(?:(?:cygdrive|mnt)/)?"
        r"([a-zA-Z])(/.*)?$",
        cwd,
    )
    if match is None:
        return cwd

    drive = match.group(1).upper()
    tail = (match.group(2) or "").replace(
        "/",
        "\\",
    )

    return f"{drive}:{tail or chr(92)}"


def _windows_to_msys_path(path: str) -> str:
    """把 Windows 盘符路径转换成 Git Bash 的 /c/... 形式。供 Python 将快照路径插入 Git Bash 脚本时使用"""
    if not _IS_WINDOWS or not path:
        return path

    match = re.match(
        r"^([a-zA-Z]):[\\/]*(.*)$",
        path,
    )
    if match is None:
        return path

    drive = match.group(1).lower()
    tail = (
            match.group(2) or ""
    ).replace("\\", "/").lstrip("/")

    return (
        f"/{drive}/{tail}"
        if tail
        else f"/{drive}/"
    )


def _bash_safe_path(path: str) -> str:
    """转换 Windows 路径并清理残留反斜杠"""
    if not _IS_WINDOWS or not path:
        return path

    converted = _windows_to_msys_path(path)
    return converted.replace("\\", "/")


def _quote_bash_path(path: str | Path) -> str:
    """处理空格、单引号等 Shell 特殊字符，防止路径被拆成多个参数"""
    return shlex.quote(
        _bash_safe_path(str(path))
    )


_ENV_NAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


def _snapshot_sensitive_env_names(
        sensitive_env_names: set[str],
) -> tuple[str, ...]:
    """
    合并 Provider 密钥变量与当前已有的虚拟环境变量过滤规则.
    删除 bad-name、A; rm ... 等非法名称，防止后续拼入 Shell 命令时形成注入
    排序后输出稳定，便于观察和复现
    """
    names = set(sensitive_env_names)
    names.update({
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
    })

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
    """生成安全的 unset 命令"""
    # 返回 list[str]：后续可以直接用 parts.extend(...) 插入命令包装器
    return [
        # unset -- NAME：删除变量，-- 表示后面不是命令选项
        # 2>/dev/null：不把 readonly 等错误写入 terminal 输出
        # || true：删除失败不能中断用户命令的收尾流程
        f"unset -- {name} 2>/dev/null || true"
        for name in _snapshot_sensitive_env_names(
            sensitive_env_names
        )
    ]


def _resolve_shell_init_files() -> list[str]:
    """解析 Shell 初始化文件"""
    if _IS_WINDOWS:
        # Windows 返回空列表，因为 Git Bash login shell 已负责加载 profile，额外 source 容易重复加载并引入路径问题
        return []

    resolved: list[str] = []
    # POSIX 下寻找真实存在的 profile 和 bashrc
    for raw in (
            "~/.profile",
            "~/.bash_profile",
            "~/.bashrc",
    ):
        try:
            # 展开 ~ 和环境变量
            path = os.path.expandvars(
                os.path.expanduser(raw)
            )
        except Exception:
            # 忽略不存在或无法解析的文件，不能让缺失配置阻止 terminal
            continue

        if path and os.path.isfile(path):
            resolved.append(path)

    return resolved


def _prepend_shell_init(
        command: str,
        files: list[str],
) -> str:
    """
    前置加载初始化文件

    生成的脚本类似：

    set +e
    [ -r '/home/user/.profile' ] && . '/home/user/.profile' 2>/dev/null || true
    原 bootstrap 命令
    """
    if not files:
        # 没有文件时原样返回命令
        return command
    # set +e 和 || true：配置文件失败不能终止 bootstrap
    prelude = ["set +e"]

    for path in files:
        # 安全处理路径中的空格和单引号
        quoted = _quote_bash_path(path)
        prelude.append(
            # -r：只 source 可读文件
            f"[ -r {quoted} ] && . {quoted} "
            "2>/dev/null || true"
        )

    return "\n".join((
        *prelude,
        command,
    ))


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
    """
    将已知 secret 的精确值替换为 [REDACTED]
    长 secret 仍显示 [REDACTED]；短 secret 使用等长 *，保证脱敏不会扩大输出
    """
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


def _wrap_command_with_cwd_marker(
        command: str,
) -> tuple[str, str]:
    r"""
    这个函数的目的是：让 Python 知道 Bash 命令执行结束后，shell 当前位于哪个目录。
    当前每次 terminal 调用都会创建新的 Bash 子进程：
    Python cwd = D:\project

    Bash 执行：cd src
    Bash cwd = D:\project\src

    Bash 退出
    Python cwd 仍然是 D:\project

    如果不获取 Bash 最后的 cwd，下一次 terminal 调用就无法继续从 src 运行。

    例如原命令：
    cd src
    包装后语义为：
    cd src
    __learn_hermes_ec=$?
    printf '\n<随机marker>%s<随机marker>\n' "$(pwd -P)"
    exit $__learn_hermes_ec
    """
    # 随机 marker 用于准确区分：用户命令正常输出 和  Hermes 内部附加的 cwd 信息
    # 后续解析器会删除 marker，不让它出现在返回给 LLM 的 output 中，并把其中的目录记录到 session：
    # 第一次：terminal("cd src") 然后 记录 session cwd = D:\project\src
    # 第二次：terminal("pwd") 默认从 D:\project\src 启动
    # 它只解决 cwd 持久化，不负责 export VAR=value 的跨调用保存；
    marker = (
        f"__LEARN_HERMES_CWD_{uuid4().hex}__"
    )

    wrapped_command = "\n".join(
        (
            command,
            "__learn_hermes_ec=$?",  # 立即保存原命令退出码。因为后面的 printf 会覆盖 $?。
            (
                f"printf '\\n{marker}%s{marker}\\n' "
                '"$(pwd -P)"'  # 返回解析符号链接后的物理路径
            ),  # 输出命令结束后的真实目录：<marker>/d/project/src<marker>
            "exit $__learn_hermes_ec",  # 仍使用原命令的退出码退出，避免 printf 成功导致失败命令被误报为成功。
        )
    )
    #  wrapped_command：追加了退出码保存和 pwd 输出的完整命令
    #  marker：本次调用专用的随机解析边界
    return wrapped_command, marker


# 完整运行状态包装器
def _wrap_command_with_runtime_state(
        command: str,
        *,
        cwd: Path,
        snapshot_path: Path | None,
        candidate_path: Path | None,
        sensitive_env_names: set[str],
) -> tuple[str, str]:
    marker = (
        f"__LEARN_HERMES_CWD_{uuid4().hex}__"
    )
    escaped = command.replace(
        "'",
        "'\\''",
    )
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


def _extract_cwd_from_output(
        output: str,
        marker: str,
) -> tuple[str, Path | None]:
    """接收完整输出和本次随机 marker，返回“清理后的输出”和“解析出的 cwd”"""
    # 空输入直接返回，没有输出或 marker 时无法解析，也不修改原输出
    if not output or not marker:
        return output, None
    # rfind() 从右向左查找，寻找最后一个 marker，返回 closing marker 的起始位置；找不到返回 -1
    closing_index = output.rfind(marker)
    # 如果连结束 marker 都没有，说明输出不完整
    if closing_index < 0:
        return output, None
    # 寻找对应的 opening marker，只在 closing marker 前面查找，并取最后一个匹配项
    opening_index = output.rfind(
        marker,
        0,
        closing_index,
    )
    # 检查 opening marker，如果只有一个 marker，不能确定 cwd 边界，因此保持输出不变
    if opening_index < 0:
        return output, None
    # 提取两个 marker 之间的 cwd，例如：MARKER/d/project/srcMARKER， 提取出：/d/project/src
    cwd_text = output[
        opening_index + len(marker):
        closing_index
    ].strip()
    # 初始化内部内容的删除起点，默认从 opening marker 开始删除
    marker_start = opening_index
    # 检查 marker 前是否有 \r\n，Windows 风格换行占两个字符。如果存在，就连同 wrapper 注入的换行一起删除。max(0, ...) 防止索引小于零。
    if output[max(0, marker_start - 2):marker_start] == "\r\n":
        marker_start -= 2
    # 检查普通 \n：POSIX/Git Bash 通常使用单字符换行。
    elif output[max(0, marker_start - 1):marker_start] == "\n":
        marker_start -= 1
    # 计算 closing marker 后的位置：此时 marker_end 指向 closing marker 后的第一个字符。
    marker_end = closing_index + len(marker)
    # 删除 closing marker 后的换行，避免内部 cwd 信息被删除后留下多余空行
    if output[marker_end:marker_end + 2] == "\r\n":
        marker_end += 2
    elif output[marker_end:marker_end + 1] == "\n":
        marker_end += 1
    # 拼接清理后的用户输出，保留内部 marker 区域之前和之后的内容
    cleaned_output = (
            output[:marker_start]
            + output[marker_end:]
    )
    # 如果 marker 中的 cwd 为空，Path("") 会解析成当前目录
    # 拒绝空 cwd，marker 仍会被删除，但不会把空字符串误认为当前目录
    if not cwd_text:
        return cleaned_output, None
    # 转换 Git Bash 路径，在 Windows 上把 /d/project 转成 D:\project
    native_cwd = _msys_to_windows_path(
        cwd_text
    )
    # 构造规范绝对路径，展开 ~、处理 ./.. 并转成绝对路径。解析失败时保留已清理输出，但不更新 cwd
    try:
        cwd_path = Path(native_cwd).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return cleaned_output, None
    # 验证它是真实目录，不存在或是普通文件时拒绝记录
    if not cwd_path.is_dir():
        return cleaned_output, None
    # 例如：
    # 输入：hello\nMARKER/d/project/srcMARKER\n
    # 输出：("hello", Path("D:/project/src"))
    return cleaned_output, cwd_path


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
            cwd
            if cwd is not None
            else Path.cwd()
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

    def _new_snapshot_candidate(self) -> Path:
        """
        每次执行生成不同候选路径，避免并发命令写同一个临时文件.
        候选文件与正式快照位于同一目录，后续才能原子替换.
        """
        return self._snapshot_path.with_name(
            f"{self._snapshot_path.name}.tmp."
            f"{uuid4().hex}"
        )

    @staticmethod
    def _remove_file(path: Path | None) -> None:
        """幂等清理：路径不存在或删除失败都不会破坏 terminal 主流程"""
        if path is None:
            return

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _candidate_contains_sensitive_name(
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> bool:
        """
        export -p 会列出当前 Shell 中所有已导出的环境变量，例如：

        export OPENAI_API_KEY="secret"
        export MODE="debug"
        export -p

        输出类似：

        declare -x MODE="debug"
        declare -x OPENAI_API_KEY="secret"

        其中：

        - declare：声明变量。
        - -x：把变量标记为 exported environment variable。
        - 这些内容写入快照后，下次 source 就能恢复环境。

        正常情况下，我们会先执行：

        unset -- OPENAI_API_KEY

        再执行 export -p，这样密钥不会写入快照。

        但 readonly 变量不能删除：

        readonly OPENAI_API_KEY="secret"
        unset OPENAI_API_KEY
        # 报错，变量仍然存在

        因此 Python 会再读取候选快照，检查是否出现：

        declare -x OPENAI_API_KEY=...

        发现后返回 True，表示“候选快照不安全”，不允许替换正式快照。

        “Fail-closed”指：

        确认安全       → 允许提交
        发现敏感变量   → 拒绝提交
        文件无法读取   → 同样拒绝提交

        如果读取失败却返回 False，就相当于“虽然没检查成功，但假定安全并提交”，这叫 fail-open，可能泄漏密钥。
        """
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
            # 无法确认内容安全时拒绝提交
            return True

        prefix = "declare -x "

        for line in lines:
            if not line.startswith(prefix):
                continue

            name = line[len(prefix):].split(
                "=",
                1,
            )[0]

            if name in blocked:
                return True

        return False

    def _promote_snapshot_candidate(
            self,
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> bool:
        """原子提交候选快照"""
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
            candidate.chmod(0o600)  # 尽量限制为当前用户读写
            # 原子替换正式快照
            os.replace(
                candidate,
                self._snapshot_path,
            )
        except OSError as exc:
            logger.warning(
                "Could not publish terminal snapshot: %s",
                exc,
            )
            # 敏感或失败候选会被删除
            # 提交失败时旧正式快照不会被主动删除
            self._remove_file(candidate)
            return False
        # 返回值告诉调用者本次是否成功发布
        return True

    def cleanup(self) -> None:
        """
        实现幂等 cleanup

        只删除当前 environment 自己的:
        - 正式快照
        - 以该正式快照名称开头的遗留候选文件

        不会删除其他 session 的快照，也不删除共享临时目录。重复调用 cleanup() 仍然安全
        """
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

    def _run_bash(
            self,
            command: str,
            *,
            cwd: Path,
            env: dict[str, str],
            login: bool,  # login 不是“登录某个账号”，而是“是否把 Bash 启动为 login shell”
    ) -> subprocess.Popen[bytes]:
        """
        统一Bash的启动方法。
        login=True 第一次创建环境时，需要用 login shell 捕获完整初始环境
        之后执行普通命令时：login=False
        此时不再重复加载登录配置，而是 source 已保存的快照；
        所以这里的 login 可以理解为：
        True  = 初始化用户 Shell 环境
        False = 快速执行普通命令
        """
        if login:
            # login=True：执行 bootstrap，使用 bash -l -c

            command = _prepend_shell_init(
                command,
                _resolve_shell_init_files(),
            )
            # bash -l -c "命令"
            # -l 会让 Bash 按登录 Shell 的方式加载用户启动配置，例如：/etc/profile  ~/.bash_profile
            # 因此能够获得用户平时配置的：PATH等
            args = [
                self.bash_path,
                "-l",
                "-c",
                command,
            ]
        else:
            # login=False：执行普通命令，使用 bash -c
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

    def _wait_internal_process(
            self,
            proc: subprocess.Popen[bytes],
            timeout: int,
    ) -> int:
        """
        等待内部 Bash 进程.
        这是 bootstrap 和 Bash 可用性探测使用的内部等待方法
        """
        try:
            # communicate() 等待进程并排空输出管道，避免管道写满
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 超时后复用现有 _kill_process_tree()
            self._kill_process_tree(proc)

            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # 124 与当前 terminal timeout 退出码保持一致
            return 124
        # 它不处理模型输出，因为 bootstrap 输出属于内部实现
        return (
            proc.returncode
            if isinstance(proc.returncode, int)
            else -1
        )

    def _build_bootstrap_script(
            self,
            candidate: Path,
            sensitive_env_names: set[str],
    ) -> str:
        """构造 bootstrap 基础脚本"""
        quoted_candidate = _quote_bash_path(
            candidate
        )
        quoted_cwd = _quote_bash_path(
            self.cwd
        )

        parts = [
            "set +e",
            "umask 077",
            (
                f"builtin cd -- {quoted_cwd} "
                "|| exit 126"
            ),
            *_snapshot_unset_script(
                sensitive_env_names
            ),
            (
                f"export -p > {quoted_candidate} "
                "|| exit 1"
            ),
            (
                "__learn_hermes_fns=$(declare -F "
                "| awk '{print $3}' "
                "| grep -vE '^_[^_]') || true"
            ),
            (
                # 名称列表为空时不能执行裸 declare -f，否则它会输出全部函数
                '[ -n "$__learn_hermes_fns" ] '
                "&& declare -f $__learn_hermes_fns "
                f">> {quoted_candidate} 2>/dev/null "
                "|| true"
            ),
            f"alias -p >> {quoted_candidate}",
            (
                # expand_aliases 允许非交互 Bash 使用 alias
                "echo 'shopt -s expand_aliases' "
                f">> {quoted_candidate}"
            ),
            # set +e、set +u 防止下次 source 后意外启用严格退出行为
            f"echo 'set +e' >> {quoted_candidate}",
            f"echo 'set +u' >> {quoted_candidate}",
        ]

        return "\n".join(parts)

    def _probe_nonlogin(
            self,
            env: dict[str, str],
    ) -> bool:
        """
        探测 non-login Bash
        当 login bootstrap 失败时，这个函数检查普通 bash -c 是否仍可使用:
        - 可用：后续回退到 non-login Bash。
        - 不可用：保留 login-per-command 回退。
        - 它只探测 Bash，不执行用户命令。
        """
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
                        min(
                            15,
                            self._snapshot_timeout,
                        ),
                    )
                    == 0
            )
        except OSError:
            return False

    def init_session(
            self,
            sensitive_env_names: set[str],
    ) -> None:
        """
        完整流程：

        过滤子进程环境
            → 生成候选路径
            → login Bash 执行 bootstrap
            → 检查退出码
            → 校验并原子发布
            → 标记 snapshot ready

        任一步失败都会删除候选文件并探测 non-login fallback，不影响 terminal 后续降级执行
        """
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
                "Terminal snapshot initialization "
                "failed: %s",
                exc,
            )
            return

        self._snapshot_ready = True

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
        collector = _BoundedOutputCollector(max_output_chars)
        # 使用新的包装器
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

        login = (
                not self._snapshot_ready
                and not self._prefer_nonlogin
        )

        try:
            # 切换到统一 Bash 启动方法
            proc = self._run_bash(
                wrapped_command,
                cwd=cwd,
                env=env,
                login=login,
            )
        except BaseException:
            # 捕获 BaseException 只用于删除尚未提交的候选文件，然后立即重新抛出，不吞掉异常
            self._remove_file(candidate_path)
            raise

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
                poll_sleep = min(
                    poll_sleep * 1.5,
                    0.2,
                )
        except BaseException:
            self._kill_process_tree(proc)
            # 如果 Python 收到 KeyboardInterrupt、SystemExit 等异常，不能留下尚未校验和提交的候选快照
            self._remove_file(candidate_path)
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

        # snapshot 未初始化：candidate_path is None，不处理
        if candidate_path is not None:
            if timed_out:
                # 只删除候选，正式快照保持原值
                # 即使 Windows Bash wrapper 在 timeout 后继续运行，它也没有机会由 Python 提交为正式快照
                self._remove_file(candidate_path)
            else:
                # 正常完成：提交，包括非零退出码
                self._promote_snapshot_candidate(
                    candidate_path,
                    sensitive_env_names,
                )

        output = collector.render(suffix=suffix)
        output, resolved_cwd = _extract_cwd_from_output(
            output,
            cwd_marker,
        )
        #  Windows 终止 sleep 后，Bash wrapper 仍可能继续输出 marker。
        # 必须使用 timed_out 这个真实状态，而不能假设 timeout 时一定没有 marker。
        if timed_out:
            resolved_cwd = None
        output = _strip_ansi(output)
        output = _redact_known_values(
            output,
            secret_values,
        )
        # 现在 LocalEnvironment.execute() 会真实执行包装后的命令，但对调用方仍保留原始输出和退出码，同时额外提供内部 cwd
        return {
            "output": output,
            "returncode": (
                returncode
                if isinstance(returncode, int)
                else -1
            ),
            "cwd": (
                str(resolved_cwd)
                if resolved_cwd is not None
                else None
            ),
        }

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
