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
