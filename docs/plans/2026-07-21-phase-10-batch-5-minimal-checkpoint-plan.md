# Phase 10 Batch 5 Minimal Checkpoint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `write_file` 和 `patch` 增加默认关闭、Agent 透明持有、每 Provider/tool iteration 去重的共享 shadow Git checkpoint，并提供 Manager 级列举与恢复接口。

**Architecture:** 使用与最新版 Hermes 相同的单一 shadow Git object store，每个 workspace 拥有独立 ref 和 index。`AIAgent` 持有 `CheckpointManager`，ToolExecutor 通过 `model_tools` 的 post-preflight / pre-dispatch callback 在写工具真正 dispatch 前创建快照；checkpoint 失败 fail-open，安全 preflight 继续 fail-closed。

**Tech Stack:** Python 3.11+、`subprocess`、系统 Git、`pathlib`、当前 `AIAgent` / `ToolExecutionContext` / ToolExecutor / Registry、uv、内联不变量与 CLI 手工验证。

---

## 执行约束

- 按 Task 1 到 Task 8 顺序执行，一次只交付一个小任务。
- Python 代码默认只在对话中给出，由用户手抄；除非用户明确要求直接写入。
- 文档可由 Codex 直接维护。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不新增测试文件；使用 `compileall`、临时目录内联脚本、CLI 和源码范围检查。
- 不实现 `/rollback`、`checkpoints` CLI、terminal、并发 executor、checkpoint diff、全局容量限制、自动维护或 legacy migration。
- 不自动提交；每个 Task 验证后由用户决定是否提交。

### Task 1：增加 checkpoint 配置和存储路径

**Files:**

- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1：增加默认配置**

在 `DEFAULT_CONFIG` 中、`security` 前增加：

```python
    "checkpoints": {
        "enabled": False,
        "max_snapshots": 20,
    },
```

**Step 2：增加 checkpoint base helper**

在 `get_skills_dir_path()` 后增加：

```python
def get_checkpoints_dir_path() -> Path:
    return get_app_home() / "checkpoints"
```

**Step 3：规范化配置**

在 `_normalize_config()` 中，写回 `security` 前增加：

```python
    checkpoints_config = config.get("checkpoints")
    if isinstance(checkpoints_config, bool):
        checkpoints_config = {"enabled": checkpoints_config}
    elif not isinstance(checkpoints_config, dict):
        checkpoints_config = {}

    default_checkpoints_config = DEFAULT_CONFIG["checkpoints"]

    checkpoints_enabled = checkpoints_config.get("enabled")
    if not isinstance(checkpoints_enabled, bool):
        checkpoints_enabled = default_checkpoints_config["enabled"]

    max_snapshots = checkpoints_config.get("max_snapshots")
    if (
        isinstance(max_snapshots, bool)
        or not isinstance(max_snapshots, int)
        or max_snapshots <= 0
    ):
        max_snapshots = default_checkpoints_config["max_snapshots"]

    config["checkpoints"] = {
        "enabled": checkpoints_enabled,
        "max_snapshots": max_snapshots,
    }
```

**Step 4：doctor 输出配置**

在 `run_doctor()` 中输出：

```python
    checkpoints = config["checkpoints"]
    print(f"checkpoints_enabled: {checkpoints['enabled']}")
    print(f"checkpoint_max_snapshots: {checkpoints['max_snapshots']}")
```

并把 `get_checkpoints_dir_path` 加入 `cli/main.py` 的 config imports，再输出：

```python
    print(f"checkpoints: {get_checkpoints_dir_path()}")
```

**Step 5：运行轻量验证**

```powershell
@'
import copy

from learn_hermes_agent.config import DEFAULT_CONFIG, _normalize_config

default = _normalize_config(copy.deepcopy(DEFAULT_CONFIG))
assert default["checkpoints"] == {
    "enabled": False,
    "max_snapshots": 20,
}

enabled = _normalize_config({"checkpoints": True})
assert enabled["checkpoints"]["enabled"] is True
assert enabled["checkpoints"]["max_snapshots"] == 20

invalid = _normalize_config({
    "checkpoints": {
        "enabled": "yes",
        "max_snapshots": True,
    },
})
assert invalid["checkpoints"] == {
    "enabled": False,
    "max_snapshots": 20,
}

print("task-1-ok")
'@ | uv run python -

uv run learn-hermes-agent doctor
```

