# Phase 10 Batch 4 Minimal ToolExecutor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按最新版 Hermes 的模块级执行器结构，提取最小顺序 tool-call 编排，关闭模型调用未暴露工具的范围缺口。

**Architecture:** 新增 `agent/tool_executor.py`，使用接收 AIAgent 的模块级 `execute_tool_calls_sequential()`；AIAgent 从实际发送给 Provider 的 definitions 形成 `valid_tool_names`，executor 负责名称阻断、模型参数解析、顺序调用现有安全分发入口和追加 tool result。`model_tools` 继续负责 Registry lookup、安全 preflight、handler dispatch 和 CLI 兼容。

**Tech Stack:** Python 3.11+、typing、json、当前 ToolRegistry、AIAgent、FakeProviderTransport、uv 和内联不变量验证。

---

## 执行约束

- 按 Task 1 到 Task 5 顺序执行，一次只交付一个小任务。
- Python 代码默认只在对话中给出，由用户手抄；除非用户再次明确要求直接修改。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不新增测试文件，以 `compileall`、CLI、FakeProvider 和内联断言验证。
- 不实现 executor 类、并发、segmented execution、middleware、guardrails、checkpoint 或 terminal。
- 不移动 `model_tools` 中现有 Registry dispatch 和安全 preflight。
- 不自动提交；提交由用户决定。

### Task 1：创建模型工具参数解析 helper

**Files:**

- Create: `src/learn_hermes_agent/agent/tool_executor.py`

**Step 1：创建模块和导入**

```python
from __future__ import annotations

import json
from typing import Any
```

**Step 2：实现 Hermes 风格参数解析**

```python
def _parse_tool_arguments(
    raw_arguments: Any,
) -> tuple[dict[str, Any], str | None]:
    if raw_arguments is None:
        return {}, None

    if isinstance(raw_arguments, dict):
        return raw_arguments, None

    if isinstance(raw_arguments, str) and not raw_arguments.strip():
        return {}, None

    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        arguments = None

    if isinstance(arguments, dict):
        return arguments, None

    return {}, json.dumps(
        {
            "error": "Invalid tool arguments",
            "message": (
                "Tool arguments must be a valid JSON object; "
                "tool was not executed."
            ),
        },
        ensure_ascii=False,
    )
```

该 helper 不修复、不猜测参数。只有 JSON object 可以进入实际分发。

**Step 3：运行轻量验证**

```powershell
@'
import json

from learn_hermes_agent.agent.tool_executor import _parse_tool_arguments

for raw in (None, "", "   ", {}):
    arguments, error = _parse_tool_arguments(raw)
    assert arguments == {}
    assert error is None

arguments, error = _parse_tool_arguments('{"text":"ok"}')
assert arguments == {"text": "ok"}
assert error is None

for raw in ("not-json", "[]", '"text"', "1", []):
    arguments, error = _parse_tool_arguments(raw)
    assert arguments == {}
    payload = json.loads(error)
    assert payload["error"] == "Invalid tool arguments"

print("task-1-ok")
'@ | uv run python -
```

预期输出：`task-1-ok`。

### Task 2：实现模块级顺序执行器

**Files:**

- Modify: `src/learn_hermes_agent/agent/tool_executor.py`

**Step 1：增加执行依赖和仅类型检查导入**

将 typing 导入改为：

```python
from typing import TYPE_CHECKING, Any
```

增加：

```python
from learn_hermes_agent.agent.messages import ChatMessage, tool_message
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.model_tools import safe_handle_function_call

if TYPE_CHECKING:
    from learn_hermes_agent.agent.core import AIAgent
```

`TYPE_CHECKING` 避免运行时形成 `core -> tool_executor -> core` 循环导入。

**Step 2：实现顺序执行函数**

在 `_parse_tool_arguments()` 后增加：

