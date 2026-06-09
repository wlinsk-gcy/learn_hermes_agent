# Phase 7.5 Minimal Resume Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit files.

**Goal:** 在进入 Memory/Skills 前，补齐最小 session resume 入口，让用户可以从已有 session 继续对话，并在传入 compression parent 时自动跳到 latest continuation child。

**Architecture:** 复用 Phase 7 Batch 4 已建立的 `SessionStore.get_compression_tip()`。CLI `chat --resume <session_id>` 只进入交互模式，读取 tip session 的 messages 作为 history，并继续向该 tip session 追加新消息。当前批次不实现真实 provider、memory、skills、session list projection 或 one-shot resume。

**Tech Stack:** Python 3.11+、SQLite、当前 `SessionStore`、当前 argparse CLI、当前 fake provider。

---

## 背景判断

现在还没有真实 LLM provider。直接进入 Memory/Skills 可以先实现结构层，但无法观察模型是否会主动调用 memory 工具或使用 skill 约束行为。相比之下，Phase 7 刚完成 compression session split 和 `compression_tip`，先做最小 resume 更自然：它能把 parent/child lineage 变成可用能力，也为后续跨 session memory 打基础。

## 选项

### 选项 A：直接进入 Memory/Skills

优点是可以尽早复刻 Hermes 的长期状态结构，例如 memory 文件、skill 目录和 `SKILL.md` frontmatter。缺点是 fake provider 不会主动写 memory 或选择 skill，学习价值主要停留在存储和 schema。

### 选项 B：先接真实 LLM provider

优点是后续 Memory/Skills 能立刻观察真实模型行为。缺点是会提前进入 provider runtime、credential、OpenAI-compatible 响应差异、错误处理等复杂度，容易打断当前 session/storage 主线。

### 选项 C：先做最小 resume

优点是直接消费 Phase 7 的 `compression_tip`，让 compression parent 能恢复到 child/tip，补齐 session continuity。缺点是会稍微推迟 Memory/Skills。

推荐选项 C。

## 设计决策

采用 `chat --resume <session_id>`，并且当前只支持交互模式。

原因：

- 当前 `chat` 无 message 时已经进入交互循环，resume 可以复用这条入口。
- 交互循环已经有 compression split 持久化逻辑，resume 后继续触发 compression 时不需要复制单轮路径。
- `chat --resume <id> "message"` 看起来方便，但会迫使单轮路径也处理“追加到既有 session”和“压缩后切 child session”的持久化分支，当前阶段不值得。

暂不采用 `/resume <session_id>` slash command。

原因是进入交互循环时已经创建了一个 session；再在循环中 resume 会产生一个空 session 或需要延迟创建 session，影响当前简单结构。

暂不采用独立 `resume <session_id>` 子命令。

原因是当前主要入口是 `chat`，独立子命令会增加命令面，但没有额外学习收益。

## 最小数据流

1. 用户运行 `learn-hermes-agent chat --resume <session_id>`。
2. CLI 读取 `SessionStore.get_session(session_id)`，不存在则 stderr 报错并返回 `1`。
3. CLI 调用 `SessionStore.get_compression_tip(session_id)`。
4. 如果传入的是 compression parent，返回 latest child；如果传入普通 session 或 child，返回自身。
5. CLI 读取 tip session 的 `system_prompt`；如果缺失，则 fallback 到 `build_system_prompt()`。
6. CLI 读取 tip session messages 作为 `history`。
7. 进入现有交互循环。
8. 普通新消息追加到 tip session。
9. 如果后续再次触发 compression，沿用现有逻辑结束当前 session 并创建新的 child session。

## 边界

实现：

- `chat --resume <session_id>`
- compression parent 自动跳转到 latest child/tip
- 使用 tip session messages 作为 history
- 使用 tip session 的 persisted `system_prompt`
- missing session 错误处理

不实现：

- one-shot resume：`chat --resume <id> "message"`
- `/resume` slash command
- 独立 `resume` 子命令
- session list projection
- title/search resume
- real provider
- memory/skills
- tests

## Acceptance

- `chat --resume <normal_session_id>` 从该 session 的 messages 继续交互。
- `chat --resume <compression_parent_id>` 实际从 child/tip session 继续交互。
- resumed session 后输入新消息，消息追加到 tip session，而不是 parent session。
- tip session 缺失 `system_prompt` 时仍可 fallback。
- missing session 返回非零退出码。
- 不改变普通 `chat`、`sessions`、`show-session` 或 compression split 行为。
