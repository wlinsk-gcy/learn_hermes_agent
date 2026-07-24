# Provider Reliability and Targeted OpenAI-Compatible Profiles 设计

## 背景

当前 Provider 主链已经完成同步与基础 streaming：

```text
ProviderProfile
  -> ProviderRuntime
  -> ProviderBinding(runtime, client)
  -> AIAgent
  -> ProviderTransport.build_kwargs()
  -> request_provider_completion()
  -> ProviderClient.create()
  -> ChatCompletionStreamAccumulator
  -> ProviderTransport.normalize_response()
  -> NormalizedResponse
```

现有实现能够消费 SSE、拼接文本和工具调用，并在已经显示内容后阻止直接 fallback。但它仍缺少：

- 结构化 HTTP / 网络错误；
- retry/backoff 和 `Retry-After`；
- 429 限流语义区分；
- stale-stream watchdog；
- 断流后的 partial continuation；
- OpenRouter、Azure OpenAI v1 和 vLLM/local 的明确 Profile。

本设计对齐参考仓库 HEAD `477c08b44766ace8b890faa72bf82ecbcf2b3ba8` 的以下设计意图：

- `agent/error_classifier.py`：先分类，再决定重试、凭证恢复或 fallback；
- `agent/retry_utils.py`：带 jitter 的指数退避和有上限的等待；
- `agent/chat_completion_helpers.py`：stale-stream 监控、attempt 隔离、流式降级和 partial stub；
- `agent/conversation_loop.py`：Provider 顺序、continuation 和最终 fallback；
- `providers/base.py`：Profile 只声明静态 Provider 差异，不拥有 Client、credential rotation 或 streaming 生命周期；
- `plugins/model-providers/openrouter/`：OpenRouter Profile；
- `plugins/model-providers/azure-foundry/` 与 `run_agent.py::_is_azure_openai_url()`：完整 Azure Foundry 与 Azure OpenAI v1 是不同范围；
- Local / Custom Endpoint：vLLM 作为本地 OpenAI-compatible 端点使用。

## 目标

本批建立以下可靠性链路：

```text
AIAgent fallback
  -> per-binding request lifecycle
     -> structured error classification
     -> retry/backoff
     -> stream supervision
     -> partial continuation signal
  -> ProviderClient
```

完成后应满足：

- 单个 ProviderBinding 使用有界重试，不与 fallback 预算相乘；
- 429 能区分账户限流、服务过载和 OpenRouter 上游限流；
- 流长时间无有效事件时不会永久阻塞；
- 已经产生文本的断流通过 continuation 继续，不重放完整回答；
- 残缺工具调用永远不会执行；
- 首批明确支持 OpenRouter、Azure OpenAI v1 和 vLLM/local；
- credential rotation 暂不实现，但保留分类决策接口。

## 决策简报

### 方案 A：全部逻辑放进 OpenAICompatibleClient

优点是文件少。缺点是 Client 不知道 fallback 顺序、可见输出、continuation 或每个 binding 的会话状态，最终会同时承担 HTTP、重试和 Agent 编排。

### 方案 B：独立可靠性层，AIAgent 持有动态状态

Client 保留 HTTP/SSE I/O；错误分类、重试、watchdog 和 partial snapshot 位于 Provider request lifecycle；AIAgent 持有 binding 状态、fallback 和 continuation。

该方案与 Hermes 的职责边界一致，也能在以后接入其他协议时复用。

### 方案 C：一次复制 Hermes 的完整 credential/failover 系统

优点是短期功能最多。缺点是会同时引入 CredentialPool、刷新、持久 cooldown、更多 Provider 协议和复杂外层恢复，超过当前学习边界。

### 结论

采用方案 B。credential rotation 延后到独立批次。

## 支持范围

### OpenRouter

- Profile 名称：`openrouter`
- API Key：`OPENROUTER_API_KEY`
- 默认地址：`https://openrouter.ai/api/v1`
- Bearer 认证
- 识别 OpenRouter 包装的上游 429，并优先 fallback

### Azure OpenAI v1

- Profile 名称：`azure-openai`
- API Key：`AZURE_OPENAI_API_KEY`
- 用户提供形如 `https://<resource>.openai.azure.com/openai/v1` 的 base URL
- 仅支持 API Key + Chat Completions
- 暂不使用 `azure` 别名；最新版 Hermes 将该别名保留给完整 `azure-foundry`

本批不支持旧 deployment URL、`api-version`、Entra ID、Responses API 或 Azure Anthropic。

### Custom / Ollama / local / vLLM

