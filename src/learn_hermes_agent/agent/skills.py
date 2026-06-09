"""
把 .learn_hermes/skills/**/SKILL.md 目录结构读成一个只读技能库，提供三类能力：
1. 列出技能元数据：list_skills()
2. 查看某个技能或技能内文件：view_skill()
3. 渲染一段轻量 system prompt index：system_prompt_block()
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 用于解析 SKILL.md 顶部的 YAML frontmatter。
import yaml

# 限制 skill 名称和描述长度
# 对齐 Hermes progressive disclosure 的思路：列表里只放短元数据，不塞大文本。
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

# 定义扫描时要跳过的目录，比如 .git、虚拟环境、缓存目录。frozenset 表示不可变集合，适合作为模块级常量。
EXCLUDED_SKILL_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    category: str | None
    # SKILL.md 相对路径
    path: str
    # skill 目录的真实路径。
    skill_dir: Path

    def to_dict(self) -> dict[str, object]:
        # 返回给 CLI/tool 的公开字段。没有返回 skill_dir，因为那是内部文件系统路径，不需要暴露给模型。
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "path": self.path,
        }


class SkillLibrary:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def list_skills(self, category: str | None = None) -> dict[str, object]:
        """列出 skills，可选按 category 过滤。"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        normalized_category = (
            category.strip()
            if isinstance(category, str) and category.strip()
            else None
        )
        skills = []

        for metadata in self._load_metadata():
            # 如果指定category的话，需要过滤指定类型的skill
            if normalized_category is not None and metadata.category != normalized_category:
                continue
            skills.append(metadata.to_dict())

        return {"success": True, "skills": skills}

    def view_skill(self, name: str, file_path: str | None = None) -> dict[str, object]:
        """查看某个 skill。默认看 SKILL.md；如果传了 file_path，看 skill 目录内的 linked file。"""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("skill name is required")

        metadata = self._find_by_name(name.strip())
        if metadata is None:
            raise ValueError(f"unknown skill: {name}")

        # 默认目标文件是该 skill 的主文件 SKILL.md。
        target_path = metadata.skill_dir / "SKILL.md"
        relative_path = metadata.path
        if file_path is not None:
            target_path = self._resolve_skill_file(metadata.skill_dir, file_path)
            relative_path = target_path.relative_to(metadata.skill_dir).as_posix()

        # 如果文件不存在或不是普通文件就报错。
        if not target_path.exists() or not target_path.is_file():
            raise ValueError(f"skill file not found: {relative_path}")
        # utf-8-sig 兼容 utf-8，sig的话，如果文件的开头有BOM，他会自动去掉的这样 SKILL.md frontmatter 能正常识别，linked file 内容也不会带 \ufeff。
        content = target_path.read_text(encoding="utf-8-sig")
        return {
            "success": True,
            "name": metadata.name,
            "description": metadata.description,
            "path": relative_path,
            "content": content,
            # linked_files 让模型知道还可以继续 skill_view(name, file_path) 加载哪些辅助文件。
            "linked_files": self._linked_files(metadata.skill_dir),
        }

    def system_prompt_block(self) -> str:
        """生成注入 system prompt 的轻量 skill index。"""
        skills = self.list_skills()["skills"]
        if not skills:
            return ""
        # prompt block 标题。
        lines = ["Available skills:"]
        for item in skills:
            name = item["name"]
            description = item["description"]
            lines.append(f"- {name}: {description}")

        lines.append("")
        # 告诉模型：如果某个 skill 相关，要用 skill_view 加载完整说明。这就是 progressive disclosure。
        lines.append("Use skill_view(name) to load full skill instructions when a skill is relevant.")
        return "\n".join(lines)

    def _load_metadata(self) -> list[SkillMetadata]:
        # 用来去重
        seen_names: set[str] = set()
        # 用来保存解析结果
        result: list[SkillMetadata] = []

        # 遍历所有 SKILL.md
        for skill_md in self._iter_skill_files():
            try:
                metadata = self._metadata_from_file(skill_md)
            except (UnicodeDecodeError, PermissionError):
                # 读不了或编码不对就跳过，不让一个坏文件拖垮整个扫描。
                continue

            if metadata.name in seen_names:
                # 同名 skill 只保留第一个，避免 tool 查询歧义。
                continue

            seen_names.add(metadata.name)
            result.append(metadata)

        # 按名称排序，输出稳定。
        return sorted(result, key=lambda item: item.name)

    def _iter_skill_files(self) -> list[Path]:
        if not self.skills_dir.exists():
            # 目录不存在时返回空列表。
            return []

        files: list[Path] = []
        # 递归扫描所有名为 SKILL.md 的文件。
        # 从 self.skills_dir 开始，递归进入所有子目录，查找名字匹配 SKILL.md 的路径。
        for path in self.skills_dir.rglob("SKILL.md"):
            # 跳过缓存、虚拟环境、.git 等目录里的文件。
            if self._is_excluded(path):
                continue
            # 只接受普通文件。
            if path.is_file():
                files.append(path)
        # 路径排序，保证扫描结果稳定。
        return sorted(files)

    def _metadata_from_file(self, skill_md: Path) -> SkillMetadata:
        # 读取文件，拆 frontmatter 和正文，拿到 skill 目录。
        content = skill_md.read_text(encoding="utf-8-sig")
        frontmatter, body = self._parse_frontmatter(content)
        skill_dir = skill_md.parent

        # 优先使用 frontmatter 的 name；没有就用目录名；最后截断长度。
        raw_name = frontmatter.get("name")
        name = str(raw_name).strip() if raw_name else skill_dir.name
        name = name[:MAX_NAME_LENGTH]

        # 优先使用 frontmatter 的 description；没有就从正文找第一行非标题文本。
        raw_description = frontmatter.get("description")
        description = (
            str(raw_description).strip()
            if raw_description
            else self._description_from_body(body)
        )
        # 描述太长就截断并加省略号。
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[: MAX_DESCRIPTION_LENGTH - 3] + "..."

        # 计算分类和相对路径。
        category = self._category_for(skill_dir)
        path = skill_md.relative_to(self.skills_dir).as_posix()

        return SkillMetadata(
            name=name,
            description=description,
            category=category,
            path=path,
            skill_dir=skill_dir,
        )

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        # 没有 YAML frontmatter，就返回空 metadata 和完整正文。
        if not content.startswith("---"):
            return {}, content

        marker = "\n---"
        # 寻找结束 frontmatter 的 ---。从第 3 个字符后开始找，避免命中开头的 ---。
        end = content.find(marker, 3)
        # 找不到结束标记，就不解析 frontmatter。返回空 metadata 和完整正文。
        if end == -1:
            return {}, content

        # 切出 YAML 部分和正文部分。
        raw_frontmatter = content[3:end].strip()
        body = content[end + len(marker):].lstrip()

        try:
            # 用安全 YAML loader 解析。解析失败就降级为空 frontmatter。
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except Exception:
            return {}, body

        # frontmatter 必须是对象；列表、字符串等都不接受。
        if not isinstance(parsed, dict):
            return {}, body

        # 返回解析结果和正文。
        return parsed, body

    def _description_from_body(self, body: str) -> str:
        # 逐行检查正文。
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                # 找第一行非空、非标题的文本作为 fallback description。
                return line
        return ""

    def _category_for(self, skill_dir: Path) -> str | None:
        # 计算 skill 目录相对 skills 根目录的位置。
        relative = skill_dir.relative_to(self.skills_dir)
        parent = relative.parent
        if str(parent) == ".":
            # 如果 skill 直接放在根目录下，就没有 category。
            return None
        # 否则父路径就是 category，比如 planning/writing-plans 的 category 是 planning。
        return parent.as_posix()

    def _find_by_name(self, name: str) -> SkillMetadata | None:
        """重新扫描 metadata，找到名字完全匹配的 skill。找不到返回 None。"""
        for metadata in self._load_metadata():
            if metadata.name == name:
                return metadata
        return None

    def _resolve_skill_file(self, skill_dir: Path, file_path: str) -> Path:
        # file_path 必须是非空字符串。
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path must be a non-empty string")

        # 禁止绝对路径和 ..，防止读取 skill 目录外的文件。
        relative = Path(file_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("file_path must stay inside the skill directory")

        # 把目标路径和 skill 根目录都解析成绝对路径。
        candidate = (skill_dir / relative).resolve()
        base = skill_dir.resolve()
        # 二次防护：确保目标路径仍在 skill 目录里面。
        if candidate != base and base not in candidate.parents:
            raise ValueError("file_path must stay inside the skill directory")
        # 返回安全解析后的文件路径。
        return candidate

    def _linked_files(self, skill_dir: Path) -> list[str]:
        files: list[str] = []
        # 递归遍历 skill 目录下所有文件。
        for path in skill_dir.rglob("*"):
            # 跳过排除目录。
            if self._is_excluded(path):
                continue
            # 只收集普通文件，并排除主文件 SKILL.md。
            if not path.is_file() or path.name == "SKILL.md":
                continue
            # 保存相对路径并排序。
            # .as_posix(): 是 pathlib.Path 的方法，用来把路径转换成 POSIX 风格字符串，也就是统一使用 / 作为分隔符。
            # 在 Windows 上，普通路径可能是：Path("planning") / "writing-plans" / "SKILL.md"
            # 转成字符串通常是：planning\writing-plans\SKILL.md
            # path.as_posix() = planning/writing-plans/SKILL.md
            # 因为我们把路径放进 JSON / tool 输出里，希望跨平台稳定。无论是在 Windows、macOS、Linux，输出都统一成：/**/**
            files.append(path.relative_to(skill_dir).as_posix())

        return sorted(files)

    def _is_excluded(self, path: Path) -> bool:
        # 只要路径任意一段命中排除目录，就认为该路径应该跳过。
        return any(part in EXCLUDED_SKILL_DIRS for part in path.parts)
