# Phase 8 Batch 2 Skills Structure Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Follow project rules: provide implementation code as snippets unless the user explicitly asks to edit code files.

**Goal:** Add read-only Skills structure support with local `SKILL.md` scanning, `skills_list` / `skill_view` tools, CLI visibility, and lightweight skill index prompt injection.

**Architecture:** Create `SkillLibrary` under `agent/`, register read-only skills tools under `tools/`, and inject a lightweight skills index into `PromptBuilder`'s volatile layer. Keep the implementation local and deterministic; do not add `skill_manage`, automatic skill creation/update, external skill dirs, platform gating, real provider integration, or tests.

**Tech Stack:** Python 3.11+、PyYAML、current `ToolRegistry`、current `PromptBuilder`、current argparse CLI、current fake provider。

---

## Ground Rules

- Do not modify the reference Hermes repository.
- Do not add tests unless explicitly requested.
- Documentation may be written directly.
- Implementation code should be given as snippets for the user to copy.
- Use lightweight validation:
  - `compileall`
  - CLI output
  - tool definitions
  - temp `LEARN_HERMES_HOME`
  - direct session `system_prompt` inspection

## Task 1: Add Skills Path Config Helper

**Files:**

- Modify: `src/learn_hermes_agent/config.py`

**Step 1: Add helper near `get_memory_dir_path()`**

```python
def get_skills_dir_path() -> Path:
    return get_app_home() / "skills"
```

**Manual Validation:**

```powershell
uv run python -c "from learn_hermes_agent.config import get_skills_dir_path; print(get_skills_dir_path())"
```

Expected: path ending with `.learn_hermes\skills`.

## Task 2: Create `SkillLibrary`

**Files:**

- Create: `src/learn_hermes_agent/agent/skills.py`

**Step 1: Add imports and constants**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

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
```

**Step 2: Add metadata dataclass**

```python
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    category: str | None
    path: str
    skill_dir: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "path": self.path,
        }
```

**Step 3: Add `SkillLibrary` public methods**

```python
class SkillLibrary:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def list_skills(self, category: str | None = None) -> dict[str, object]:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        normalized_category = category.strip() if isinstance(category, str) and category.strip() else None
        skills = []

        for metadata in self._load_metadata():
            if normalized_category is not None and metadata.category != normalized_category:
                continue
            skills.append(metadata.to_dict())

        return {"success": True, "skills": skills}

    def view_skill(self, name: str, file_path: str | None = None) -> dict[str, object]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("skill name is required")

        metadata = self._find_by_name(name.strip())
        if metadata is None:
            raise ValueError(f"unknown skill: {name}")

        target_path = metadata.skill_dir / "SKILL.md"
        relative_path = metadata.path
        if file_path is not None:
            target_path = self._resolve_skill_file(metadata.skill_dir, file_path)
            relative_path = target_path.relative_to(metadata.skill_dir).as_posix()

        if not target_path.exists() or not target_path.is_file():
            raise ValueError(f"skill file not found: {relative_path}")

        content = target_path.read_text(encoding="utf-8")
        return {
            "success": True,
            "name": metadata.name,
            "description": metadata.description,
            "path": relative_path,
            "content": content,
            "linked_files": self._linked_files(metadata.skill_dir),
        }

    def system_prompt_block(self) -> str:
        skills = self.list_skills()["skills"]
        if not skills:
            return ""

        lines = ["Available skills:"]
        for item in skills:
            name = item["name"]
            description = item["description"]
            lines.append(f"- {name}: {description}")

        lines.append("")
        lines.append("Use skill_view(name) to load full skill instructions when a skill is relevant.")
        return "\n".join(lines)