```python
def execute_tool_calls_sequential(
    agent: AIAgent,
    assistant_message: ChatMessage,
    messages: list[ChatMessage],
    *,
    tool_context: ToolExecutionContext | None = None,
) -> None:
    for tool_call in agent._get_tool_calls(assistant_message):
        function_name, raw_arguments, tool_call_id = agent._parse_tool_call(
            tool_call
        )

        if function_name not in agent.valid_tool_names:
            result_json = json.dumps(
                {
                    "error": (
                        f"Tool '{function_name}' is not available "
                        "in this turn."
                    )
                },
                ensure_ascii=False,
            )
        else:
            function_args, argument_error = _parse_tool_arguments(
                raw_arguments
            )
            if argument_error is not None:
                result_json = argument_error
            else:
                result_json = safe_handle_function_call(
                    function_name,
                    function_args,
                    registry=agent.registry,
                    context=tool_context,
                )

        messages.append(
            tool_message(
                name=function_name,
                content=result_json,
                tool_call_id=tool_call_id,
            )
        )
```

范围检查必须使用 `not in`，不能写成基于集合 truthy/falsy 的可选判断；空集合表示没有工具可以执行。

**Step 3：运行执行器不变量验证**

```powershell
@'
import json

from learn_hermes_agent.agent.tool_executor import execute_tool_calls_sequential
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

calls = []

def visible(arguments):
    calls.append(arguments["value"])
    return {"value": arguments["value"]}

def hidden(arguments):
    raise AssertionError("hidden handler must not run")

registry = ToolRegistry()
registry.register(ToolEntry(
    name="visible",
    description="visible",
    parameters={"type": "object"},
    handler=visible,
))
registry.register(ToolEntry(
    name="hidden",
    description="hidden",
    parameters={"type": "object"},
    handler=hidden,
))

class StubAgent:
    def __init__(self):
        self.registry = registry
        self.valid_tool_names = {"visible"}

    def _get_tool_calls(self, message):
        return message.get("tool_calls", [])

    def _parse_tool_call(self, tool_call):
        function = tool_call["function"]
        return (
            function["name"],
            function.get("arguments", "{}"),
            tool_call["id"],
        )

assistant_message = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call-hidden",
            "type": "function",
            "function": {"name": "hidden", "arguments": "{}"},
        },
        {
            "id": "call-invalid",
            "type": "function",
            "function": {"name": "visible", "arguments": "[]"},
        },
        {
            "id": "call-visible",
            "type": "function",
            "function": {
                "name": "visible",
                "arguments": '{"value":"ok"}',
            },
        },
    ],
}

messages = []
execute_tool_calls_sequential(StubAgent(), assistant_message, messages)

assert calls == ["ok"]
assert len(messages) == 3
assert all(message["role"] == "tool" for message in messages)
assert [message["tool_call_id"] for message in messages] == [
    "call-hidden",
    "call-invalid",
    "call-visible",
]
assert "not available" in json.loads(messages[0]["content"])["error"]
assert json.loads(messages[1]["content"])["error"] == "Invalid tool arguments"
assert json.loads(messages[2]["content"]) == {"value": "ok"}
print("task-2-ok")
'@ | uv run python -
```

预期输出：`task-2-ok`。

### Task 3：迁移 AIAgent 到顺序执行器

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`

**Step 1：调整导入**

从 messages 导入中移除 `tool_message`。

删除：

```python
from learn_hermes_agent.model_tools import safe_handle_function_call
```

增加：

```python
from learn_hermes_agent.agent.tool_executor import execute_tool_calls_sequential
```

**Step 2：增加 valid_tool_names 状态**

在 `AIAgent.__init__()` 设置 Registry 后增加：

```python
self.valid_tool_names: set[str] = set()
```

**Step 3：从同一批 definitions 生成名称快照**

紧接：

```python
tools = self.registry.get_definitions()
```

增加：

```python
self.valid_tool_names = {
    definition["function"]["name"]
    for definition in tools
}
```

不要调用 `registry.names()`，也不要第二次调用 `get_definitions()`。

**Step 4：用执行器替换 AIAgent 内部 for-loop**

保留：

```python
tool_calls = self._get_tool_calls(assistant_response)
if not tool_calls:
    return messages
```

删除后面的逐工具 for-loop，替换为：

```python
execute_tool_calls_sequential(
    self,
    assistant_response,
    messages,
    tool_context=tool_context,
)
```

**Step 5：验证隐藏工具不能被模型调用**

```powershell
@'
import json