预期：内联脚本输出 `task-1-ok`；doctor 显示 checkpoint base、`enabled: False` 和 `max_snapshots: 20`。

### Task 2：创建共享 shadow Git store 基础层

**Files:**

- Create: `src/learn_hermes_agent/tools/checkpoint_manager.py`

**Step 1：创建模块、常量和路径 helper**

```python
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
_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")

DEFAULT_EXCLUDES = [
    ".git/",
    ".learn_hermes/",
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
    normalized = str(_normalize_path(working_dir))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _store_path(base: Path) -> Path:
    return base / _STORE_DIRNAME


def _index_path(store: Path, project_hash: str) -> Path:
    return store / _INDEXES_DIRNAME / project_hash


def _ref_name(project_hash: str) -> str:
    return f"{_REFS_PREFIX}/{project_hash}"


def _validate_commit_hash(commit_hash: str) -> str | None:
    if not commit_hash or not commit_hash.strip():
        return "Empty commit hash"
    if commit_hash.startswith("-"):
        return "Invalid commit hash: value must not start with '-'"
    if not _COMMIT_HASH_RE.fullmatch(commit_hash):
        return "Invalid commit hash: expected 4-64 hexadecimal characters"
    return None
```

**Step 2：增加隔离的 Git 环境和执行 helper**

```python
def _git_env(
    store: Path,
    working_dir: str,
    *,
    index_file: Path | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_DIR"] = str(store)
    env["GIT_WORK_TREE"] = str(_normalize_path(working_dir))
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.pop("GIT_NAMESPACE", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
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
    allowed_returncodes: set[int] | None = None,
) -> tuple[bool, str, str]:
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
            env=_git_env(store, str(worktree), index_file=index_file),
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("checkpoint git command failed: %s", exc)
        return False, "", str(exc)

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0 and result.returncode not in allowed:
        logger.debug(
            "checkpoint git command failed: git %s: %s",
            " ".join(args),
            stderr,
        )
    return result.returncode == 0, stdout, stderr
```

**Step 3：初始化共享 bare store**

```python
def _init_store(store: Path, working_dir: str) -> str | None:
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
        return f"checkpoint store initialization failed: {result.stderr.strip()}"

    _run_git(["config", "user.email", "learn-hermes@local"], store, working_dir)
    _run_git(["config", "user.name", "Learn Hermes Checkpoint"], store, working_dir)
    _run_git(["config", "commit.gpgsign", "false"], store, working_dir)
    _run_git(["config", "tag.gpgSign", "false"], store, working_dir)
    _run_git(["config", "gc.auto", "0"], store, working_dir)

    info_dir = store / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "exclude").write_text(
        "\n".join(DEFAULT_EXCLUDES) + "\n",
        encoding="utf-8",
    )
    return None


def _repair_store_dirs(store: Path) -> None:
    for relative in ("refs/heads", "branches"):
        (store / relative).mkdir(parents=True, exist_ok=True)
```

**Step 4：运行基础验证**

```powershell
@'
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.tools.checkpoint_manager import (
    _init_store,
    _project_hash,
    _store_path,
)

with TemporaryDirectory() as raw:
    root = Path(raw)
    work = root / "work"
    base = root / "checkpoints"
    work.mkdir()
    store = _store_path(base)

    assert _init_store(store, str(work)) is None
    assert (store / "HEAD").exists()
    assert (store / "info" / "exclude").exists()
    assert not (work / ".git").exists()
    assert _project_hash(str(work)) == _project_hash(str(work.resolve()))

print("task-2-ok")
'@ | uv run python -
```

预期输出：`task-2-ok`。

### Task 3：实现快照、去重、列举和数量限制

**Files:**

- Modify: `src/learn_hermes_agent/tools/checkpoint_manager.py`