```

**Step 4: Add metadata loading helpers**

```python
    def _load_metadata(self) -> list[SkillMetadata]:
        seen_names: set[str] = set()
        result: list[SkillMetadata] = []

        for skill_md in self._iter_skill_files():
            try:
                metadata = self._metadata_from_file(skill_md)
            except (UnicodeDecodeError, PermissionError):
                continue

            if metadata.name in seen_names:
                continue

            seen_names.add(metadata.name)
            result.append(metadata)

        return sorted(result, key=lambda item: item.name)

    def _iter_skill_files(self) -> list[Path]:
        if not self.skills_dir.exists():
            return []

        files: list[Path] = []
        for path in self.skills_dir.rglob("SKILL.md"):
            if self._is_excluded(path):
                continue
            if path.is_file():
                files.append(path)
        return sorted(files)

    def _metadata_from_file(self, skill_md: Path) -> SkillMetadata:
        content = skill_md.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)
        skill_dir = skill_md.parent

        raw_name = frontmatter.get("name")
        name = str(raw_name).strip() if raw_name else skill_dir.name
        name = name[:MAX_NAME_LENGTH]

        raw_description = frontmatter.get("description")
        description = str(raw_description).strip() if raw_description else self._description_from_body(body)
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[: MAX_DESCRIPTION_LENGTH - 3] + "..."

        category = self._category_for(skill_dir)
        path = skill_md.relative_to(self.skills_dir).as_posix()

        return SkillMetadata(
            name=name,
            description=description,
            category=category,
            path=path,
            skill_dir=skill_dir,
        )
