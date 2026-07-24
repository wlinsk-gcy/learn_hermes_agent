# Provider Streaming Request Lifecycle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为现有 Provider Runtime 增加可选的 Chat Completions 流式请求、增量回调和完整原始响应重建，同时保持 Transport 与 fallback 职责不变。

**Architecture:** `ProviderClient.create(stream=True)` 返回原始 chunk iterator；独立 request helper 使用 accumulator 消费并拼装完整 Chat Completions 响应；`AIAgent` 选择同步或流式请求并管理 fallback；最终响应仍由现有 Transport 校验和标准化。

**Tech Stack:** Python 3.11、stdlib `urllib` / `json`、dataclass、iterator、现有 Provider Runtime / Transport。

---

## 实施约束

- 源码由用户手工抄写，Codex 一次只提供一个小步骤。
- Codex 可以直接维护本设计、实施计划和进度文档。
- 默认不新增测试文件。
- 不实现 Gemini、Anthropic、Codex Responses、credential pool、复杂 retry、跨线程 interrupt 或 stale-stream watchdog。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。

### Task 1: Streaming 错误与回调原语

**Files:**

- Create: `src/learn_hermes_agent/providers/streaming.py`

**Steps:**

1. 定义 `StreamCallback = Callable[[str], None]`。
2. 定义 frozen `ProviderStreamCallbacks`，包含三个可选回调。
3. 为三个回调提供 best-effort emit helper，并返回回调是否成功接收事件。
4. 定义 `ProviderStreamError(RuntimeError)`，保存 `text_emitted`。
5. 使用 `compileall` 和一次性 import 检查验证接口。

**Acceptance:**

- 回调缺失或回调自身报错时不会向上传播。
- `ProviderStreamError(..., text_emitted=True)` 可被调用方可靠识别。

### Task 2: 文本、reasoning 与 usage 累加器

**Files:**

- Modify: `src/learn_hermes_agent/providers/streaming.py`

**Steps:**

1. 新增 `ChatCompletionStreamAccumulator`。
2. 校验每个 chunk 是字典。
3. 接受没有 choices 的 usage-only chunk。
4. 从 `choices[0].delta` 累加 `content`、`reasoning_content` / `reasoning`。
5. 记录 model、finish reason、usage 和是否收到有效事件。
6. 文本和 reasoning 到达时触发相应回调。
7. 用一次性脚本验证两段文本拼成一个最终值。

**Acceptance:**

- 文本和 reasoning 顺序稳定。
- usage-only chunk 不被当作空流。
- callback 异常不破坏 accumulator。

### Task 3: Tool-call 分片与最终原始响应

**Files:**

- Modify: `src/learn_hermes_agent/providers/streaming.py`

**Steps:**

1. 按 tool-call `index` 建立累加槽位。
2. id 使用最新非空值。
3. function name 使用赋值，arguments 使用追加。
4. 首次得到完整 tool name 时触发 `on_tool_call_started`。
5. 实现 `build_response()`，返回完整原始 Chat Completions 字典。
6. 空流抛出 `ProviderStreamError`。
7. 没有 finish reason 的流抛出 `ProviderStreamError`。
8. 一次性脚本验证多 tool call 和 arguments 跨 chunk 拼接。

**Acceptance:**

- 最终字典可直接通过 `ChatCompletionsTransport.validate_response()` 和 `normalize_response()`。
- 不完整流不会被默认伪装为 `finish_reason="stop"`。

### Task 4: Fake Client 流式原始 chunk

**Files:**

- Modify: `src/learn_hermes_agent/providers/fake.py`

**Steps:**

1. 保留现有同步原始响应生成逻辑。
2. 当 `request_kwargs["stream"] is True` 时，将当前完整 fake 响应转换为 chunk iterator。
3. 文本至少拆成两个 chunk，便于观察顺序。
4. tool calls 以 Chat Completions delta 形状输出。
5. finish reason 在终止 chunk 输出，usage 如存在则使用 usage-only chunk 输出。
6. 验证普通 fake 和 tool demo 的同步行为不变。

