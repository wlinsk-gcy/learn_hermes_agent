# Phase 8 Batch 1 Memory Structure Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit files.

**Goal:** 先实现 Hermes Memory 的结构层：文件持久化、`memory` tool、system prompt 注入边界和手工可观察验证。

**Architecture:** 使用本地 `.learn_hermes/memories/` 下的 `MEMORY.md` 和 `USER.md` 作为 durable store。`MemoryStore` 负责读写和渲染 frozen snapshot；`memory` tool 负责 live mutation；`PromptBuilder` 在新 session 构建 system prompt 时注入 snapshot。当前批次不实现真实 LLM provider、自动记忆、external memory provider、prefetch/sync hooks 或 Skills。

**Tech Stack:** Python 3.11+、当前 `ToolRegistry`、当前 `PromptBuilder`、当前 argparse CLI、当前 fake provider。

---

## 真实 Hermes 对照

- `tools/memory_tool.py`
  - file-backed curated memory。
  - 两个 store：`MEMORY.md` 和 `USER.md`。
  - 单个 `memory` tool，通过 `action` 参数执行 `add`、`replace`、`remove`。
  - frozen snapshot：system prompt 使用 session 开始时的快照；tool responses 反映 live state。
- `agent/memory_manager.py`
  - 后续负责 provider orchestration、prefetch、post-turn sync、external providers 和 compression hooks。
  - 当前学习项目暂不进入这些复杂度。
- `agent/memory_provider.py`
  - 真实 Hermes 支持 pluggable memory providers。
  - 当前阶段不做 provider abstraction，只保留本地文件 store。

## Architecture Decision Gate

### 选项 A：Memory 和 Skills 一起做

优点是 Phase 8 完整度高。缺点是范围过大，并且 fake provider 不会主动选择写 memory 或调用 skill；容易把存储、工具、prompt、skill 扫描、skill 编辑混在一起。

### 选项 B：先做 Memory 结构层

优点是最贴近 Hermes 的跨 session recall 基础，可以用 `call-tool memory ...` 手工验证写入，再用新 session 的 `system_prompt` 验证注入。缺点是暂时不会出现模型自主记忆行为。

### 选项 C：先做 Skills 结构层

优点是没有真实 LLM 也可以观察 `SKILL.md` frontmatter 扫描和 progressive disclosure。缺点是它更像资料索引，还没触及 Hermes 的 durable self-improvement memory 基础。

推荐选项 B。

## Batch 1 范围

实现：

- `MemoryStore`
  - `load()`
  - `read(target)`
  - `add(target, content)`
  - `replace(target, old_text, content)`
  - `remove(target, old_text)`
  - `system_prompt_block()`
- `memory` tool
  - `action`: `add | read | replace | remove`
  - `target`: `memory | user`
  - `content`
  - `old_text`
- tool registry 接入
- `PromptBuilder` volatile layer 注入 memory snapshot
- 使用现有 `call-tool`、`chat`、`show-session` 做手工验证

不实现：

- 自动 memory 写入
- `MemoryManager`
- external memory providers
- prefetch/sync_turn
- compression hooks
- memory config
- Skills
- 真实 provider
- 测试

## 数据模型

Memory 文件位置：

```text
.learn_hermes/
  memories/
    MEMORY.md
    USER.md
```

`MEMORY.md` 用于 agent notes：

- 环境事实
- 项目约定
- 工具 quirks
- 长期有用的工作流事实

`USER.md` 用于 user profile：

- 用户偏好
- 沟通风格
- 工作习惯
- 用户明确要求记住的个人化信息

文件格式采用简单 ASCII delimiter，便于手写和解析：

```text
entry one

---

entry two
```

Batch 1 先不支持多行复杂结构；如果 content 包含 delimiter，工具应拒绝写入。

## Prompt 注入

`PromptBuilder` 当前已有 stable/context/volatile 三层。Memory 应进入 volatile layer：

```text
Persistent memory:

MEMORY:
- ...

USER:
- ...
```

设计重点：

- 新 session 构建 system prompt 时读取 memory snapshot。
- 当前 session 内通过 `memory` tool 写入后，不重建本 session system prompt。
- 下一次新建或 resume 到缺失 prompt 的 session 时才看到更新后的 memory。

这对齐真实 Hermes 的 frozen snapshot 思路，也避免破坏 prompt cache 稳定性。

## Tool 行为

`memory(action="add", target="memory", content="...")`

- 追加一条 entry。
- 重复 entry 可以视为 no-op 或返回已存在；Batch 1 推荐 no-op 成功。

`memory(action="read", target="memory")`

- 返回当前 live entries。

`memory(action="replace", target="memory", old_text="...", content="...")`

- `old_text` 必须匹配且只匹配一条 entry 的 substring。
- 匹配 0 或多条都返回错误。

`memory(action="remove", target="memory", old_text="...")`

- 同样使用唯一 substring 匹配。

返回值统一是可 JSON 序列化 dict，由现有 `handle_function_call()` 转成 JSON 字符串。

## Prompt Injection 边界

Batch 1 做轻量防护即可：

- 写入时拒绝明显 injection marker，例如：
  - `ignore previous instructions`
  - `ignore all previous instructions`
  - `disregard previous instructions`
  - `you are now`
- snapshot 渲染时再做一次扫描；如果发现风险 entry，注入 placeholder，而不是原文。

这不是完整安全系统，只是保证会进入 system prompt 的 durable 内容不会完全裸奔。

## Acceptance

- `call-tool memory '{"action":"add","target":"memory","content":"..."}'` 可以写入 `.learn_hermes/memories/MEMORY.md`。
- `call-tool memory '{"action":"read","target":"memory"}'` 可以读回 entries。
- `replace/remove` 使用唯一 substring 匹配。
- `tools` 输出中包含 `memory` tool schema。
- 新 session 的 `sessions.system_prompt` 可以观察到 memory snapshot。
- 当前 session 内写 memory 不强制修改已经持久化的 `system_prompt`。
- 普通 `chat`、`chat --resume`、`echo` tool 行为不变。
