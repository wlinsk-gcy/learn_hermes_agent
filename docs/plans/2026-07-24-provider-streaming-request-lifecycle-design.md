# Provider Streaming Request Lifecycle 设计

## 背景

当前学习项目已经完成 Provider Transport / Runtime Foundation，主链为：

```text
ProviderProfile
  -> ProviderRuntime
  -> ProviderBinding(runtime, client)
  -> AIAgent
  -> ProviderTransport
  -> ProviderClient.create()
  -> NormalizedResponse
```

这条链路目前只支持同步 Chat Completions。`OpenAICompatibleClient` 会一次性读取完整 JSON，`AIAgent` 直接调用 Client，尚不能消费 SSE 增量、实时回调文本或拼接分片 tool calls。

参考 Hermes HEAD `477c08b44766ace8b890faa72bf82ecbcf2b3ba8`：

- `agent/conversation_loop.py` 决定本轮使用流式还是非流式请求；
- `agent/chat_completion_helpers.py::interruptible_streaming_api_call()` 管理流式请求生命周期；
- Chat Completions 流由 request helper 消费并拼装成完整的原始响应；
- 拼装后的响应继续经过现有 Transport 校验和标准化；
- Transport 不持有 Client、凭据、网络连接、重试或流生命周期。

## 目标

建立最小但边界正确的 Chat Completions streaming 主链：

```text
AIAgent
  -> Provider Request Helper
  -> ProviderClient.create(stream=True)
  -> raw Chat Completions chunks
  -> Stream Accumulator
  -> complete raw Chat Completions response
  -> ProviderTransport.validate_response()
  -> ProviderTransport.normalize_response()
  -> NormalizedResponse
```

本批完成后：

- `AIAgent.run_conversation()` 可选接收文本增量回调；
- 注册回调时使用 streaming，没有回调时保持现有同步行为；
- 文本、reasoning、tool-call arguments、finish reason 和 usage 可以从多个 chunk 中正确拼装；
- 空流和没有 finish reason 的残缺流不会被误认为成功；
- streaming 请求失败时仍遵守 primary/fallback 顺序；
- 已向用户发出部分文本后不自动切换 fallback，避免两份回答混在一起。

## 决策简报

### 方案 A：由 Client 完成全部 streaming

Client 负责 SSE、chunk 拼装、回调和最终响应。

优点是调用方简单。缺点是 Client 同时承担网络、协议状态和 Agent 展示逻辑，Gemini、Anthropic 加入后会形成多个不一致的生命周期实现。

### 方案 B：独立 Provider Request Helper

Client 只负责 HTTP/SSE I/O并返回原始 chunk；request helper 消费 chunk、触发回调并拼装完整原始响应；Transport 继续负责最终标准化。

优点是最接近 Hermes 的职责意图，现有同步链路和 Transport 可以复用，也为后续 Gemini Native Client facade 保留统一入口。

### 方案 C：由 Transport 管理 streaming

Transport 同时执行网络请求、消费事件并标准化。

这会破坏当前已经建立的无状态协议层边界，后续中断、连接关闭和 retry 都会进入 Transport。

### 结论

采用方案 B。

## 组件设计

### 1. Streaming primitives

新增 `providers/streaming.py`，定义：

- `ProviderStreamCallbacks`
  - `on_text_delta`
  - `on_reasoning_delta`
  - `on_tool_call_started`
- `ProviderStreamError`
  - 继承 `RuntimeError`
  - 保存 `text_emitted`，表示本次失败前是否已经成功发送可见文本

回调属于观察层。回调自身抛出的异常不能破坏 Provider 请求，因此采用 best-effort 调用。

### 2. Chat Completions stream accumulator

`ChatCompletionStreamAccumulator` 只接收原始字典 chunk，不依赖 `ChatMessage` 或 `NormalizedResponse`。

状态包括：

- `content_parts`
- `reasoning_parts`
- 按 tool-call `index` 聚合的 id、name、arguments 和额外字段
- `finish_reason`
- `model`
- `usage`
- 是否收到有效 chunk
- 是否已经成功发送可见文本

关键规则：

