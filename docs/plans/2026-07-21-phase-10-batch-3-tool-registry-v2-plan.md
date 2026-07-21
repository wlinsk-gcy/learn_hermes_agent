# Phase 10 Batch 3 ToolRegistry v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 以兼容方式为当前 ToolRegistry 增加 toolset、check_fn、generation 和可用定义查询，为下一批最小 ToolExecutor 提供稳定元数据边界。

**Architecture:** 保留当前 `ToolEntry(description, parameters, handler)` 和直接执行链路，只追加带默认值的元数据及只读查询。模型定义通过 `get_definitions()` 执行 fail-closed 可用性过滤，`list_definitions()` 保留为兼容包装；本批不把可用性检查误作审批或执行权限。

**Tech Stack:** Python 3.11+、dataclasses、typing、logging、uv、当前 CLI 和轻量内联不变量验证。

---

## 执行约束

- 按 Task 1 到 Task 6 顺序执行，一次只交付一个小任务。
- Python 代码默认只在对话中给出，由用户手抄；除非用户再次明确要求直接修改。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不新增测试文件，以 `compileall`、CLI 和内联断言验证。
- 不实现 ToolExecutor、terminal、checkpoint、TTL 缓存、动态 schema、插件注册或并发 dispatch。
- 不自动提交；提交由用户决定。

### Task 1：增加 ToolEntry 元数据和 Registry generation

**Files:**

- Modify: `src/learn_hermes_agent/tools/registry.py`

**Step 1：增加可用性检查类型别名**

紧接 `ToolHandler` 增加：

```python
ToolAvailabilityCheck = Callable[[], bool]
```

**Step 2：为 ToolEntry 增加兼容字段**

在 `handler` 后增加：

```python
    toolset: str = "other"
    check_fn: ToolAvailabilityCheck | None = None
```

字段必须放在无默认值字段之后，保证 dataclass 构造合法，并保持现有注册代码兼容。

**Step 3：增加 generation 状态和只读属性**

将 Registry 初始化和注册更新为：

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def register(self, entry: ToolEntry, *, override: bool = False) -> None:
        if entry.name in self._tools and not override:
            raise ValueError(f"Tool already registered: {entry.name}")
        self._tools[entry.name] = entry
        self._generation += 1
```

**Step 4：运行轻量验证**

```powershell
@'
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

registry = ToolRegistry()
entry = ToolEntry(
    name="demo",
    description="demo",
    parameters={"type": "object"},
    handler=lambda arguments: arguments,
)

assert registry.generation == 0
assert entry.toolset == "other"
assert entry.check_fn is None

registry.register(entry)
assert registry.generation == 1

try:
    registry.register(entry)
except ValueError:
    pass
else:
    raise AssertionError("duplicate registration should fail")

