from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from learn_hermes_agent.agent.messages import ChatMessage, tool_message
from learn_hermes_agent.agent.tool_context import ToolExecutionContext
from learn_hermes_agent.model_tools import safe_handle_function_call
from learn_hermes_agent.agent.file_safety import resolve_workspace_path
from learn_hermes_agent.agent.tool_dispatch_helpers import is_destructive_command

if TYPE_CHECKING:
    # TYPE_CHECKING 使 AIAgent 只在类型检查时导入，避免后续形成循环导入。
    from learn_hermes_agent.agent.core import AIAgent

"""
解释一下这里为什么要用TYPE CHECKING：主要是为了避免循环依赖

TYPE_CHECKING 是 typing 提供的常量：
from typing import TYPE_CHECKING

它在两种场景中的值不同：
Python 真正运行代码时：False
mypy / Pyright 检查类型时：视为 True

因此：
if TYPE_CHECKING:
  from learn_hermes_agent.agent.core import AIAgent
实际运行程序时，相当于：
if False:
  from learn_hermes_agent.agent.core import AIAgent

所以不会真正导入 AIAgent。
但 IDE、mypy 或 Pyright 仍然知道这里的：
agent: AIAgent
指的是哪个类，因此可以提供类型检查和代码提示。

为什么要避免运行时导入？
Task 3 之后，依赖关系会是：
core.py
-> 导入 tool_executor.py

如果 tool_executor.py 又在运行时导入 core.py：
core.py
-> tool_executor.py
     -> core.py
就形成了循环导入。

可能发生：
1. Python 开始加载 core.py。
2. core.py 导入 tool_executor.py。
3. tool_executor.py 又导入尚未加载完成的 core.py。
4. 此时 AIAgent 可能还没定义，产生循环导入错误。

使用 TYPE_CHECKING 后，运行时变成：
core.py
-> tool_executor.py
不会再从 tool_executor.py 返回导入 core.py。

同时文件顶部已有：
from __future__ import annotations

它会推迟解析：
agent: AIAgent
所以 Python 运行时不要求 AIAgent 已经是一个真实导入的对象。

总结：

- TYPE_CHECKING：让类型检查工具看见导入。
- 运行时：不执行这个导入。
- from __future__ import annotations：让运行时暂时不解析 AIAgent。
- 两者配合：既有类型提示，又避免循环导入。

"""


def _ensure_file_checkpoint(
        agent: AIAgent,
        function_name: str,
        function_args: dict[str, Any],
        tool_context: ToolExecutionContext | None,
) -> None:
    if function_name not in {"write_file", "patch"}:
        return

    if (
            tool_context is None
            or not agent._checkpoint_mgr.enabled
    ):
        return

    file_path = function_args.get("path")
    if (
            not isinstance(file_path, str)
            or not file_path.strip()
    ):
        return

    resolved_path = resolve_workspace_path(
        file_path,
        tool_context,
    )

    working_dir = (
        agent._checkpoint_mgr.get_working_dir_for_path(
            str(resolved_path),
            boundary=str(tool_context.workspace_root),
        )
    )

    agent._checkpoint_mgr.ensure_checkpoint(
        working_dir,
        f"before {function_name}",
    )


def _parse_tool_arguments(
        raw_arguments: Any,
) -> tuple[dict[str, Any], str | None]:
    """只允许 JSON object 进入工具分发。非法 JSON、数组、数字和字符串都返回结构化错误，不尝试修复"""
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
        # 名称范围检查先于参数解析和 handler
        if function_name not in agent.valid_tool_names:
            # 空的 valid_tool_names 表示全部拒绝
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
                    # 这里的 lambda name, args, context 是从 model_tools.handle_function_call() 对 callback 的调用时传过来的。
                    # 其中只有 agent 不是由 model_tools 传入的。它来自外层 execute_tool_calls_sequential() 的参数，这种行为叫做闭包。
                    #  之所以这样设计，是因为 model_tools 不应该依赖或认识 AIAgent。它只定义通用 callback：
                    # ToolExecutor 再通过 lambda 把自己持有的 agent 补进去。这样避免了 model_tools -> AIAgent 的反向依赖。
                    before_dispatch=(lambda name, args, context: _ensure_file_checkpoint(agent,name,args,context,)),
                )

        messages.append(
            # 每个 call 都追加一个相同 tool_call_id 的 tool result
            tool_message(
                name=function_name,
                content=result_json,
                tool_call_id=tool_call_id,
            )
        )
