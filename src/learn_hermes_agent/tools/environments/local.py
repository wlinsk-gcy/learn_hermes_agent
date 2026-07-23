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
        collector = _BoundedOutputCollector(max_output_chars)
        # 对命令进行包装
        wrapped_command, cwd_marker = (
            _wrap_command_with_cwd_marker(command)
        )

        creationflags = _windows_hide_flags()
        if _IS_WINDOWS:
            creationflags |= getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        # 执行包装后的命令
        proc = subprocess.Popen(
            [self.bash_path, "-c", wrapped_command],
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
                poll_sleep = min(
                    poll_sleep * 1.5,
                    0.2,
                )
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
