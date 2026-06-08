# Phase 7 Context Compression Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit files.

**Goal:** 对齐真实 Hermes 的 context compression 方向，在学习项目中实现最小可观察的长上下文压缩。

**Architecture:** `ContextCompressor` 负责上下文 token 估算和压缩；`AIAgent.run_conversation()` 在 provider 调用前执行 preflight compression。`IterationBudget` 只表示 agent/tool loop 的迭代次数上限，不表示上下文 token budget。

**Tech Stack:** Python 3.11+、当前 `ChatMessage` 字典协议、当前 fake provider、当前 SQLite session store。

---

## 对照的真实 Hermes 源码

- `agent/context_compressor.py`
  - `ContextCompressor` 是真实上下文压缩核心。
  - 算法方向是：裁剪旧 tool result、保护头部、保护尾部、摘要中间消息、后续压缩迭代更新摘要。
- `agent/conversation_loop.py`
  - 在 provider 调用前执行 preflight compression。
  - provider 返回后也会结合真实或粗略 usage 判断是否需要压缩。
- `agent/conversation_compression.py`
  - 压缩成功后会切分 SQLite session、旋转 `session_id`、创建 child session、重建 system prompt。
- `hermes_state.py`
  - `sessions.parent_session_id` 表示 compression continuation chain。
  - `compression_locks` 用于避免多个 agent 同时压缩同一 session。
- `agent/iteration_budget.py`
  - `IterationBudget` 是线程安全的迭代次数计数器，服务于 `max_iterations`。
  - 它不是 context token budget。
- `cli-config.yaml.example`
  - 真实配置区是 `compression.enabled`、`compression.threshold`、`compression.target_ratio`、`compression.protect_last_n`、`compression.protect_first_n`。

## 设计决策

### 采用方案

Phase 7 Batch 1 只实现 deterministic `ContextCompressor` 和 preflight compression。

原因：

- 对齐真实 Hermes 的压缩入口：provider 调用前先估算并压缩。
- 不需要真实 provider，也不需要辅助 summary model。
- 不提前引入 session resume、gateway、memory 或 compression lock。
- 现有 `AIAgent.run_conversation()` 已有 `max_iterations` 循环，暂不急着抽 `IterationBudget`。

### 暂不采用

不在 Batch 1 实现 parent-child session split。

真实 Hermes 会在压缩时结束旧 session、创建 child session，并通过 `parent_session_id` 维护链路。但当前学习项目还没有 session resume 和 session list projection，过早实现会让 Phase 7 失焦。

## 最小数据流

1. CLI 传入 `history` 和当前 user input。
2. `AIAgent.run_conversation()` 将 user message append 到 working messages。
3. provider 调用前执行 `_maybe_compress_messages()`。
4. compressor 粗略估算 messages + system prompt 的 token 数。
5. 如果低于阈值，原样发送。
6. 如果超过阈值：
   - 保留前 `protect_first_n` 条消息。
   - 保留后 `protect_last_n` 条消息。
   - 中间消息合并成一条 deterministic summary message。
7. provider 使用压缩后的 working messages。
8. 返回给 CLI 的 messages 也是压缩后的 working messages，避免下一轮重复携带已丢弃的中间窗口。

## Batch 1 配置

学习项目使用比真实 Hermes 更小的配置面：

```yaml
compression:
  enabled: true
  context_length: 2000
  threshold: 0.50
  protect_first_n: 2
  protect_last_n: 6
```

说明：

- `context_length` 是学习项目的粗略上下文窗口，真实 Hermes 会从 provider/model metadata 推断。
- `threshold` 乘以 `context_length` 得到触发压缩的阈值。
- `protect_first_n` 和 `protect_last_n` 对齐真实 Hermes 的命名。

## 后续批次

Batch 2：

- 抽出 `IterationBudget`，只用于 `max_iterations` 循环。
- 保持语义为“provider/tool loop 次数预算”。

Batch 3：

- 扩展 `SessionStore`：
  - `parent_session_id`
  - `end_reason`
  - compression continuation session
- 实现压缩后 session split。

Batch 4：

- 考虑真实 provider usage、auxiliary compression model、LLM summary。

## 验收方式

- 构造一段长 history。
- 设置较小 `compression.context_length` 或较低 `threshold`。
- 运行 `chat --show-messages`。
- 观察输出中出现一条 summary message。
- 确认 fake provider 仍返回 final assistant message。
- 确认不会写入 system role message 到普通 messages。