**Step 1：增加 CheckpointManager 主结构**

```python
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
        self._checkpointed_dirs.clear()

    def ensure_checkpoint(
        self,
        working_dir: str,
        reason: str = "auto",
    ) -> bool:
        if not self.enabled:
            return False

        if self._git_available is None:
            self._git_available = shutil.which("git") is not None
        if not self._git_available:
            logger.debug("checkpoints disabled: git executable not found")
            return False

        path = _normalize_path(working_dir)
        filesystem_root = Path(path.anchor).resolve()
        if path in {filesystem_root, Path.home().resolve()}:
            logger.debug("checkpoint skipped for overly broad path: %s", path)
            return False

        normalized = str(path)
        if normalized in self._checkpointed_dirs:
            return False

        self._checkpointed_dirs.add(normalized)
        try:
            return self._take(normalized, reason)
        except Exception as exc:
            logger.debug("checkpoint failed (non-fatal): %s", exc)
            return False
```

**Step 2：实现 `_take()`**

把下面的方法放入类中：

```python
    def _take(
        self,
        working_dir: str,
        reason: str,
        *,
        prune: bool = True,
    ) -> bool:
        store = _store_path(self.checkpoint_base)
        init_error = _init_store(store, working_dir)
        if init_error is not None:
            logger.debug(init_error)
            return False

        project_hash = _project_hash(working_dir)
        index_file = _index_path(store, project_hash)
        ref = _ref_name(project_hash)
        index_file.parent.mkdir(parents=True, exist_ok=True)

        ok_ref, ref_commit, _ = _run_git(
            ["rev-parse", "--verify", f"{ref}^{{commit}}"],
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

        ok_add, _, add_error = _run_git(
            ["add", "-A"],
            store,
            working_dir,
            index_file=index_file,
        )
        if not ok_add:
            logger.debug("checkpoint git add failed: %s", add_error)
            return False

        if has_ref:
            no_changes, _, _ = _run_git(
                ["diff-index", "--cached", "--quiet", ref_commit],
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

        ok_tree, tree_hash, tree_error = _run_git(
            ["write-tree"],
            store,
            working_dir,
            index_file=index_file,
        )
        if not ok_tree or not tree_hash:
            logger.debug("checkpoint write-tree failed: %s", tree_error)
            return False

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
            logger.debug("checkpoint commit-tree failed: %s", commit_error)
            return False

        update_args = ["update-ref", ref, commit_hash]
        if has_ref:
            update_args.append(ref_commit)

        ok_update, _, update_error = _run_git(
            update_args,
            store,
            working_dir,
        )
        if not ok_update:
            logger.debug("checkpoint update-ref failed: %s", update_error)
            return False

        if prune:
            self._prune(store, working_dir, ref)
        return True
```

注意 `commit_args[2:2] = ["-p", ref_commit]` 会把 parent 放到 tree hash 后、`-m` 前，得到：

```text
git commit-tree TREE -p PARENT -m REASON --no-gpg-sign
```

**Step 3：实现列举和真实数量限制**

```python
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

    def _prune(self, store: Path, working_dir: str, ref: str) -> None:
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

        keep = history.splitlines()[-self.max_snapshots:]
        new_parent: str | None = None

        for commit_hash in keep:
            ok_tree, tree_hash, _ = _run_git(
                ["rev-parse", f"{commit_hash}^{{tree}}"],
                store,
                working_dir,
            )
            ok_reason, reason, _ = _run_git(
                ["log", "--format=%s", "-1", commit_hash],
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

        _run_git(["update-ref", ref, new_parent], store, working_dir)
        _run_git(["reflog", "expire", "--expire=now", "--all"], store, working_dir)
        _run_git(["gc", "--prune=now", "--quiet"], store, working_dir)
        _repair_store_dirs(store)
```

**Step 4：运行快照不变量验证**

