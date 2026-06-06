from __future__ import annotations # 推迟类型注解的求值，类型注解就是给一个属性显示指定了类型
# from __future__ import annotations 的作用是：让这些类型标注不要在定义函数或类的时候立刻求值，而是延后处理。

from typing import Any, Literal, NotRequired, TypedDict

# 定义一个类型别名，Role只能是四个值之一, 否则就会被类型检查器warning，只是提醒，不会在运行时自动验证数据
Role = Literal["system", "user", "assistant", "tool"]


# 定义一个字典的形状。普通的字典dict只能告诉你这是一个字典，无法告诉你字典有哪些key。
# TypedDict可以精确的表达，这是一个字典，有哪些key，每个key可能有哪些值
# total=False 的意思是：这个 TypedDict 里的所有字段默认都不是必填的。所以NotRequired就有点重复了，如果total=False删掉了，那这个字典里面只有NotRequired的字段才允许为空
class ChatMessage(TypedDict, total=False):
    role: Role
    content: str | None
    name: NotRequired[str]
    tool_calls: NotRequired[list[dict[str,Any]]]
    tool_call_id: NotRequired[str]

# 开头加了 from __future__ import annotations之后 Python 不会立刻把 str、ChatMessage 当成真实对象去解析，而是先把它们保存成“延迟形式”。
# 可以近似理解为：def system_message(content: "str") -> "ChatMessage":
def system_message(content: str) -> ChatMessage:
    return {"role": "system", "content": content}

def user_message(content: str) -> ChatMessage:
    return {"role": "user", "content": content}


def assistant_message(content: str | None, *, tool_calls: list[dict[str, Any]] | None = None) -> ChatMessage:
    message: ChatMessage = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return message


def tool_message(*, name: str, content: str, tool_call_id: str) -> ChatMessage:
    return {
        "role": "tool",
        "name": name,
        "content": content,
        "tool_call_id": tool_call_id,
    }