from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.providers.fake import FakeProviderTransport
from learn_hermes_agent.providers.types import NormalizedResponse, ToolCall
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

executed = []

def hidden(arguments):
    executed.append(arguments)
    return {"executed": True}

registry = ToolRegistry()
registry.register(ToolEntry(
    name="hidden",
    description="hidden",
    parameters={"type": "object"},
    handler=hidden,
    check_fn=lambda: False,
))

provider = FakeProviderTransport(scripted_responses=[
    NormalizedResponse(
        content=None,
        tool_calls=[ToolCall(
            id="call-hidden",
            name="hidden",
            arguments="{}",
        )],
        finish_reason="tool_calls",
    ),
    NormalizedResponse(
        content="recovered",
        tool_calls=None,
        finish_reason="stop",
    ),
])

agent = AIAgent(provider, registry=registry)
messages = agent.run_conversation("try hidden")
tool_results = [message for message in messages if message["role"] == "tool"]

assert executed == []
assert agent.valid_tool_names == set()
assert len(tool_results) == 1
assert "not available" in json.loads(tool_results[0]["content"])["error"]
assert messages[-1]["content"] == "recovered"
print("task-3-ok")
'@ | uv run python -
```

预期输出：`task-3-ok`。

### Task 4：运行集中回归验证

**Files:**

- Read: `src/learn_hermes_agent/agent/tool_executor.py`
- Read: `src/learn_hermes_agent/agent/core.py`
- Read: `src/learn_hermes_agent/model_tools.py`

**Step 1：编译和正常 CLI 回归**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent call-tool echo '{\"text\":\"tool-executor\"}'
uv run learn-hermes-agent chat --tool-demo --show-messages "please use a tool"
```

预期：

- 所有命令退出码为 0。
- `tools` 仍显示八个内置工具。
- `call-tool echo` 返回 `{"text": "tool-executor"}`。
- tool demo 仍输出 `user -> assistant(tool_calls) -> tool -> assistant(final)`。

**Step 2：验证 CLI 严格错误语义**

```powershell
uv run learn-hermes-agent call-tool missing '{}'
$LASTEXITCODE
```

预期：stderr 包含 `Unknown tool: missing`，退出码为 `1`。

**Step 3：验证现有路径 preflight 未被绕过**

```powershell
@'
import json

from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.model_tools import handle_function_call

result = json.loads(handle_function_call(
    "read_file",
    {"path": ".env"},
    context=ToolExecutionContext(),
))
assert result["allowed"] is False
assert result["pattern_key"] == "sensitive_name"
print("preflight-ok")
'@ | uv run python -
```

预期输出：`preflight-ok`。

**Step 4：复核源码范围**

```powershell
git diff -- src/learn_hermes_agent/agent/tool_executor.py src/learn_hermes_agent/agent/core.py src/learn_hermes_agent/model_tools.py
git status --short
```

确认：

- 新增模块级顺序 executor，没有 executor 类。
- `model_tools.py` 没有被迁移或重写。
- 没有新增测试文件。
- 没有 checkpoint、terminal、并发、middleware 或 guardrails。

### Task 5：更新项目状态文档

**Files:**

- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

验证全部通过后记录：

- Phase 10 Batch 4 Minimal ToolExecutor 已完成。
- `AIAgent.valid_tool_names` 来自实际 Provider tool definitions。
- 模块级顺序执行器负责模型范围检查、参数解析和 tool result 追加。
- `model_tools` 继续负责 Registry dispatch、安全 preflight 和 CLI 兼容。
- 模型不能执行 `check_fn` 隐藏的已注册工具。
- 下一步为 Batch 5 Minimal Checkpoint。
- terminal、并发 executor、middleware 和 guardrails 仍未实现。

运行：

```powershell
rg -n "Batch 4|Minimal ToolExecutor|valid_tool_names|Batch 5|checkpoint|terminal" docs/00-overview.md docs/02-roadmap.md docs/04-progress-handoff.md docs/plans
git diff --check
git status --short
```

预期：当前状态一致指向 Batch 5；源码改动只包含 executor 和 AIAgent 迁移，文档记录实际运行结果。
