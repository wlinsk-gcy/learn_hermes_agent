from __future__ import annotations

import logging
import ntpath
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

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


def find_bash() -> str | None:
    """在 Windows 优先寻找 Git Bash 的标准安装位置；其他系统优先寻找 Bash，并保留 /bin/sh 作为最后降级"""
    if not _IS_WINDOWS:
        candidates = [
            shutil.which("bash"),
            "/usr/bin/bash",
            "/bin/bash",
            os.environ.get("SHELL"),
            "/bin/sh",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return None

    program_files = os.environ.get(
        "ProgramFiles",
        r"C:\Program Files",
    )
    program_files_x86 = os.environ.get(
        "ProgramFiles(x86)",
        r"C:\Program Files (x86)",
    )
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    candidates = [
        Path(program_files) / "Git" / "bin" / "bash.exe",
        Path(program_files_x86) / "Git" / "bin" / "bash.exe",
    ]
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "Programs"
            / "Git"
            / "bin"
            / "bash.exe"
        )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return shutil.which("bash.exe")