```powershell
@'
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.tools.checkpoint_manager import CheckpointManager

with TemporaryDirectory() as raw:
    root = Path(raw)
    work = root / "work"
    work.mkdir()
    target = work / "main.py"
    target.write_text("v1\n", encoding="utf-8")

    manager = CheckpointManager(
        enabled=True,
        max_snapshots=2,
        checkpoint_base=root / "checkpoints",
    )

    assert manager.ensure_checkpoint(str(work), "v1") is True
    assert manager.ensure_checkpoint(str(work), "same turn") is False

    for version in ("v2", "v3", "v4"):
        target.write_text(version + "\n", encoding="utf-8")
        manager.new_turn()
        assert manager.ensure_checkpoint(str(work), version) is True

    checkpoints = manager.list_checkpoints(str(work))
    assert len(checkpoints) == 2, checkpoints
    assert [item["reason"] for item in checkpoints] == ["v4", "v3"]
    assert not (work / ".git").exists()

print("task-3-ok")
'@ | uv run python -
```

预期输出：`task-3-ok`。

### Task 4：增加工作目录解析和恢复

**Files:**

- Modify: `src/learn_hermes_agent/tools/checkpoint_manager.py`

**Step 1：增加 restore file path 校验 helper**

放在类定义前：

```python
def _validate_file_path(file_path: str, working_dir: str) -> str | None:
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
```

**Step 2：增加受 workspace boundary 限制的项目根发现**

放入 `CheckpointManager`：

```python
    def get_working_dir_for_path(
        self,
        file_path: str,
        *,
        boundary: str | None = None,
    ) -> str:
        path = _normalize_path(file_path)
        candidate = path if path.is_dir() else path.parent
        boundary_path = _normalize_path(boundary) if boundary else None

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
            if any((current / marker).exists() for marker in markers):
                return str(current)
            if boundary_path is not None and current == boundary_path:
                return str(boundary_path)
            if current == current.parent:
                break
            parent = current.parent
            if boundary_path is not None:
                try:
                    parent.relative_to(boundary_path)
                except ValueError:
                    return str(boundary_path)
            current = parent

        return str(boundary_path or candidate)
```

**Step 3：实现 restore**

放入 `CheckpointManager`：

```python
    def restore(
        self,
        working_dir: str,
        commit_hash: str,
        file_path: str | None = None,
    ) -> dict[str, object]:
        hash_error = _validate_commit_hash(commit_hash)
        if hash_error is not None:
            return {"success": False, "error": hash_error}

        normalized = str(_normalize_path(working_dir))
        if file_path is not None:
            path_error = _validate_file_path(file_path, normalized)
            if path_error is not None:
                return {"success": False, "error": path_error}

        store = _store_path(self.checkpoint_base)
        if not (store / "HEAD").exists():
            return {
                "success": False,
                "error": "No checkpoints exist for this directory",
            }

        ref = _ref_name(_project_hash(normalized))
        belongs, _, _ = _run_git(
            ["merge-base", "--is-ancestor", commit_hash, ref],
            store,
            normalized,
            allowed_returncodes={1, 128},
        )
        if not belongs:
            return {
                "success": False,
                "error": "Checkpoint does not belong to this directory",
            }

        self._take(
            normalized,
            f"pre-rollback snapshot (restoring to {commit_hash[:8]})",
            prune=False,
        )

        project_hash = _project_hash(normalized)
        index_file = _index_path(store, project_hash)
        restore_target = Path(file_path).as_posix() if file_path else "."

        ok, _, error = _run_git(
            ["checkout", commit_hash, "--", restore_target],
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
```

本批 restore 的验收对象是 checkpoint 中已存在的文本文件。恢复 checkpoint 后新建、且 checkpoint 中不存在的文件删除语义延后到 rollback UX 设计，不在本批使用 `git clean`。

**Step 4：运行路径和恢复验证**

