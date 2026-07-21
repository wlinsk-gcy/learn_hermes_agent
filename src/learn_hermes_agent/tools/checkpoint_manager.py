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


def _validate_file_path(
        file_path: str,
        working_dir: str,
) -> str | None:
    """
    阻止三类危险输入
    - 空路径
    - 绝对路径，例如 D:/other/secret.txt
    - 目录穿越，例如 ../other/secret.txt
    """
    if not file_path or not file_path.strip():
        return "restore file path must not be empty"

    candidate = Path(file_path)

    if candidate.is_absolute():
        return "restore file path must be relative"

    root = _normalize_path(working_dir)
    resolved = (root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError:
        return "restore file path escapes the working directory"

    return None


class CheckpointManager:
    def __init__(
            self,
            *,
            enabled: bool = False,
            max_snapshots: int = 20,
            checkpoint_base: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.max_snapshots = max(1, int(max_snapshots))
        self.checkpoint_base = _normalize_path(
            checkpoint_base or get_checkpoints_dir_path()
        )

        self._checkpointed_dirs: set[str] = set()
        self._git_available: bool | None = None

    def new_turn(self) -> None:
        """清除去重状态"""
        self._checkpointed_dirs.clear()

    def ensure_checkpoint(
            self,
            working_dir: str,
            reason: str = "auto",
    ) -> bool:
        if not self.enabled:
            return False
        # _git_available 只探测一次，避免每次写文件都调用 shutil.which()。
        if self._git_available is None:
            self._git_available = shutil.which("git") is not None

        if not self._git_available:
            logger.debug(
                "checkpoints disabled: git executable not found"
            )
            return False

        path = _normalize_path(working_dir)
        filesystem_root = Path(path.anchor).resolve()
        # 禁止对磁盘根目录和用户主目录建立快照
        if path in {filesystem_root, Path.home().resolve()}:
            logger.debug(
                "checkpoint skipped for overly broad path: %s",
                path,
            )
            return False

        normalized = str(path)
        # 同一个 Agent iteration 内，同一 workspace 最多尝试一次
        if normalized in self._checkpointed_dirs:
            return False

        self._checkpointed_dirs.add(normalized)

        try:
            # 即使失败也不会阻止文件工具继续执行，即 fail-open。
            return self._take(normalized, reason)
        except Exception as exc:
            logger.debug(
                "checkpoint failed (non-fatal): %s",
                exc,
            )
            return False

    def _take(
            self,
            working_dir: str,
            reason: str,
            *,
            prune: bool = True,
    ) -> bool:
        store = _store_path(self.checkpoint_base)
        # 初始化store
        init_error = _init_store(store, working_dir)
        if init_error is not None:
            logger.debug(init_error)
            return False
        # 读取 workspace 当前 ref
        project_hash = _project_hash(working_dir)
        index_file = _index_path(store, project_hash)
        ref = _ref_name(project_hash)
        index_file.parent.mkdir(parents=True, exist_ok=True)
        # 用旧 commit 初始化独立 index
        ok_ref, ref_commit, _ = _run_git(
            [
                "rev-parse",
                "--verify",
                f"{ref}^{{commit}}",
            ],
            store,
            working_dir,
            allowed_returncodes={128},
        )
        has_ref = ok_ref and bool(ref_commit)

        if has_ref:
            _run_git(
                ["read-tree", ref_commit],
                store,
                working_dir,
                index_file=index_file,
                allowed_returncodes={128},
            )
        elif index_file.exists():
            index_file.unlink()
        # git add -A
        ok_add, _, add_error = _run_git(
            ["add", "-A"],
            store,
            working_dir,
            index_file=index_file,
        )
        if not ok_add:
            logger.debug(
                "checkpoint git add failed: %s",
                add_error,
            )
            return False
        # 检查是否有变化
        if has_ref:
            no_changes, _, _ = _run_git(
                [
                    "diff-index",
                    "--cached",
                    "--quiet",
                    ref_commit,
                ],
                store,
                working_dir,
                index_file=index_file,
                allowed_returncodes={1},
            )
            if no_changes:
                return False
        else:
            ok_files, files, _ = _run_git(
                ["ls-files", "--cached"],
                store,
                working_dir,
                index_file=index_file,
            )
            if ok_files and not files:
                return False
        # write-tree
        ok_tree, tree_hash, tree_error = _run_git(
            ["write-tree"],
            store,
            working_dir,
            index_file=index_file,
        )
        if not ok_tree or not tree_hash:
            logger.debug(
                "checkpoint write-tree failed: %s",
                tree_error,
            )
            return False
        # commit-tree
        commit_args = [
            "commit-tree",
            tree_hash,
            "-m",
            reason,
            "--no-gpg-sign",
        ]

        if has_ref:
            commit_args[2:2] = ["-p", ref_commit]

        ok_commit, commit_hash, commit_error = _run_git(
            commit_args,
            store,
            working_dir,
            index_file=index_file,
        )
        if not ok_commit or not commit_hash:
            logger.debug(
                "checkpoint commit-tree failed: %s",
                commit_error,
            )
            return False
        # update-ref
        update_args = [
            "update-ref",
            ref,
            commit_hash,
        ]
        if has_ref:
            update_args.append(ref_commit)

        ok_update, _, update_error = _run_git(
            update_args,
            store,
            working_dir,
        )
        if not ok_update:
            logger.debug(
                "checkpoint update-ref failed: %s",
                update_error,
            )
            return False
        # 裁剪旧 checkpoint
        if prune:
            self._prune(store, working_dir, ref)

        return True

    def get_working_dir_for_path(
            self,
            file_path: str,
            *,
            boundary: str | None = None,
    ) -> str:
        """最新版 Hermes 会向上寻找项目 marker；学习版增加 boundary，确保搜索不会越过安全 workspace。这是有意的安全收紧。"""
        path = _normalize_path(file_path)
        candidate = path if path.is_dir() else path.parent

        boundary_path = (
            _normalize_path(boundary)
            if boundary
            else None
        )

        if boundary_path is not None:
            try:
                candidate.relative_to(boundary_path)
            except ValueError:
                return str(boundary_path)

        markers = {
            ".git",
            ".hg",
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "Makefile",
        }

        current = candidate

        while True:
            if any(
                    (current / marker).exists()
                    for marker in markers
            ):
                # 找到最近的项目 marker 时，返回对应项目根目录
                return str(current)

            if (
                    boundary_path is not None
                    and current == boundary_path
            ):
                # 没找到 marker 且提供了 boundary，返回 boundary
                return str(boundary_path)

            if current == current.parent:
                break
            # 没有 boundary 时，回退到目标文件的父目录
            parent = current.parent

            if boundary_path is not None:
                try:
                    parent.relative_to(boundary_path)
                except ValueError:
                    # 目标在 boundary 外时，不继续向外搜索，直接返回 boundary
                    return str(boundary_path)

            current = parent

        return str(boundary_path or candidate)

    def restore(
            self,
            working_dir: str,
            commit_hash: str,
            file_path: str | None = None,
    ) -> dict[str, object]:
        """
        学习版比最新版 Hermes 多一层保护：merge-base --is-ancestor 确认 checkpoint 属于当前 workspace。
        共享 store 中即使存在其他 workspace 的 commit，也不能拿来恢复当前目录。

        本 Batch 不使用 git clean，因此不会删除 checkpoint 后新建的文件
        """
        # 先校验 commit hash 和文件路径
        hash_error = _validate_commit_hash(commit_hash)
        if hash_error is not None:
            return {
                "success": False,
                "error": hash_error,
            }

        normalized = str(_normalize_path(working_dir))

        if file_path is not None:
            path_error = _validate_file_path(
                file_path,
                normalized,
            )
            if path_error is not None:
                return {
                    "success": False,
                    "error": path_error,
                }

        store = _store_path(self.checkpoint_base)

        if not (store / "HEAD").exists():
            return {
                "success": False,
                "error": "No checkpoints exist for this directory",
            }

        ref = _ref_name(_project_hash(normalized))
        # 确认 commit 是当前 workspace ref 的祖先
        belongs, _, _ = _run_git(
            [
                "merge-base",
                "--is-ancestor",
                commit_hash,
                ref,
            ],
            store,
            normalized,
            allowed_returncodes={1, 128},
        )
        if not belongs:
            return {
                "success": False,
                "error": (
                    "Checkpoint does not belong to this directory"
                ),
            }
        # 恢复前自动建立 pre-rollback 快照，允许将来撤销恢复。
        self._take(
            normalized,
            (
                "pre-rollback snapshot "
                f"(restoring to {commit_hash[:8]})"
            ),
            prune=False,
        )

        project_hash = _project_hash(normalized)
        index_file = _index_path(store, project_hash)

        restore_target = (
            Path(file_path).as_posix()
            if file_path
            else "."
        )
        #  -- 明确结束 Git 参数，避免文件路径被解释成选项。
        ok, _, error = _run_git(
            [
                "checkout",
                commit_hash,
                "--",
                restore_target,
            ],
            store,
            normalized,
            index_file=index_file,
        )
        if not ok:
            return {
                "success": False,
                "error": f"Restore failed: {error}",
            }

        self._prune(store, normalized, ref)

        result: dict[str, object] = {
            "success": True,
            "restored_to": commit_hash[:8],
            "directory": normalized,
        }

        if file_path is not None:
            result["file"] = file_path

        return result

    def list_checkpoints(
            self,
            working_dir: str,
    ) -> list[dict[str, object]]:
        normalized = str(_normalize_path(working_dir))
        store = _store_path(self.checkpoint_base)

        if not (store / "HEAD").exists():
            return []

        ref = _ref_name(_project_hash(normalized))

        ok, output, _ = _run_git(
            [
                "log",
                ref,
                "--format=%H|%h|%aI|%s",
                "-n",
                str(self.max_snapshots),
            ],
            store,
            normalized,
            allowed_returncodes={128},
        )
        if not ok or not output:
            return []

        checkpoints: list[dict[str, object]] = []

        for line in output.splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue

            checkpoints.append(
                {
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "timestamp": parts[2],
                    "reason": parts[3],
                }
            )

        return checkpoints

    def _prune(
            self,
            store: Path,
            working_dir: str,
            ref: str,
    ) -> None:
        ok_count, count_output, _ = _run_git(
            ["rev-list", "--count", ref],
            store,
            working_dir,
            allowed_returncodes={128},
        )
        if not ok_count:
            return

        try:
            count = int(count_output)
        except ValueError:
            return

        if count <= self.max_snapshots:
            return

        ok_list, history, _ = _run_git(
            ["rev-list", "--reverse", ref],
            store,
            working_dir,
        )
        if not ok_list or not history:
            return
        # 取最新的 max_snapshots 个 checkpoint。
        keep = history.splitlines()[-self.max_snapshots:]
        new_parent: str | None = None
        # 保留每个 checkpoint 的 tree 和 reason。
        for commit_hash in keep:
            ok_tree, tree_hash, _ = _run_git(
                [
                    "rev-parse",
                    f"{commit_hash}^{{tree}}",
                ],
                store,
                working_dir,
            )
            ok_reason, reason, _ = _run_git(
                [
                    "log",
                    "--format=%s",
                    "-1",
                    commit_hash,
                ],
                store,
                working_dir,
            )

            if not ok_tree or not tree_hash:
                return

            args = [
                "commit-tree",
                tree_hash,
                "-m",
                reason if ok_reason and reason else "checkpoint",
                "--no-gpg-sign",
            ]

            if new_parent is not None:
                args[2:2] = ["-p", new_parent]
            # 重建一条较短的 commit 链
            ok_commit, rewritten, _ = _run_git(
                args,
                store,
                working_dir,
            )
            if not ok_commit or not rewritten:
                return

            new_parent = rewritten

        if new_parent is None:
            return
        # 将 workspace ref 指向新链
        _run_git(
            ["update-ref", ref, new_parent],
            store,
            working_dir,
        )
        # 清理不可达的旧对象
        _run_git(
            ["reflog", "expire", "--expire=now", "--all"],
            store,
            working_dir,
        )
        _run_git(
            ["gc", "--prune=now", "--quiet"],
            store,
            working_dir,
        )
        _repair_store_dirs(store)