assert registry.generation == 1
registry.register(entry, override=True)
assert registry.generation == 2
print("task-1-ok")
'@ | uv run python -
```

预期输出：`task-1-ok`。

### Task 2：实现可用工具定义过滤

**Files:**

- Modify: `src/learn_hermes_agent/tools/registry.py`

**Step 1：增加模块 logger**

```python
import logging
```

在类型别名之前增加：

```python
logger = logging.getLogger(__name__)
```

**Step 2：实现 get_definitions 并保留兼容入口**

用下面两个方法替换原 `list_definitions()`：

```python
    def get_definitions(
        self,
        tool_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        entries = self.entries()
        if tool_names is not None:
            entries = [entry for entry in entries if entry.name in tool_names]

        availability_cache: dict[int, bool] = {}
        definitions: list[dict[str, Any]] = []

        for entry in entries:
            check_fn = entry.check_fn
            if check_fn is not None:
                cache_key = id(check_fn)
                if cache_key not in availability_cache:
                    try:
                        availability_cache[cache_key] = bool(check_fn())
                    except Exception:
                        logger.warning(
                            "Tool availability check failed for %s",
                            entry.name,
                            exc_info=True,
                        )
                        availability_cache[cache_key] = False

                if not availability_cache[cache_key]:
                    continue

            definitions.append(entry.to_definition())

        return definitions

    def list_definitions(self) -> list[dict[str, Any]]:
        return self.get_definitions()
```

`availability_cache` 只活在一次调用中，因此不会把环境状态长期缓存。

**Step 3：运行轻量验证**

```powershell
@'
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

registry = ToolRegistry()
calls = 0

def available() -> bool:
    global calls
    calls += 1
    return True

def unavailable() -> bool:
    return False

def broken() -> bool:
    raise RuntimeError("probe failed")

def add(name: str, check_fn=None) -> None:
    registry.register(ToolEntry(
        name=name,
        description=name,
        parameters={"type": "object"},
        handler=lambda arguments: arguments,
        check_fn=check_fn,
    ))

add("a", available)
add("b", available)
add("off", unavailable)
add("broken", broken)
add("plain")

names = [item["function"]["name"] for item in registry.get_definitions()]
assert names == ["a", "b", "plain"]
assert calls == 1
assert registry.get("off").name == "off"
assert [item["function"]["name"] for item in registry.get_definitions({"a", "missing"})] == ["a"]
assert registry.list_definitions() == registry.get_definitions()
print("task-2-ok")
'@ | uv run python -
```

预期：会有一次 `broken` 的 warning，最后输出 `task-2-ok`。

### Task 3：增加 toolset 查询接口

**Files:**

- Modify: `src/learn_hermes_agent/tools/registry.py`

**Step 1：在 entries() 后增加三个查询方法**

```python
    def get_registered_toolset_names(self) -> list[str]:
        return sorted({entry.toolset for entry in self._tools.values()})

    def get_tool_names_for_toolset(self, toolset: str) -> list[str]:
        return sorted(
            entry.name
            for entry in self._tools.values()
            if entry.toolset == toolset
        )

    def get_toolset_for_tool(self, tool_name: str) -> str | None:
        entry = self._tools.get(tool_name)
        if entry is None:
            return None
        return entry.toolset
```

**Step 2：运行轻量验证**

```powershell
@'
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

registry = ToolRegistry()
for name, toolset in [("z", "file"), ("a", "file"), ("m", "memory")]:
    registry.register(ToolEntry(
        name=name,
        description=name,
        parameters={"type": "object"},
        handler=lambda arguments: arguments,
        toolset=toolset,
    ))

assert registry.get_registered_toolset_names() == ["file", "memory"]
assert registry.get_tool_names_for_toolset("file") == ["a", "z"]
assert registry.get_tool_names_for_toolset("missing") == []
assert registry.get_toolset_for_tool("m") == "memory"
assert registry.get_toolset_for_tool("missing") is None
print("task-3-ok")
'@ | uv run python -
```

预期输出：`task-3-ok`。

### Task 4：标注内置工具的 toolset

**Files:**

- Modify: `src/learn_hermes_agent/tools/file_tools.py`
- Modify: `src/learn_hermes_agent/tools/memory.py`
- Modify: `src/learn_hermes_agent/tools/skills.py`
- Read only: `src/learn_hermes_agent/tools/echo.py`

**Step 1：标注四个文件工具**

在 `read_file`、`write_file`、`patch`、`search_files` 的每个 `ToolEntry(...)` 中，紧接 `handler` 增加：

```python
            toolset="file",
```

**Step 2：标注 memory 工具**

```python
            toolset="memory",
```

**Step 3：标注两个 skills 工具**

在 `skills_list` 和 `skill_view` 的每个 `ToolEntry(...)` 中增加：

```python
            toolset="skills",
```

`echo` 不修改，让它使用默认 `other`。当前内置工具均不设置 `check_fn`。

**Step 4：验证分类**

```powershell
@'
from learn_hermes_agent.tools.registry import discover_builtin_tools

registry = discover_builtin_tools()
assert registry.get_registered_toolset_names() == ["file", "memory", "other", "skills"]
assert registry.get_tool_names_for_toolset("file") == ["patch", "read_file", "search_files", "write_file"]
assert registry.get_tool_names_for_toolset("memory") == ["memory"]
assert registry.get_tool_names_for_toolset("skills") == ["skill_view", "skills_list"]
assert registry.get_tool_names_for_toolset("other") == ["echo"]
assert all(entry.check_fn is None for entry in registry.entries())
print("task-4-ok")
'@ | uv run python -
```

预期输出：`task-4-ok`。

### Task 5：迁移模型定义调用方到主接口

**Files:**

- Modify: `src/learn_hermes_agent/model_tools.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

**Step 1：扩展 model_tools 包装入口**

将 `get_tool_definitions()` 改为：

```python
def get_tool_definitions(
    registry: ToolRegistry | None = None,
    *,
    tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    target = registry or get_default_registry()
    return target.get_definitions(tool_names)
```

保留 `registry` 的原位置参数兼容性，名称过滤只能通过关键字传入。

**Step 2：迁移 AIAgent**

将：

```python
tools = self.registry.list_definitions()
```

改为：

```python
tools = self.registry.get_definitions()
```

不要修改当前工具执行循环。模型调用范围校验属于 Batch 4。

**Step 3：运行 schema 一致性验证**

```powershell
@'
from learn_hermes_agent.model_tools import get_tool_definitions
from learn_hermes_agent.tools.registry import get_default_registry

registry = get_default_registry()
all_definitions = get_tool_definitions(registry)
file_definitions = get_tool_definitions(
    registry,
    tool_names=set(registry.get_tool_names_for_toolset("file")),
)

assert len(all_definitions) == len(registry.names())
assert [item["function"]["name"] for item in file_definitions] == [
    "patch",
    "read_file",
    "search_files",
    "write_file",
]
assert all(item["type"] == "function" for item in all_definitions)
print("task-5-ok")
'@ | uv run python -
```

预期输出：`task-5-ok`。

### Task 6：集中验证并更新交接文档

**Files:**

- Modify after validation: `docs/02-roadmap.md`
- Modify after validation: `docs/04-progress-handoff.md`
- Modify if needed: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Step 1：编译和 CLI 回归**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{"text":"registry-v2"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

预期：

- 所有命令退出码为 `0`。
- `tools` 仍显示八个内置工具。
- `call-tool echo` 返回 `{"text": "registry-v2"}`。
- tool demo 仍能完成一轮工具调用。

**Step 2：验证 Registry 总体不变量**

```powershell
@'
from learn_hermes_agent.tools.registry import discover_builtin_tools

registry = discover_builtin_tools()
assert registry.generation == len(registry.names()) == 8
assert registry.get_registered_toolset_names() == ["file", "memory", "other", "skills"]
assert len(registry.get_definitions()) == 8
assert registry.list_definitions() == registry.get_definitions()
print("batch-3-ok")
'@ | uv run python -
```

预期输出：`batch-3-ok`。

**Step 3：复核范围**

```powershell
git diff -- src/learn_hermes_agent/tools/registry.py src/learn_hermes_agent/tools/file_tools.py src/learn_hermes_agent/tools/memory.py src/learn_hermes_agent/tools/skills.py src/learn_hermes_agent/model_tools.py src/learn_hermes_agent/agent/core.py
git status --short
```

确认没有新增测试文件，没有实现 ToolExecutor、terminal 或 checkpoint。

**Step 4：更新项目状态**

验证全部通过后记录：

- Phase 10 Batch 3 ToolRegistry v2 已完成。
- Registry 已具备 toolset、check_fn、generation 和可用定义过滤。
- `check_fn` 不是审批或执行权限边界。
- 下一步为 Batch 4 最小 ToolExecutor。
- terminal 和 checkpoint 仍未实现。