```powershell
@'
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.tools.checkpoint_manager import CheckpointManager

with TemporaryDirectory() as raw:
    root = Path(raw)
    workspace = root / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    (workspace / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    target = source / "main.py"
    target.write_text("before\n", encoding="utf-8")

    manager = CheckpointManager(
        enabled=True,
        checkpoint_base=root / "checkpoints",
    )
    discovered = manager.get_working_dir_for_path(
        str(target),
        boundary=str(workspace),
    )
    assert discovered == str(workspace.resolve())

    assert manager.ensure_checkpoint(discovered, "before patch") is True
    checkpoint = manager.list_checkpoints(discovered)[0]
    target.write_text("after\n", encoding="utf-8")

    result = manager.restore(
        discovered,
        str(checkpoint["hash"]),
        file_path="src/main.py",
    )
    assert result["success"] is True, result
    assert target.read_text(encoding="utf-8") == "before\n"
    assert "pre-rollback" in manager.list_checkpoints(discovered)[0]["reason"]

    invalid = manager.restore(discovered, "--patch")
    assert invalid["success"] is False

print("task-4-ok")
'@ | uv run python -
```

预期输出：`task-4-ok`。

### Task 5：让 AIAgent 持有 Manager 并建立 iteration 生命周期

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1：AIAgent 创建 manager**

在 `agent/core.py` 导入：

```python
from learn_hermes_agent.tools.checkpoint_manager import CheckpointManager
```

给 `AIAgent.__init__()` 增加 keyword-only 参数：

```python
checkpoints_enabled: bool = False,
checkpoint_max_snapshots: int = 20,
```

在 Registry 状态附近创建：

```python
        self._checkpoint_mgr = CheckpointManager(
            enabled=checkpoints_enabled,
            max_snapshots=checkpoint_max_snapshots,
        )
```

**Step 2：每个 Provider/tool iteration 重置去重**

在：

```python
while self.iteration_budget.consume():
```

紧接着增加：

```python
            self._checkpoint_mgr.new_turn()
```

这里的 `turn` 与最新版 Hermes 当前实际调用位置一致，表示一次 Provider/tool iteration，而不是整个用户输入的所有后续循环。

**Step 3：CLI build_agent 传入配置**

在 `build_agent()` 中增加：

```python
        checkpoints_enabled=config["checkpoints"]["enabled"],
        checkpoint_max_snapshots=config["checkpoints"]["max_snapshots"],
```

**Step 4：运行生命周期验证**

```powershell
@'
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.providers.fake import FakeProviderTransport

agent = AIAgent(FakeProviderTransport(), checkpoints_enabled=False)
assert agent._checkpoint_mgr.enabled is False
assert agent._checkpoint_mgr.max_snapshots == 20

agent._checkpoint_mgr._checkpointed_dirs.add("sentinel")
agent._checkpoint_mgr.new_turn()
assert agent._checkpoint_mgr._checkpointed_dirs == set()

print("task-5-ok")
'@ | uv run python -

uv run python -m compileall -q src
```

预期：输出 `task-5-ok`；compileall 退出码为 0。

### Task 6：在安全 preflight 后接入自动 checkpoint

**Files:**

- Modify: `src/learn_hermes_agent/model_tools.py`
- Modify: `src/learn_hermes_agent/agent/tool_executor.py`

**Step 1：给 model_tools 增加 callback 类型**

调整 imports：

```python
import logging
from collections.abc import Callable
```

模块级增加：

```python
logger = logging.getLogger(__name__)

BeforeDispatch = Callable[
    [str, dict[str, Any], ToolExecutionContext | None],
    None,
]
```

**Step 2：在 preflight 通过后调用 best-effort callback**

给 `handle_function_call()` 增加 keyword-only 参数：

```python
before_dispatch: BeforeDispatch | None = None,
```

在：

```python
    if preflight_error is not None:
        return json.dumps(preflight_error, ensure_ascii=False)
```

之后、`entry.handler(arguments)` 之前增加：

```python
    if before_dispatch is not None:
        try:
            before_dispatch(name, arguments, context)
        except Exception as exc:
            logger.debug(
                "before-dispatch callback failed (non-fatal): %s",
                exc,
                exc_info=True,
            )
```

给 `safe_handle_function_call()` 增加同名 keyword-only 参数，并转发：

```python
        return handle_function_call(
            name,
            arguments_json,
            registry=registry,
            context=context,
            before_dispatch=before_dispatch,
        )
```