- canonical Profile：`custom`
- aliases：`ollama`、`local`、`vllm`
- 不提供默认地址；用户必须配置 OpenAI-compatible base URL
- API Key 可选；不设置固定 Key 环境变量
- 用户显式配置 `api_key_env` 时读取对应环境变量
- 已配置 `api_key_env` 但对应变量缺失时提前报错
- 是否为本地端点根据实际 base URL 判断，不使用静态 Profile 标记
- 本地 URL 使用本地 stale timeout
- 识别 vLLM 常见的上下文长度错误，不进行无意义重试

Provider 专属默认地址和 API Key 环境变量由 `ProviderProfile`
解析。配置归一化层必须保留字段“未配置”的状态，不能把
OpenAI 的默认地址或 `OPENAI_API_KEY` 注入 OpenRouter、
Azure OpenAI v1 或 custom 端点。Custom 没有固定地址或固定
Key 环境变量；没有配置凭据时，Client 不发送 Authorization。
本项目 Client 不受 OpenAI SDK 的非空 Key 限制，因此不复制
Hermes 内部的 `no-key-required` 占位值。

## 组件设计

### 1. 结构化错误

新增 `providers/errors.py`：

```text
ProviderErrorKind
ProviderRequestError
ProviderErrorDecision
classify_provider_error()
```

`ProviderRequestError` 继续继承 `RuntimeError`，并保存：

- `status_code`
- 经过筛选的 `response_headers`
- 截断和脱敏后的 `response_body`
- 原始异常链

分类优先级：

1. 明确的状态码、Header 和响应正文；
2. Provider-specific 信号；
3. 已知错误文本；
4. timeout、socket 和连接异常；
5. 未知错误采用保守决策。

核心语义：

| 场景 | retry | fallback | rotate advisory |
| --- | --- | --- | --- |
| 401 / 403 auth | 否 | 是 | 是 |
| 402 billing | 否 | 是 | 是 |
| 429 account rate limit | 是 | 是 | 是 |
| OpenRouter upstream 429 | 否 | 是 | 否 |
| overload 429 / 503 / 529 | 是 | 是 | 否 |
| 408 / timeout / connection | 是 | 是 | 否 |
| 500 / 502 / 504 server error | 是 | 是 | 否 |
| 500 / 502 request validation | 否 | 是 | 否 |
| context / format error | 否 | 视情况 | 否 |

`should_rotate_credential` 只是决策字段。本批没有 rotator 时，它不会改变当前 API Key。

### 2. Retry/backoff

新增 `providers/retry.py`：

- frozen `RetryPolicy`
- `parse_retry_after()`
- `jittered_backoff()`
- 单 binding 请求循环

默认总尝试次数为 3，即首次请求加最多 2 次重试。等待时间采用带 jitter 的指数退避：

```text
约 2 秒 -> 约 4 秒 -> 上限 60 秒
```

服务端 `Retry-After` 优先于本地计算，同时仍受最大等待时间约束。sleep 和随机源可注入，以便一次性脚本无需真实等待。

同一个错误不能同时触发 request helper 内层无限重试和 AIAgent 外层重试。单 binding 只有一个明确预算，耗尽后才交给 AIAgent fallback。

### 3. Binding 动态状态

新增 `ProviderBindingState`，由 `AIAgent` 按 binding 顺序持有：

```python
streaming_disabled: bool = False
stream_options_disabled: bool = False
consecutive_stale_streams: int = 0
```

动态状态不能写入 frozen `ProviderRuntime`，也不能写入无状态 Transport。

当 `stream=True` 返回一份完整响应时：

1. 直接使用该响应，不重复发起同步请求；
2. 将该 binding 的 `streaming_disabled` 设为 `True`；
3. 后续请求使用同步模式。

### 4. Stale-stream watchdog

新增 `providers/stream_supervisor.py`。

```text
Provider stream iterator
  -> daemon producer thread
  -> per-attempt Queue
  -> caller thread accumulator/callback
```

后台线程只读取流和投递事件。callback 与 accumulator 始终在调用线程执行，避免跨线程并发修改。

每次请求拥有独立 attempt id 和取消状态。stale 后到达的旧事件会被丢弃，不能写入下一次请求。

默认值：

- 云端：180 秒；
- 本地：900 秒；
- 推理或大上下文请求可提高到 240 至 300 秒；
- 显式配置优先。

成功完成流后 stale 计数清零。连续 stale 达到 5 次时，当前 binding 在本 Agent 会话中禁用 streaming。

Python 不能安全强杀线程。超时时采用：

