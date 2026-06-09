from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MemoryTarget = Literal["memory", "user"]

# 就是md的---前后加两换行
ENTRY_SEPARATOR = "\n\n---\n\n"
# 简单的prompt injection 风险词
RISKY_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "you are now",
)


@dataclass(frozen=True)
class MemorySnapshot:
    """一次读取出来的记忆快照"""
    memory: list[str]
    user: list[str]


class MemoryStore:
    """
    把长期记忆存在本地的Markdown文件里，并提供CRUD和render to system prompt的接口。
    暂时还不是 Hermes 里的完整 MemoryManager，不负责自动判断“该不该记忆”。
    """
    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir

    def load(self) -> MemorySnapshot:
        """读取完整 memory 快照。"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        return MemorySnapshot(
            memory=self._read_entries(self._path_for("memory")),
            user=self._read_entries(self._path_for("user")),
        )

    def read(self, target: MemoryTarget) -> dict[str, object]:
        """读取某一个 target(memory / user) 的记忆"""
        entries = self._read_entries(self._path_for(target))
        # 返回统一 dict，方便后面 tool handler 直接 JSON 化返回给模型。
        return {"success": True, "target": target, "entries": entries}

    def add(self, target: MemoryTarget, content: object) -> dict[str, object]:
        """向某个 memory 文件新增一条内容。"""
        content = self._normalize_content(content)
        self._validate_content(content)

        path = self._path_for(target)
        entries = self._read_entries(path)
        if content not in entries:
            entries.append(content)
            self._write_entries(path, entries)

        return {"success": True, "target": target, "entries": entries}

    def replace(self, target: MemoryTarget, old_text: str, content: object) -> dict[str, object]:
        """替换某条已有 memory。"""
        # 旧记忆
        old_text = old_text.strip()
        # 新记忆
        content = self._normalize_content(content)
        self._validate_content(content)

        path = self._path_for(target)
        entries = self._read_entries(path)
        # 查找唯一匹配的条目下标。这里是“包含匹配”，不是必须完全相等。
        index = self._find_unique_match(entries, old_text)
        entries[index] = content
        self._write_entries(path, entries)

        return {"success": True, "target": target, "entries": entries}

    def remove(self, target: MemoryTarget, old_text: str) -> dict[str, object]:
        old_text = old_text.strip()

        path = self._path_for(target)
        entries = self._read_entries(path)
        index = self._find_unique_match(entries, old_text)
        removed = entries.pop(index)
        self._write_entries(path, entries)

        return {"success": True, "target": target, "removed": removed, "entries": entries}

    def system_prompt_block(self) -> str:
        """把当前 memory 渲染成一段可以放进 system prompt 的文本。"""
        snapshot = self.load()
        memory_entries = self._sanitize_for_prompt(snapshot.memory)
        user_entries = self._sanitize_for_prompt(snapshot.user)

        parts: list[str] = []
        if memory_entries:
            parts.append("MEMORY:\n" + "\n".join(f"- {entry}" for entry in memory_entries))
        if user_entries:
            parts.append("USER:\n" + "\n".join(f"- {entry}" for entry in user_entries))

        if not parts:
            return ""

        return "Persistent memory snapshot:\n\n" + "\n\n".join(parts)

    def _path_for(self, target: MemoryTarget) -> Path:
        if target == "user":
            return self.memory_dir / "USER.md"
        return self.memory_dir / "MEMORY.md"

    def _read_entries(self, path: Path) -> list[str]:
        if not path.exists():
            return []

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []

        # 按分隔符拆成多条，清理每条首尾空白，丢弃空条目。
        return [entry.strip() for entry in text.split(ENTRY_SEPARATOR) if entry.strip()]

    def _write_entries(self, path: Path, entries: list[str]) -> None:
        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        # 清理条目，过滤空条目，再用分隔符拼成文件内容。
        text = ENTRY_SEPARATOR.join(entry.strip() for entry in entries if entry.strip())
        # 非空内容末尾补一个换行；空内容就写空字符串。
        path.write_text(text + ("\n" if text else ""), encoding="utf-8")

    def _normalize_content(self, content: object) -> str:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        return content.strip()

    def _validate_content(self, content: str):
        if not content:
            raise ValueError("content is required")
        if ENTRY_SEPARATOR.strip() in content:
            raise ValueError("content must not contain the memory entry separator")

        lowered = content.lower()
        if any(marker in lowered for marker in RISKY_MARKERS):
            raise ValueError("content contains a possible prompt-injection marker")

    def _find_unique_match(self, entries: list[str], old_text: str) -> int:
        if not old_text:
            raise ValueError("old_text is required")

        matches = [index for index, entry in enumerate(entries) if old_text in entry]
        if not matches:
            raise ValueError("old_text did not match any entry")
        if len(matches) > 1:
            raise ValueError("old_text matched multiple entries; provide a more specific substring")

        return matches[0]

    def _sanitize_for_prompt(self, entries: list[str]) -> list[str]:
        result: list[str] = []
        for entry in entries:
            lowered = entry.lower()
            if any(marker in lowered for marker in RISKY_MARKERS):
                result.append("[BLOCKED: memory entry contained a possible prompt-injection marker.]")
            else:
                result.append(entry)
        return result