callback 默认 `None`，所以 CLI 和既有调用方不变。

**Step 3：ToolExecutor 增加 checkpoint helper**

在 `agent/tool_executor.py` 增加 import：

```python
from learn_hermes_agent.agent.file_safety import resolve_workspace_path
```

在 `_parse_tool_arguments()` 前增加：

```python
def _ensure_file_checkpoint(
    agent: AIAgent,
    function_name: str,
    function_args: dict[str, Any],
    tool_context: ToolExecutionContext | None,
) -> None:
    if function_name not in {"write_file", "patch"}:
        return
    if tool_context is None or not agent._checkpoint_mgr.enabled:
        return

    file_path = function_args.get("path")
    if not isinstance(file_path, str) or not file_path.strip():
        return

    resolved_path = resolve_workspace_path(file_path, tool_context)
    working_dir = agent._checkpoint_mgr.get_working_dir_for_path(
        str(resolved_path),
        boundary=str(tool_context.workspace_root),
    )
    agent._checkpoint_mgr.ensure_checkpoint(
        working_dir,
        f"before {function_name}",
    )
```

**Step 4：顺序执行时传入 callback**

把现有 `safe_handle_function_call(...)` 调用改为：

```python
                result_json = safe_handle_function_call(
                    function_name,
                    function_args,
                    registry=agent.registry,
                    context=tool_context,
                    before_dispatch=(
                        lambda name, args, context: _ensure_file_checkpoint(
                            agent,
                            name,
                            args,
                            context,
                        )
                    ),
                )
```

lambda 只负责把当前 agent 绑定进 callback。是否属于写工具、是否启用和路径解析都由 `_ensure_file_checkpoint()` 判断。

**Step 5：验证 block ordering、路径和 fail-open**

```powershell
@'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.providers.fake import FakeProviderTransport
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall
from learn_hermes_agent.tools.checkpoint_manager import CheckpointManager
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

with TemporaryDirectory() as raw:
    root = Path(raw)
    process_cwd = root / "process"
    workspace = root / "workspace"
    process_cwd.mkdir()
    workspace.mkdir()
    (process_cwd / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (workspace / "existing.txt").write_text("before\n", encoding="utf-8")

    executed = []
    def write_handler(arguments):
        executed.append(arguments["path"])
        return {"path": arguments["path"]}

    registry = ToolRegistry()
    registry.register(ToolEntry(
        name="write_file",
        description="write",
        parameters={"type": "object"},
        handler=write_handler,
        toolset="file",
    ))

    provider = FakeProviderTransport(scripted_responses=[
        NormalizedResponse(
            content=None,
            tool_calls=[
                ToolCall(id="blocked", name="write_file", arguments='{"path":".env"}'),
                ToolCall(id="allowed", name="write_file", arguments='{"path":"existing.txt"}'),
            ],
            finish_reason="tool_calls",
        ),
        NormalizedResponse(content="done", tool_calls=None, finish_reason="stop"),
    ])

    agent = AIAgent(provider, registry=registry)
    agent._checkpoint_mgr = CheckpointManager(
        enabled=True,
        checkpoint_base=root / "checkpoints",
    )
    context = ToolExecutionContext(
        cwd=workspace,
        workspace_root=workspace,
    )
    messages = agent.run_conversation("write", tool_context=context)

    assert executed == ["existing.txt"]
    checkpoints = agent._checkpoint_mgr.list_checkpoints(str(workspace))
    assert len(checkpoints) == 1, checkpoints
    assert checkpoints[0]["reason"] == "before write_file"
    assert agent._checkpoint_mgr.list_checkpoints(str(process_cwd)) == []

    tool_results = [message for message in messages if message["role"] == "tool"]
    assert len(tool_results) == 2
    assert json.loads(tool_results[0]["content"])["allowed"] is False
    assert json.loads(tool_results[1]["content"])["path"] == "existing.txt"

    called = []
    def failing_checkpoint(*args, **kwargs):
        raise RuntimeError("checkpoint unavailable")

    agent._checkpoint_mgr.ensure_checkpoint = failing_checkpoint
    agent._checkpoint_mgr.new_turn()
    result = agent._checkpoint_mgr.enabled
    assert result is True

print("task-6-ok")
'@ | uv run python -
```

