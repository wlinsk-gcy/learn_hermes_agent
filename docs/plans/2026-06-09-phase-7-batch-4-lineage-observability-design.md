# Phase 7 Batch 4 Lineage Observability Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit files.

**Goal:** 为 Phase 7 收口补齐最小 session lineage 可观察性，让压缩后的 parent/child 关系可以被 CLI 清楚查看。

**Architecture:** `SessionStore` 提供 lineage 查询 helper，CLI `show-session` 负责展示 session metadata 和 messages。当前批次只增强可观察性，不引入真实 provider usage、LLM summary、compression lock、gateway projection、resume 或 memory hooks。

**Tech Stack:** Python 3.11+、SQLite、当前 `SessionStore`、当前 CLI。

---

## 对照的真实 Hermes 源码

- `hermes_state.py`
  - `parent_session_id` 表示 session lineage。
  - `end_reason='compression'` 表示 compression continuation 边界。
  - `get_compression_tip()` 会把 compression parent 投影到最新 continuation child。
  - `resolve_resume_session_id()` 会在 resume 时跳到真正持有 messages 的 descendant。
  - `list_sessions_rich()` 能把 compression root 投影到 tip，避免用户看到一堆中间 session。
- `agent/conversation_compression.py`
  - compression 后结束旧 session、创建 child session、旋转当前 session id。
  - 真实 Hermes 还会通知 memory/context engine，并释放 compression lock。

## 设计决策

### 采用方案

Batch 4 只实现 lineage 可观察性：

- `SessionStore.get_session_chain(session_id)`：返回 root -> current 的 parent chain。
- `SessionStore.get_compression_tip(session_id)`：如果传入 session 后面有 compression continuation child，返回链路末端 child；否则返回原 id。
- `show-session <session_id>` 输出 session metadata：
  - `id`
  - `title`
  - `parent_session_id`
  - `end_reason`
  - `ended_at`
  - `compression_tip`
  - `lineage`
  - `message_count`
- messages 仍按当前方式 JSON 输出。

原因：

- Batch 3 已经创建 parent/child，但当前 CLI 只靠 `/sessions` 观察，信息够用但不够聚焦。
- 这一步能学习真实 Hermes 的 lineage/tip 设计，而不会进入 resume/gateway 的复杂语义。
- 对后续最小 resume 是铺垫，但不提前实现 resume。

### 暂不采用

不实现 compression lock。

真实 Hermes 需要 `compression_locks` 是因为多入口、多 agent、background review 可能同时压缩同一个 session。当前学习项目只有单进程 CLI 交互，没有并发压缩入口；现在实现锁会增加表和过期语义，但没有实际验证场景。

不实现真实 provider usage、auxiliary compression model 或 LLM summary。

这些能力会把 Phase 7 带入 provider runtime、模型 metadata、auxiliary provider 和真实 token usage，超出当前阶段“不跳真实 provider”的边界。

不实现 session list projection 或 resume redirect。

真实 Hermes 的 `list_sessions_rich()` 会把 compression root 投影到 tip，`resolve_resume_session_id()` 会重定向 resume 目标。当前项目还没有 resume 命令；Batch 4 只提供 helper 和观测入口，避免 CLI 行为突然变复杂。

## 最小数据流

1. 用户运行 `learn-hermes-agent show-session <session_id>`。
2. CLI 调用 `store.get_session(session_id)` 获取 metadata。
3. CLI 调用 `store.get_session_chain(session_id)` 获取 lineage。
4. CLI 调用 `store.get_compression_tip(session_id)` 获取当前 compression continuation tip。
5. CLI 调用 `store.get_session_messages(session_id)` 获取该 session 自身 messages。
6. CLI 输出一个 JSON object：
   - `session`
   - `compression_tip`
   - `lineage`
   - `messages`

## Acceptance

- 普通 session 的 `lineage` 只有自己，`compression_tip` 等于自己。
- compression parent 的 `compression_tip` 指向 child。
- compression child 的 `lineage` 包含 parent 和 child。
- `show-session` 能同时看到 metadata 和 messages。
- 不改变 `chat`、`sessions`、压缩触发或持久化行为。

