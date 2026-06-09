# Phase 8 Batch 2 Skills Structure Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit code files.

**Goal:** 实现 Hermes Skills 的只读结构层：本地 `skills/` 扫描、`SKILL.md` frontmatter 解析、`skills_list` / `skill_view` 工具、CLI 可观察入口，以及新 session 的轻量 skills index 注入。

**Architecture:** 使用 `.learn_hermes/skills/` 作为本地 skills 根目录。`SkillLibrary` 负责扫描、解析、查看和 prompt index 渲染；`tools/skills.py` 负责把只读能力暴露为 model tool；CLI 提供手工观察入口。当前批次不实现 `skill_manage`、自动创建/更新 skill、background review、external skill dirs、disabled skills config 或平台 gating。

**Tech Stack:** Python 3.11+、PyYAML、当前 `ToolRegistry`、当前 `PromptBuilder`、当前 argparse CLI、当前 fake provider。

---

## 参考结论

对照参考仓库：

- `tools/skills_tool.py`
  - `skills_list` 只返回 name / description / category，用于 progressive disclosure 第一层。
  - `skill_view` 按需加载 `SKILL.md` 或 skill 目录内的 linked files。
  - `skill_manage` 是写文件/编辑 skill 的能力，风险更高。
- `agent/skill_utils.py`
  - 共享 frontmatter parsing、platform matching、external dirs、excluded dirs 等工具。
- `agent/conversation_loop.py`
  - 真实 Hermes 会在后台 review 中触发 memory/skill 更新；当前学习项目不接这个行为。

当前批次只复刻最有学习价值且低风险的只读结构层。

## 方案选择

### 选项 A：完整 Skills 系统

一次实现 `skills_list`、`skill_view`、`skill_manage`、自动 skill review 和 background update。

优点是更接近真实 Hermes。缺点是范围过大，尤其 `skill_manage` 会引入文件写入、自修改、安全审批和 provenance 问题；在 fake provider 下也无法观察真实自主行为。

### 选项 B：只读 Skills 结构层

实现 `skills/` 扫描、frontmatter 解析、`skills_list`、`skill_view`、CLI 入口和轻量 prompt index。

优点是能学习 Hermes progressive disclosure 的核心：先暴露元数据，需要时再按需加载全文。也能用 fake provider 和手工 CLI 验证完整链路。缺点是暂时不能创建或更新 skill。

### 选项 C：只做 prompt 注入

扫描 skills 并把所有摘要直接写入 system prompt，不提供 tool。

优点是实现少。缺点是缺少 `skill_view` 的按需加载能力，容易把 skill 系统退化成 prompt 噪音。

推荐选项 B。

## Batch 2 范围

实现：

- `get_skills_dir_path()`
- `SkillLibrary`
  - `list_skills(category=None)`
  - `view_skill(name, file_path=None)`
  - `system_prompt_block()`
- `SKILL.md` frontmatter parsing
- nested category detection
- linked file listing
- safe relative file viewing
- built-in tools:
  - `skills_list`
  - `skill_view`
- CLI:
  - `skills [--category CATEGORY]`
  - `view-skill NAME [FILE_PATH]`
- prompt builder 注入轻量 skills index，不注入完整 skill 内容。

不实现：

- `skill_manage`
- 自动创建 skill
- 自动更新 skill
- background memory/skill review
- external skill dirs
- disabled skills config
- platform gating
- plugin skills
- skill provenance
- tests

## 数据模型

目录：

```text
.learn_hermes/
  skills/
    planning/
      writing-plans/
        SKILL.md
        references/
          examples.md
```

`SKILL.md` 格式：

```markdown
---
name: writing-plans
description: Write implementation plans for multi-step changes.
---

# Writing Plans

Instructions here.
```

元数据：

- `name`: frontmatter `name`，缺失时使用 skill 目录名，最长 64。
- `description`: frontmatter `description`，缺失时使用正文第一行非 heading 文本，最长 1024。
- `category`: skill 目录相对于 skills root 的父路径；root 下 skill 为 `None`。
- `path`: `SKILL.md` 相对于 skills root 的路径。

## Prompt 注入

只注入轻量 index：

```text
Available skills:

- writing-plans: Write implementation plans for multi-step changes.

Use skill_view(name) to load full skill instructions when a skill is relevant.
```

不把完整 `SKILL.md` 自动塞进 system prompt。这样保持 progressive disclosure，也避免 token 膨胀。

## Tool 行为

`skills_list`：

```json
{
  "success": true,
  "skills": [
    {
      "name": "writing-plans",
      "description": "Write implementation plans for multi-step changes.",
      "category": "planning",
      "path": "planning/writing-plans/SKILL.md"
    }
  ]
}
```

`skill_view`：

```json
{
  "success": true,
  "name": "writing-plans",
  "description": "Write implementation plans for multi-step changes.",
  "path": "planning/writing-plans/SKILL.md",
  "content": "...",
  "linked_files": ["references/examples.md"]
}
```

`file_path` 只能是 skill 目录内的相对路径，不允许绝对路径、`..` 或越界访问。

## 失败模式

- skills 目录不存在：创建目录或返回空列表。
- `SKILL.md` frontmatter 不合法：降级为空 frontmatter，使用目录名和正文 fallback。
- skill name 不存在：返回 `ValueError`，CLI 显示 `error: ...`。
- `file_path` 越界：返回 `ValueError`。
- 文件非 UTF-8 或不可读：跳过该 skill 或返回错误。

## 验收

- `skills` CLI 能列出 sample skill。
- `view-skill NAME` 能显示该 skill 的 `SKILL.md` 内容。
- `view-skill NAME references/example.md` 能读取 linked file。
- `tools` 输出包含 `skills_list` 和 `skill_view`。
- `call-tool skills_list ...` 和 `call-tool skill_view ...` 可用。
- 新 session 的 `system_prompt` 包含 `Available skills` 和 sample skill 名称。
- 现有 `memory`、`echo`、`chat --resume missing` 仍可用。
