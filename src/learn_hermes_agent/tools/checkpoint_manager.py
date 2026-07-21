from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from learn_hermes_agent.config import get_checkpoints_dir_path

logger = logging.getLogger(__name__)

_STORE_DIRNAME = "store"
_REFS_PREFIX = "refs/hermes"
_INDEXES_DIRNAME = "indexes"
_GIT_TIMEOUT = 30
# 检查字符串是否像合法的 Git commit hash
# 目的主要有两个
# 1. 支持短 Git hash、40 位 SHA-1 和最长 64 位 SHA-256
# 2. 防止把恶意字符串或 Git 参数传给后续 Git 命令
_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")

DEFAULT_EXCLUDES = [
    ".git/",
    ".learn_hermes/",  # 必须排除，否则 checkpoint 可能把 checkpoint store 自身再次纳入快照
    ".hg/",
    ".svn/",
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    "dist/",
    "build/",
    "target/",
    ".env",
    ".env.*",
    "*.log",
]


def _normalize_path(path_value: str | Path) -> Path:
    return Path(path_value).expanduser().resolve()


def _project_hash(working_dir: str) -> str:
    """为每个工作目录生成稳定且较短的唯一标识"""
    normalized = str(_normalize_path(working_dir))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _store_path(base: Path) -> Path:
    return base / _STORE_DIRNAME


def _index_path(store: Path, project_hash: str) -> Path:
    """让不同 workspace 使用不同 Git index"""
    return store / _INDEXES_DIRNAME / project_hash


def _ref_name(project_hash: str) -> str:
    """让不同 workspace 使用不同 Git 引用"""
    return f"{_REFS_PREFIX}/{project_hash}"


def _validate_commit_hash(commit_hash: str) -> str | None:
    """防止非法 revision 或 Git 参数注入"""
    if not commit_hash or not commit_hash.strip():
        return "Empty commit hash"

    if commit_hash.startswith("-"):
        return "Invalid commit hash: value must not start with '-'"

    if not _COMMIT_HASH_RE.fullmatch(commit_hash):
        return "Invalid commit hash: expected 4-64 hexadecimal characters"

    return None


def _git_env(
        store: Path,
        working_dir: str,
        *,
        index_file: Path | None = None,
) -> dict[str, str]:
    """把 Git 操作重定向到 shadow store，避免碰项目自身的 .git"""
    env = os.environ.copy()

    env["GIT_DIR"] = str(store)
    env["GIT_WORK_TREE"] = str(_normalize_path(working_dir))
    # GIT_CONFIG_*: 阻止用户的签名、hook 等全局 Git 配置干扰后台快照
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    env.pop("GIT_NAMESPACE", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)

    # GIT_INDEX_FILE: 让每个 workspace 使用独立 index
    if index_file is None:
        env.pop("GIT_INDEX_FILE", None)
    else:
        env["GIT_INDEX_FILE"] = str(index_file)

    return env


def _run_git(
        args: list[str],
        store: Path,
        working_dir: str,
        *,
        index_file: Path | None = None,
        allowed_returncodes: set[int] | None = None,  # 只表示某些非零状态是预期行为、无需记录错误；返回值中的 ok 仍然只有退出码 0 才为 True。
) -> tuple[bool, str, str]:
    """统一处理超时、输出捕获、工作目录检查和 Windows 窗口隐藏"""
    worktree = _normalize_path(working_dir)

    if not worktree.exists() or not worktree.is_dir():
        return False, "", f"working directory not found: {worktree}"

    allowed = allowed_returncodes or set()

    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt"
        else 0
    )

    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(worktree),
            env=_git_env(
                store,
                str(worktree),
                index_file=index_file,
            ),
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("checkpoint git command failed: %s", exc)
        return False, "", str(exc)

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    # allowed_returncodes: 只表示某些非零状态是预期行为、无需记录错误；返回值中的 ok 仍然只有退出码 0 才为 True。
    if result.returncode != 0 and result.returncode not in allowed:
        logger.debug(
            "checkpoint git command failed: git %s: %s",
            " ".join(args),
            stderr,
        )

    return result.returncode == 0, stdout, stderr


def _init_store(store: Path, working_dir: str) -> str | None:
    # working_dir 暂时没有直接使用，但保留它是为了与 Hermes 的函数接口和后续调用方式对齐。
    if (store / "HEAD").exists():
        return None

    base = store.parent
    base.mkdir(parents=True, exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    (store / _INDEXES_DIRNAME).mkdir(exist_ok=True)

    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    # 初始化时清除外部 GIT_* 环境变量，避免错误操作其他仓库。
    for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(name, None)

    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt"
        else 0
    )
    try:
        # 用 git init --bare 创建共享 object store。
        result = subprocess.run(
            ["git", "init", "--bare", str(store)],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(base),
            env=env,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"checkpoint store initialization failed: {exc}"

    if result.returncode != 0:
        return (
            "checkpoint store initialization failed: "
            f"{result.stderr.strip()}"
        )

    config_working_dir = str(base)

    _run_git(
        ["config", "user.email", "learn-hermes@local"],
        store,
        config_working_dir,
    )
    _run_git(
        ["config", "user.name", "Learn Hermes Checkpoint"],
        store,
        config_working_dir,
    )
    _run_git(
        ["config", "commit.gpgsign", "false"],
        store,
        config_working_dir,
    )
    # 禁用签名和自动 GC，避免后台弹窗或自动清理干扰
    _run_git(
        ["config", "tag.gpgSign", "false"],
        store,
        config_working_dir,
    )
    _run_git(
        ["config", "gc.auto", "0"],
        store,
        config_working_dir,
    )
    # 写入 info/exclude。
    info_dir = store / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "exclude").write_text(
        "\n".join(DEFAULT_EXCLUDES) + "\n",
        encoding="utf-8",
    )

    return None

# 修复 Git GC 可能删除的必要空目录
def _repair_store_dirs(store: Path) -> None:
    """
    Git 的 bare store 内部通常有这些目录：
    store/
    ├── refs/
    │   └── heads/
    └── branches/
    Git GC（垃圾回收）会整理对象、压缩引用，有时会删除已经为空的目录。
    部分 Git 版本后续执行 git add 等操作时，如果这些目录缺失，可能错误地报告：fatal: not a git repository
    所以 Hermes 增加了防御性修复：_repair_store_dirs()
    它不会恢复 checkpoint 内容，也不会执行 Git GC，只是确保 shadow store 的内部目录结构完整。
    即使重复调用也没有副作用
    """
    # refs/heads：存放分支引用的标准目录
    # branches：bare Git 仓库的兼容目录。
    for relative in ("refs/heads", "branches"):
        (store / relative).mkdir(parents=True, exist_ok=True)