上面主要验证 block ordering 和正确 workspace。另运行一个直接 callback fail-open 小断言：

```powershell
@'
import json

from learn_hermes_agent.model_tools import handle_function_call
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

calls = []
registry = ToolRegistry()
registry.register(ToolEntry(
    name="visible",
    description="visible",
    parameters={"type": "object"},
    handler=lambda arguments: calls.append(arguments) or {"ok": True},
))

def fail(*args):
    raise RuntimeError("checkpoint unavailable")

result = json.loads(handle_function_call(
    "visible",
    {},
    registry=registry,
    before_dispatch=fail,
))
assert result == {"ok": True}
assert calls == [{}]
print("task-6-fail-open-ok")
'@ | uv run python -
```

预期输出：`task-6-ok` 和 `task-6-fail-open-ok`。

### Task 7：集中回归验证

**Files:**

- Read: `src/learn_hermes_agent/tools/checkpoint_manager.py`
- Read: `src/learn_hermes_agent/agent/tool_executor.py`
- Read: `src/learn_hermes_agent/model_tools.py`
- Read: `src/learn_hermes_agent/agent/core.py`
- Read: `src/learn_hermes_agent/config.py`

**Step 1：编译和 CLI 回归**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent doctor
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"checkpoint\"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

预期：

- 所有命令退出码为 0。
- doctor 显示 checkpoint 默认关闭。
- `tools` 仍只有八个内置工具，不包含 checkpoint。
- echo 和完整 tool demo 消息链保持不变。

**Step 2：安全 preflight 回归**

```powershell
@'
import json

from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.model_tools import handle_function_call

result = json.loads(handle_function_call(
    "write_file",
    {"path": ".env", "content": "secret"},
    context=ToolExecutionContext(),
))
assert result["allowed"] is False
assert result["pattern_key"] == "sensitive_name"
print("preflight-ok")
'@ | uv run python -
```

预期输出：`preflight-ok`。

**Step 3：复核源码范围**

```powershell
rg -n "CheckpointManager|new_turn|before_dispatch|_ensure_file_checkpoint|terminal|rollback|checkpoint" src/learn_hermes_agent
git diff --check
git status --short
```

确认：

- checkpoint manager 不是 Registry entry。
- 只接入 `write_file` 和 `patch`。
- 没有 terminal、rollback CLI、并发 executor 或新测试文件。
- shadow store 永远不使用项目自身 `.git`。
- `sandbox/` 等既有无关文件不处理。

### Task 8：更新路线与交接文档

**Files:**

- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`
- Modify: `docs/plans/2026-07-21-phase-10-post-file-tools-route-design.md`

验证全部通过后记录：

- Phase 10 Batch 5 Minimal Checkpoint 已完成。
- 对齐 Hermes HEAD `477c08b44` 和 checkpoint path fix `d7b36070e`。
- AIAgent 透明持有 manager；checkpoint 不向 LLM 暴露。
- 使用共享 shadow Git object store、per-workspace ref/index。
- `new_turn()` 按 Provider/tool iteration 重置去重。
- `write_file` / `patch` 在安全 preflight 通过后、handler 前创建 checkpoint。
- checkpoint fail-open，安全 preflight fail-closed。
- 默认关闭；无 `/rollback`、checkpoint CLI、terminal 或聊天记录回滚。
- 下一步是 Phase 10 Batch 6 Local Foreground Terminal，但开始前必须重新对齐最新版 Hermes terminal/approval/backend 源码并单独完成设计。

运行：

```powershell
rg -n "Batch 5|Minimal Checkpoint|CheckpointManager|shadow Git|Batch 6|terminal|rollback" docs/00-overview.md docs/02-roadmap.md docs/04-progress-handoff.md docs/plans
git diff --check
git status --short
```

预期：状态一致指向 Batch 6；实现范围仍不包含 terminal 和 rollback UX。