1. `content` 和 `arguments` 按顺序追加。
2. tool name 使用赋值，不使用字符串追加；部分兼容端点会在每个 chunk 重发完整名称。
3. usage-only chunk 可以没有 choices。
4. 空流抛出 `ProviderStreamError`。
5. 收到内容但最终没有 finish reason，视为残缺流并抛错，不能默认标记为 `stop`。
6. 最终输出仍是原始 Chat Completions 字典，而不是 `NormalizedResponse`。

### 3. Provider request helper

新增 `providers/request.py`。

它负责：

- 非流式时直接调用 `binding.client.create()`；
- 流式时加入 `stream=True`；
- Chat Completions 请求加入 `stream_options={"include_usage": True}`；
- 校验 Client 返回的是可迭代 chunk 流；
- 调用 accumulator 构造完整原始响应；
- 将底层迭代错误转换为带 `text_emitted` 状态的 `ProviderStreamError`。

该 helper 不做 Transport 标准化，不管理 fallback，也不选择 Provider。

### 4. Client streaming I/O

`OpenAICompatibleClient.create()` 保持单一入口：

- `stream` 不为 `True` 时，保持当前完整 JSON 行为；
- `stream=True` 时返回惰性 iterator；
- iterator 负责打开和关闭 HTTP response；
- 按 SSE event 聚合一个或多个 `data:` 行；
- 忽略空行与注释；
- `[DONE]` 结束迭代；
- 每个 JSON event 解析为原始字典 chunk。

使用同一个 `create(stream=True)` 入口与 Hermes 的 Chat Completions 调用方式保持一致，不新增 `stream()` Client 接口。

### 5. Agent orchestration

`AIAgent.run_conversation()` 新增可选 `stream_callback`。

`_complete_with_fallback()` 继续拥有 Provider 选择：

- 没有回调：使用原同步请求；
- 有回调：通过 request helper 发起流式请求；
- 流式错误发生在首个可见文本之前：允许尝试下一个 fallback；
- 已经发出可见文本：立即向上抛错，不再 fallback。

这样避免 primary 的半段文本与 fallback 的完整文本同时出现在一个终端输出中。

## 错误边界

- Client：HTTP 状态、网络、timeout、SSE 和 JSON 解码错误。
- Stream accumulator：chunk schema、空流、残缺流和 tool-call 分片聚合错误。
- Request helper：选择同步或流式调用，并保留 `text_emitted` 状态。
- AIAgent：Provider fallback 顺序和最终失败聚合。
- Transport：完整原始响应的校验和标准化。

## 非目标

本批不实现：

- Gemini Native、Anthropic Messages 或 Codex Responses；
- 跨线程 interrupt 和主动关闭 socket；
- stale-stream watchdog；
- 单 Provider retry、credential rotation 或健康状态机；
- superseded-stream single-writer fencing；
- CLI/TUI 完整流式渲染；
- partial response continuation；
- 新测试文件。

这些能力不能为了“看起来完整”而提前塞进本批。当前先建立后续 Provider 可以复用的正确边界。

## 兼容性

- `stream_callback` 默认为 `None`，现有调用继续走同步链路。
- `ProviderClient` 仍只有 `create(**request_kwargs)`。
- `ChatCompletionsTransport` 的公开接口不变。
- Fake 与 OpenAI-compatible 最终都向 Transport 提供相同的完整原始响应。
- 当前 tool calling、usage、checkpoint 和 session 行为不改变。

## 验证方式

默认不新增测试文件，使用：

1. `uv run python -m compileall -q src`
2. Fake 流手工验证文本 chunk 顺序和最终内容。
3. Fake 流手工验证 tool-call name/arguments 跨 chunk 拼接。
4. usage-only final chunk 手工验证。
5. 空流和无 finish reason 流手工验证错误。
6. 回调前失败允许 fallback，回调后失败禁止 fallback。
7. 现有 `doctor`、普通 fake chat 和 tool demo 回归。
8. `git diff --check`

## 后续方向

本批完成后再进入 Gemini：

```text
GeminiProfile
  -> GeminiNativeClient facade
  -> Gemini native request/stream conversion
  -> OpenAI-shaped raw chunks/final response
  -> 当前 request helper
  -> ChatCompletionsTransport
```

Gemini 不应绕过本批的 request lifecycle，也不应把原生协议转换塞进通用 Transport。
