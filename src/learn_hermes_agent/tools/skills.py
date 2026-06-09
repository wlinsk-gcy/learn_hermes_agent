from __future__ import annotations

from typing import Any

from learn_hermes_agent.agent.skills import SkillLibrary
from learn_hermes_agent.config import get_skills_dir_path
from learn_hermes_agent.tools.registry import ToolEntry, ToolRegistry

SKILLS_LIST_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "description": "Optional category filter.",
        }
    },
    "required": [],
    "additionalProperties": False,
}

SKILL_VIEW_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name. Use skills_list to discover available skills.",
        },
        "file_path": {
            "type": "string",
            "description": "Optional relative file path inside the skill directory.",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


def skills_list(arguments: dict[str, Any]) -> dict[str, object]:
    category = arguments.get("category")
    if category is not None and not isinstance(category, str):
        raise ValueError("category must be a string")
    # 这里每次调用这个函数都创建一个SkillLibrary，原因如下：
    # 第一，保持 handler 无状态。SkillLibrary 只是一个很轻的对象，里面只保存：skills_dir
    # 第二，确保读到最新文件状态。Skills 是本地文件系统内容，用户可能刚刚新增、删除或修改了某个 SKILL.md。如果我们把SkillLibrary 做成全局对象并缓存扫描结果，就要处理缓存失效。当前阶段没必要。
    # 第三，和当前工具体系一致。memory tool 也是每次调用时创建：MemoryStore
    # 这是同一种模式：tool handler 负责把 JSON 参数转成领域对象调用，领域对象负责读写文件。
    # 后续也可以优化，做缓存，做进程内单例
    library = SkillLibrary(get_skills_dir_path())
    return library.list_skills(category)


def skill_view(arguments: dict[str, Any]) -> dict[str, object]:
    name = arguments.get("name")
    file_path = arguments.get("file_path")

    if not isinstance(name, str):
        raise ValueError("skill_view requires a string argument: name")
    if file_path is not None and not isinstance(file_path, str):
        raise ValueError("file_path must be a string")

    library = SkillLibrary(get_skills_dir_path())
    return library.view_skill(name, file_path)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolEntry(
            name="skills_list",
            description="List available skills with minimal metadata.",
            parameters=SKILLS_LIST_PARAMETERS,
            handler=skills_list,
        )
    )
    registry.register(
        ToolEntry(
            name="skill_view",
            description="Load a skill's full instructions or a linked file.",
            parameters=SKILL_VIEW_PARAMETERS,
            handler=skill_view,
        )
    )
