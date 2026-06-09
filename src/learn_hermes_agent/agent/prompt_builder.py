from __future__ import annotations

from dataclasses import dataclass # dataclass，用来快速定义只保存数据的小类。它会自动生成 __init__、__repr__ 等方法。
from pathlib import Path

from learn_hermes_agent.agent.system_prompt import STABLE_SYSTEM_PROMPT
from learn_hermes_agent.agent.memory_store import MemoryStore
from learn_hermes_agent.agent.skills import SkillLibrary


@dataclass(frozen=True)
class PromptLayers:
    stable: str
    context: str
    volatile: str

    def render(self) -> str:
        # Prompt的三层架构
        parts = [
            self.stable.strip(), # 固定的Prompt，应该长期不变
            self.context.strip(), # 项目上下文，例如AGENTS.md
            self.volatile.strip(), # 短期动态内容，未来给Memory / User profile等使用
        ]
        return "\n\n".join(part for part in parts if part) # 每个部分前面加一个空号


class PromptBuilder:
    """定义 system prompt 构建器。它的职责是：读取需要的上下文文件，然后组装 PromptLayers。"""
    def __init__(
            self,
            *,
            project_root: Path | None = None,
            memory_store: MemoryStore | None = None,
            skill_library: SkillLibrary | None = None,
    ) -> None:
        self.project_root = project_root or Path.cwd()
        self.memory_store = memory_store
        self.skill_library = skill_library

    def build(self) -> str:
        layers = PromptLayers(
            stable=STABLE_SYSTEM_PROMPT,
            context=self._build_context_layer(),
            volatile=self._build_volatile_layer(),
        )
        return layers.render()

    def _build_context_layer(self) -> str:
        # 目前只读AGENTS.md
        context_files = [self.project_root / "AGENTS.md"]

        # 用来保存每个 context file 的内容块
        blocks: list[str] = []
        for path in context_files:
            # 读取并清洗上下文文件
            text = self._read_context_file(path)
            if text:
                # 如果文件不存在或被安全扫描拦截，会返回空字符串或替代文本。然后做拼接，并标注内容块是来自哪个文件的
                """
                例如：
                Context file: AGENTS.md

                # learn_hermes_agent 工作约定
                ...
                """
                blocks.append(f"Context file: {path.name}\n\n{text}")

        if not blocks:
            return ""

        return "Project context:\n\n" + "\n\n---\n\n".join(blocks)

    def _build_volatile_layer(self) -> str:
        """未来可以放user profile，当前时间，当前session状态， 运行环境摘要"""
        blocks: list[str] = []
        if self.memory_store is not None:
            memory_block = self.memory_store.system_prompt_block()
            if memory_block:
                blocks.append(memory_block)

        if self.skill_library is not None:
            skills_block = self.skill_library.system_prompt_block()
            if skills_block:
                blocks.append(skills_block)

        return "\n\n".join(blocks)

    def _read_context_file(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""

        text = path.read_text(encoding="utf-8") # 显式UTF-8，因为项目文档是中文
        return self._sanitize_context_text(text)

    def _sanitize_context_text(self, text: str) -> str:
        """清洗 context 文件内容。当前只是一个非常轻量的 prompt injection 检查"""
        risky_markers = [
            "ignore previous instructions",
            "ignore all previous instructions",
            "disregard previous instructions",
        ]
        # 转小写，方便做判断
        lowered = text.lower()
        # 意思是：如果上下文文件里出现“忽略之前指令”这种文本，就不要原样注入。
        # 返回一个说明即可
        if any(marker in lowered for marker in risky_markers):
            return "[Context file omitted: possible prompt-injection marker detected.]"

        return text.strip()