1. 标记 attempt 取消；
2. best-effort 调用流对象的 `close()`；
3. 抛出结构化 stale-stream 错误；
4. 使用 attempt fence 丢弃迟到事件。

### 5. Partial continuation

引入 `PARTIAL_STREAM_STUB_ID`，与 Hermes 的 partial stub 意图一致。

无可见内容时断流：

```text
classify -> 同 binding 有界重试
```

已有文本时断流：

```text
partial assistant response
  -> finish_reason="length"
  -> append internal continuation instruction
  -> next Provider request
```

continuation 只输出新 delta，不重新触发已有文本 callback。最多续写 4 次，并继续受 Agent iteration budget 约束。

残缺工具调用：

- 不执行；
- 不伪造成完整 JSON；
- 标记 `partial_tool_call=True`；
- 下一次请求明确要求从头生成完整工具调用；
- 在提示中说明旧工具调用从未执行。

续写耗尽时：

- 有文本则保留部分文本，`finish_reason="length"`；
- 只有残缺工具调用则抛错。

### 6. OpenAI-compatible 安全兼容

公共解析器只接受可以安全统一处理的差异：

- usage-only `choices=[]`；
- `reasoning_content` 与 `reasoning`；
- 整数 tool-call ID 转字符串；
- 重复完整工具名使用覆盖而不是拼接；
- SSE EOF 前缺少最后空行；
- SSE 顶层 `{"error": ...}` 转结构化错误；
- 有内容但缺少 `finish_reason` 进入 partial continuation；
- 完全缺少 `choices` 仍然报错。

本批不加入 Ollama 专有 index 复用、Gemini stream options 或宽松的任意 JSON 修复。

## 配置

建议配置：

```yaml
model:
  reliability:
    max_attempts: 3
    backoff_base_seconds: 2.0
    backoff_cap_seconds: 60.0
    stale_timeout_seconds: 180.0
    local_stale_timeout_seconds: 900.0
    stale_giveup_threshold: 5
    continuation_max_attempts: 4
```

关闭方式：

- `max_attempts: 1`：关闭单 binding 重试；
- `stale_timeout_seconds: 0`：关闭 watchdog；
- `continuation_max_attempts: 0`：关闭 partial continuation。

## 风险与保护

- 无可见输出时重试仍可能产生重复计费，因此必须有界。
- `Retry-After` 可能异常大，必须限制上限。
- continuation 只能通过提示降低重复文本概率，不能数学保证模型不重复。
- 阻塞 socket 可能无法立刻被 `close()` 打断，因此必须使用 daemon thread、attempt fence 和 stale giveup。
- 错误正文可能含敏感信息，日志只能使用截断、脱敏后的文本。
- Azure 名称容易混淆，本批明确使用 `azure-openai`，不给不完整实现注册 `azure` 别名。

## 非目标

本批不实现：

- credential pool、credential rotation、refresh 或 cooldown 持久化；
- 完整 Azure Foundry、Entra ID、Responses API 或 Anthropic Messages；
- Gemini、Ollama 或所有第三方 OpenAI-compatible 端点；
- HTTP 字节级断点续传；
- 强制终止 Python 线程；
- 新测试文件。

## 验证方式

默认不新增测试文件，使用：

1. `uv run python -m compileall -q src`
2. Profile canonical name、alias、认证 Header 和 Runtime secret repr 不变量。
3. 注入 no-op sleep 的一次性脚本验证 429、`Retry-After`、401 和 fallback。
4. 短 stale timeout 的一次性流验证 watchdog、attempt fence 和 streaming 降级。
5. partial text 验证 callback 无重复、continuation 有上限。
6. partial tool call 验证 ToolExecutor 没有收到调用。
7. 替换 `urlopen` 验证 OpenRouter、Azure OpenAI v1 和 custom
   OpenAI-compatible 请求，不发送真实网络请求。
8. 回归 `doctor`、普通 fake chat、tool demo、usage 和 schema。
9. `git diff --check`

## 验收标准

- 错误信息在进入 retry/fallback 前保持结构化。
- 每个 binding 不超过配置的总请求次数。
- OpenRouter upstream 429 不进行同 key 盲目重试。
- Azure OpenAI v1 不被描述成完整 Azure Foundry。
- `ollama`、`local` 和 `vllm` 解析为 canonical `custom`。
- Custom 必须显式配置 base URL，API Key 可选，且不会继承
  `OPENAI_API_KEY`。
- stale stream 不会永久阻塞主线程。
- 已显示文本不会被完整请求重放。
- 残缺工具调用永远不会执行。
- 现有同步、fake、tool calling、usage 和 fallback 行为保持兼容。