**Acceptance:**

- Fake Client 不直接返回 `NormalizedResponse`。
- 同一个 scripted response 可分别用于同步和流式路径。

### Task 5: Provider Request Helper

**Files:**

- Create: `src/learn_hermes_agent/providers/request.py`

**Steps:**

1. 新增统一 `request_provider_completion()`。
2. 无 callbacks 时直接调用 `binding.client.create()`。
3. 有 callbacks 且 `api_mode="chat_completions"` 时加入 `stream=True` 和 usage stream options。
4. 校验返回值是 iterator，而不是完整字典。
5. 使用 `ChatCompletionStreamAccumulator` 消费所有 chunk。
6. 将底层迭代错误包装为保留 `text_emitted` 的 `ProviderStreamError`。
7. 未实现的 api mode 明确报错。

**Acceptance:**

- Request helper 不导入 `ChatMessage`、`NormalizedResponse` 或 ToolRegistry。
- Request helper 不管理 fallback。

### Task 6: AIAgent 流式编排与 fallback 边界

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`

**Steps:**

1. `run_conversation()` 新增可选 `stream_callback`。
2. 本轮有 callback 时构造 `ProviderStreamCallbacks`。
3. `_complete_with_fallback()` 改为调用 `request_provider_completion()`。
4. 同步路径保持不变。
5. `ProviderStreamError.text_emitted is False` 时允许尝试 fallback。
6. `text_emitted is True` 时立即重新抛出，禁止把 fallback 文本接到已显示的 partial text 后。
7. 验证现有 primary/fallback 可观察字段语义不变。

**Acceptance:**

- 不传 callback 时，现有 chat、tool demo 和 usage 行为不变。
- 传 callback 时，最终 messages 仍只追加一次完整 assistant message。

### Task 7: OpenAI-compatible SSE I/O

**Files:**

- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`

**Steps:**

1. 提取复用的 HTTP request 构造逻辑。
2. 保留非流式完整 JSON 读取。
3. `stream=True` 时返回惰性 SSE iterator。
4. iterator 负责 response 生命周期。
5. 按 SSE 空行分隔 event，聚合一个或多个 `data:` 行。
6. 忽略注释，识别 `[DONE]`。
7. 将每个 data event 解析为字典 chunk。
8. HTTP、网络、timeout 和 JSON 错误继续转换为 `RuntimeError`。
9. 使用替换 `urlopen` 的一次性脚本验证请求 body、headers、chunk 和关闭行为，不发送真实网络请求。

**Acceptance:**

- `ProviderClient` Protocol 无需增加第二个 stream 方法。
- Client 不导入 Agent message 或 normalized response 类型。

### Task 8: 全链路轻量验证与文档收尾

**Files:**

- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`

**Steps:**

1. 运行 `uv run python -m compileall -q src`。
2. 手工验证 Fake 文本流的 callback 顺序和最终消息。
3. 手工验证 Fake tool demo 的分片聚合与 tool result 配对。
4. 手工验证 usage-only chunk。
5. 手工验证空流和无 finish reason 流。
6. 手工验证 callback 前失败可 fallback。
7. 手工验证 callback 后失败不 fallback。
8. 回归 `doctor`、普通 fake chat、tool demo 和 schema。
9. 运行 `git diff --check`。
10. 更新三份进度文档，将下一步指向 Gemini Provider 设计。

**Acceptance:**

- 不新增测试文件。
- 同步默认路径向后兼容。
- Streaming 最终仍进入同一个 Transport。
- 文档准确记录尚未实现的 interrupt、retry 和 Provider 原生协议。

## 执行顺序

严格按 Task 1 到 Task 8 执行。每次只给一个小步骤，用户确认“好了”或要求验证后再继续。