```

**Step 5: Add parsing and safety helpers**

```python
    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---"):
            return {}, content

        marker = "\n---"
        end = content.find(marker, 3)
        if end == -1:
            return {}, content

        raw_frontmatter = content[3:end].strip()
        body = content[end + len(marker):].lstrip()

        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except Exception:
            return {}, body

        if not isinstance(parsed, dict):
            return {}, body

        return parsed, body

    def _description_from_body(self, body: str) -> str:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return ""

    def _category_for(self, skill_dir: Path) -> str | None:
        relative = skill_dir.relative_to(self.skills_dir)
        parent = relative.parent
        if str(parent) == ".":
            return None
        return parent.as_posix()

    def _find_by_name(self, name: str) -> SkillMetadata | None:
        for metadata in self._load_metadata():
            if metadata.name == name:
                return metadata
        return None

    def _resolve_skill_file(self, skill_dir: Path, file_path: str) -> Path:
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path must be a non-empty string")

        relative = Path(file_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("file_path must stay inside the skill directory")

        candidate = (skill_dir / relative).resolve()
        base = skill_dir.resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError("file_path must stay inside the skill directory")

        return candidate

    def _linked_files(self, skill_dir: Path) -> list[str]:
        files: list[str] = []
        for path in skill_dir.rglob("*"):
            if self._is_excluded(path):
                continue
            if not path.is_file() or path.name == "SKILL.md":
                continue
            files.append(path.relative_to(skill_dir).as_posix())
        return sorted(files)

    def _is_excluded(self, path: Path) -> bool:
        return any(part in EXCLUDED_SKILL_DIRS for part in path.parts)
```

**Manual Validation:**

Use a temporary `LEARN_HERMES_HOME` with one sample skill.

Expected:

- `list_skills()` returns one skill.
- `view_skill("writing-plans")` returns `content`.
- `view_skill("writing-plans", "references/example.md")` returns linked file content.
- `view_skill("writing-plans", "../x")` raises `ValueError`.

## Task 3: Register Built-In Skills Tools

**Files:**

- Create: `src/learn_hermes_agent/tools/skills.py`
- Modify: `src/learn_hermes_agent/tools/registry.py`

**Step 1: Create `tools/skills.py`**

```python
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
```

**Step 2: Register skills tools in `discover_builtin_tools()`**

```python
from learn_hermes_agent.tools.skills import register_tools as register_skills_tools
```

Then call:

```python
register_skills_tools(target)
```

after memory registration.

**Manual Validation:**

```powershell
uv run learn-hermes-agent tools
```

Expected: output contains `skills_list` and `skill_view`.

## Task 4: Add CLI Visibility

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Import `SkillLibrary` and `get_skills_dir_path()`**

Add:

```python
from learn_hermes_agent.agent.skills import SkillLibrary
```

Ensure config import includes:

```python
get_skills_dir_path,
```

**Step 2: Add parsers**

```python
skills_parser = subparsers.add_parser(
    "skills",
    help="List available skills.",
)
skills_parser.add_argument(
    "--category",
    help="Optional skill category filter.",
)

view_skill_parser = subparsers.add_parser(
    "view-skill",
    help="View one skill or a linked file inside it.",
)
view_skill_parser.add_argument("name", help="Skill name.")
view_skill_parser.add_argument(
    "file_path",
    nargs="?",
    help="Optional relative file path inside the skill directory.",
)
```

**Step 3: Add handlers**

```python
def run_skills(category: str | None = None) -> int:
    library = SkillLibrary(get_skills_dir_path())
    payload = library.list_skills(category)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def run_view_skill(name: str, file_path: str | None = None) -> int:
    library = SkillLibrary(get_skills_dir_path())
    try:
        payload = library.view_skill(name, file_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0
```

**Step 4: Route commands in `main()`**

```python
if args.command == "skills":
    return run_skills(args.category)

if args.command == "view-skill":
    return run_view_skill(args.name, args.file_path)
```

**Manual Validation:**

```powershell
uv run learn-hermes-agent skills
uv run learn-hermes-agent view-skill writing-plans
uv run learn-hermes-agent view-skill writing-plans references/example.md
```

Expected: JSON output with skill metadata/content.

## Task 5: Inject Skills Index Into Prompt Builder

**Files:**

- Modify: `src/learn_hermes_agent/agent/prompt_builder.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Step 1: Update `PromptBuilder`**

Import:

```python
from learn_hermes_agent.agent.skills import SkillLibrary
```

Add constructor argument:

```python
skill_library: SkillLibrary | None = None,
```

Set:

```python
self.skill_library = skill_library
```

Change `_build_volatile_layer()` to combine memory and skills:

```python
def _build_volatile_layer(self) -> str:
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
```

**Step 2: Update `build_system_prompt()`**

```python
def build_system_prompt() -> str:
    memory_store = MemoryStore(get_memory_dir_path())
    skill_library = SkillLibrary(get_skills_dir_path())
    return PromptBuilder(memory_store=memory_store, skill_library=skill_library).build()
```

**Manual Validation:**

Create a sample skill, run one `chat`, then inspect the new session's `system_prompt`.

Expected:

- `system_prompt` contains `Available skills`.
- `system_prompt` contains the sample skill name.
- `system_prompt` does not contain full linked file content unless explicitly loaded by `skill_view`.

## Task 6: Validation and Docs Update

**Files:**

- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

**Manual Validation Commands:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent tools
uv run learn-hermes-agent skills
uv run learn-hermes-agent view-skill writing-plans
uv run learn-hermes-agent call-tool skills_list '{\"category\":\"planning\"}'
uv run learn-hermes-agent call-tool skill_view '{\"name\":\"writing-plans\"}'
uv run learn-hermes-agent call-tool echo '{\"text\":\"echo_ok\"}'
uv run learn-hermes-agent call-tool memory '{\"action\":\"read\",\"target\":\"memory\"}'
uv run learn-hermes-agent chat --resume missing
```

Use a temp `LEARN_HERMES_HOME` for sample skill validation so the project default state is not polluted.

**Expected:**

- `compileall` passes.
- `tools` lists `echo`, `memory`, `skills_list`, `skill_view`.
- `skills` lists the sample skill.
- `view-skill` returns `SKILL.md` content.
- `call-tool skills_list` and `call-tool skill_view` work.
- Existing `echo`, `memory`, and resume error paths still work.
- Roadmap marks Phase 8 Batch 2 completed.
- Handoff has a Batch 2 Skills entry.
