from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# 这里的Callable表示：ToolHandler 是一个可调用对象，它接收 一个参数，这个参数类型是 dict[str, Any]，返回值可以是任意类型。
ToolHandler = Callable[[dict[str, Any]], Any]
ToolAvailabilityCheck = Callable[[], bool]


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    toolset: str = "other"
    check_fn: ToolAvailabilityCheck | None = None

    def to_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        """
        新 Registry 的 generation 是 0。
        成功注册后加 1。
        重复注册抛错，不增加。
        成功覆盖也加 1。 -- “成功覆盖”指：注册一个与已有工具同名的新 ToolEntry，并用它替换旧的工具定义。
        property 只允许外部读取，不能直接执行 registry.generation = 10。
        """
        return self._generation

    def register(self, entry: ToolEntry, *, override: bool = False) -> None:
        if entry.name in self._tools and not override:
            raise ValueError(f"Tool already registered: {entry.name}")
        self._tools[entry.name] = entry
        self._generation += 1

    def get(self, name: str) -> ToolEntry:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def entries(self) -> list[ToolEntry]:
        return [self._tools[name] for name in self.names()]

    def list_definitions(self) -> list[dict[str, Any]]:
        return [entry.to_definition() for entry in self.entries()]


_default_registry: ToolRegistry | None = None


def discover_builtin_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    target = registry or ToolRegistry()

    from learn_hermes_agent.tools.echo import register_tools as register_echo_tools
    from learn_hermes_agent.tools.file_tools import register_tools as register_file_tools
    from learn_hermes_agent.tools.memory import register_tools as register_memory_tools
    from learn_hermes_agent.tools.skills import register_tools as register_skills_tools

    register_echo_tools(target)
    register_file_tools(target)
    register_memory_tools(target)
    register_skills_tools(target)
    return target


def get_default_registry() -> ToolRegistry:
    """保证默认工具注册表只初始化一次。"""
    global _default_registry

    if _default_registry is None:
        # Python 只要看到函数内部对某个名字赋值，就默认这个名字是局部变量。如果没有 global，Python 会把 _default_registry 当成函数内部变量。
        # 所以如果没有global，if _default_registry的判断就会出问题
        # 所以 global 是在告诉 Python：不要创建局部变量，我要读写模块级变量 _default_registry。
        _default_registry = discover_builtin_tools()

    return _default_registry
