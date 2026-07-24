# Provider Reliability and Targeted OpenAI-Compatible Profiles Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为现有 Provider lifecycle 增加结构化错误、retry/backoff、限流语义、stale-stream watchdog 和 partial continuation，并首批支持 OpenRouter、Azure OpenAI v1 与 vLLM/local。

**Architecture:** Client 只负责 HTTP/SSE I/O；request lifecycle 负责单 binding 的错误分类、重试和流监督；`AIAgent` 持有每个 binding 的动态状态、fallback 和 continuation；Transport 保持无状态并继续负责协议标准化。

**Tech Stack:** Python 3.11、stdlib `urllib` / `threading` / `queue` / `email.utils`、dataclass、现有 Provider Runtime / Transport / AIAgent。

---

## 实施约束

- 源码由用户手工抄写，Codex 一次只提供一个小步骤。
- Codex 可以直接维护设计、实施计划和进度文档。
- 默认不新增测试文件，验证使用 `compileall`、一次性脚本、CLI 和接口不变量。
- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不实现 credential rotation，只保留 `should_rotate_credential` 决策字段。
- 不实现完整 Azure Foundry、Entra ID、Responses API、Gemini、Ollama 或任意端点自动兼容。
- 每个 Task 开始前先核对当前代码；如果用户手抄结果与计划不一致，先说明差异。

### Task 1: Provider 错误原语

**Files:**

- Create: `src/learn_hermes_agent/providers/errors.py`

**Steps:**

1. 定义最小 `ProviderErrorKind`。
2. 定义继承 `RuntimeError` 的 `ProviderRequestError`。
3. 保存 status、筛选后的 headers、截断正文和原始错误链。
4. 定义 frozen `ProviderErrorDecision`。
5. 加入 `should_rotate_credential`，但不实现任何轮换行为。
6. 使用 import 和构造不变量验证类型。

**Acceptance:**

- 旧的 `except RuntimeError` 仍能捕获 Provider 错误。
- `repr` 和错误文本不包含 API Key。
- 分类所需状态码和 Header 不再被拼成不可恢复的普通字符串。

### Task 2: OpenAI-compatible 结构化 HTTP 错误

**Files:**

- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`

**Steps:**

1. 同步和流式路径统一提取 HTTP error body。
2. 将 `HTTPError` 转换为 `ProviderRequestError`，保留状态码和允许的 Header。
3. 将 URL、timeout 和连接错误保留为有原因链的结构化错误。
4. 截断并脱敏公开错误正文。
5. SSE 顶层 `error` payload 转换为同一错误类型。
6. 使用替换 `urlopen` 的一次性脚本验证 401、429、503 和 SSE error。

**Acceptance:**

- 同步与流式错误具有相同结构。
- 调用方无需解析 `"HTTP 429"` 字符串获得状态码。
- response 和 error response 都会被关闭。

### Task 3: 错误分类器

**Files:**

- Modify: `src/learn_hermes_agent/providers/errors.py`

**Steps:**

1. 实现状态码优先的 `classify_provider_error()`。
2. 区分 auth、billing、rate limit、upstream rate limit、overload、timeout、server、context、format 和 unknown。
3. 根据响应正文区分 500/502 validation error 与可重试 server error。
4. 识别 vLLM 常见 context-length 文本。
5. 识别 OpenRouter 包装的 upstream 429。
6. 使用表驱动的一次性脚本打印并核对分类矩阵。

**Acceptance:**

- 401/402/403 不进行同 key 重试。
- OpenRouter upstream 429 优先 fallback，且不建议切换凭证。
- overload、timeout 和普通 server error 可重试。
- unknown 不会被错误标记为无限重试。

### Task 4: RetryPolicy、Retry-After 与 jitter backoff

**Files:**

- Create: `src/learn_hermes_agent/providers/retry.py`

**Steps:**

1. 定义 frozen `RetryPolicy` 和字段校验。
2. 实现 `Retry-After` 秒数解析。
3. 实现 HTTP-date 形式的 `Retry-After`。
4. 实现带 jitter 的指数退避和最大等待上限。
5. 将 sleep、随机源和时钟设计为可注入依赖。
6. 使用 no-op sleep 一次性脚本验证等待序列。

**Acceptance:**

- 默认是首次请求加最多两次重试。
- `Retry-After` 优先但不能超过 cap。
- 验证脚本不需要真实等待。

### Task 5: 单 Binding 请求重试编排

**Files:**

- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

**Steps:**

1. 为 `request_provider_completion()` 接入 `RetryPolicy`。
2. 每次失败先分类，再决定是否继续当前 binding。
3. 已产生可见输出时禁止直接重放完整请求。
4. 重试耗尽后抛出最后一个结构化错误。
5. `AIAgent` 只在 binding 请求生命周期结束后尝试 fallback。
6. 使用 scripted client 验证一次失败后成功、非重试错误和预算耗尽。

**Acceptance:**

- 单 binding 不超过 `max_attempts`。
- retry 与 fallback 不形成乘法嵌套。
- `TypeError`、`AssertionError` 等代码错误仍不被伪装成 Provider fallback。

### Task 6: OpenRouter、Azure OpenAI v1 与 custom 本地端点 aliases

**Files:**

- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/__init__.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`
- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/cli/main.py`

**Steps:**

1. 为 Profile 增加带兼容默认值的静态 Header、必填地址和可选认证字段；
   本地属性不放在 Profile，由实际 URL 判断。
2. 注册 `openrouter`。
3. 注册 `azure-openai`，要求用户提供 `/openai/v1` base URL。
4. 注册 canonical `custom`，aliases 为 `ollama`、`local`、`vllm`。
5. Custom 不提供默认地址或固定 Key 环境变量；地址必填，API Key 可选；
   显式配置 `api_key_env` 后，对应环境变量必须存在。
6. 配置归一化保留 `base_url` 和 `api_key_env` 未配置状态，
   由 Profile 解析 Provider 专属默认值。
7. `doctor` 显示 Profile 解析后的有效地址、环境变量和凭据状态。
8. Client 只在凭据存在时发送 Authorization，作为防止空 Bearer
   Header 的底层防御。
9. 保持 `azure` 名称未注册，为后续完整 Azure Foundry 保留迁移空间。
10. 验证 alias、必填地址、可选环境变量、凭据隔离和 Header。

**Acceptance:**

- OpenRouter 使用独立 API Key 环境变量。
- Azure OpenAI v1 不接受旧 deployment URL 作为已支持形态。
- `provider: ollama`、`local`、`vllm` 均解析为 canonical `custom`。
- Custom 未显式配置地址时在网络请求前失败。
- Custom API Key 可选；没有凭据时不发送 Authorization，也不继承
  OpenAI 或 OpenRouter 的凭据。
- Custom 显式配置 `api_key_env` 但环境变量缺失时提前失败。

### Task 7: ProviderBindingState 与 streaming 动态降级

**Files:**

- Create: `src/learn_hermes_agent/providers/state.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/providers/request.py`

**Steps:**

1. 定义可变 `ProviderBindingState`。
2. `AIAgent` 为每个 binding 建立独立状态。
3. 已禁用 streaming 的 binding 自动走同步路径。
4. `stream=True` 返回完整响应时直接使用该响应，不重复发请求。
5. 将该 binding 标记为后续禁用 streaming。
6. Provider 明确拒绝 streaming 时，按分类结果降级为同步。
7. 验证两个 binding 的状态互不污染。

**Acceptance:**

- 完整响应不会因为 streaming 探测而重复计费。
- 动态状态不修改 frozen `ProviderRuntime`。
- Transport 仍然无状态。

### Task 8: Stale-stream supervisor 原语

**Files:**

- Create: `src/learn_hermes_agent/providers/stream_supervisor.py`

**Steps:**

1. 定义 frozen `StreamReliabilityPolicy`。
2. 定义 queue item 的 event、done 和 error 状态。
3. 使用 daemon producer thread 消费原始 iterator。
4. 调用线程使用带 timeout 的 queue 读取。
5. stale 时标记 attempt 取消并 best-effort 关闭流。
6. 迟到事件通过 attempt fence 丢弃。
7. 使用短 timeout 的阻塞 iterator 一次性验证，不强杀线程。

**Acceptance:**

- callback 不在 producer thread 执行。
- stale 后调用线程在有界时间内返回错误。
- 旧 attempt 不能把事件写入新请求。

### Task 9: Watchdog 接入请求生命周期

**Files:**

- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`
- Modify: `src/learn_hermes_agent/providers/state.py`

**Steps:**

1. streaming iterator 经过 supervisor 后再进入 accumulator。
2. OpenAI-compatible 流暴露可选 `close()`。
3. stale 无可见输出时进入普通 retry。
4. 成功完成流时清零 `consecutive_stale_streams`。
5. stale 时增加计数。
6. 达到阈值后禁用该 binding 的 streaming。
7. 根据 binding 的实际 base URL 判断本地端点；本地 URL 使用本地
   timeout，远程 custom URL 使用云端 timeout。

**Acceptance:**

- watchdog 不改变同步路径。
- 成功请求能够恢复 stale streak。
- 达到 giveup 阈值后的下一轮使用同步请求。

### Task 10: Partial stream snapshot 与 stub

**Files:**

- Modify: `src/learn_hermes_agent/providers/streaming.py`
- Modify: `src/learn_hermes_agent/providers/types.py`
- Modify: `src/learn_hermes_agent/providers/transports/chat_completions.py`

**Steps:**

1. 定义 `PARTIAL_STREAM_STUB_ID`。
2. accumulator 暴露只读 partial snapshot。
3. 文本断流时构造 `finish_reason="length"` 的 partial raw response。
4. 在 `NormalizedResponse.provider_data` 中保留 partial marker。
5. 残缺 tool call 只记录 marker，不进入可执行 `tool_calls`。
6. 保留 reasoning 和 usage 的现有标准化语义。
7. 一次性验证 partial response 能通过 Transport 且无法触发工具执行。

**Acceptance:**

- partial stub 可被 Agent 明确识别。
- 不完整工具参数不会进入 ToolExecutor。
- 普通完整 response 的标准化结果不变。

### Task 11: AIAgent partial continuation

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/agent/messages.py`

**Steps:**

1. 识别 `PARTIAL_STREAM_STUB_ID`。
2. 将 partial assistant 内容加入对话上下文。
3. 加入内部 continuation instruction。
4. 文本续写要求从停止位置继续且不重复。
5. partial tool call 要求从头重新生成，并说明旧调用未执行。
6. 使用独立 `continuation_attempts`，默认最多 4 次。
7. 达到上限时保留 partial 文本；只有残缺工具调用时抛错。
8. 验证 callback 只收到新 delta，Agent iteration budget 仍然生效。

**Acceptance:**

- 断流后不会重放原始完整请求。
- partial tool call 永远不会执行。
- continuation 不能形成无限循环。

### Task 12: 目标端点兼容行为收口

**Files:**

- Modify: `src/learn_hermes_agent/providers/streaming.py`
- Modify: `src/learn_hermes_agent/providers/openai_compatible.py`
- Modify: `src/learn_hermes_agent/providers/transports/chat_completions.py`
- Modify: `src/learn_hermes_agent/providers/errors.py`

**Steps:**

1. 保留 usage-only chunk。
2. 保留 `reasoning_content` / `reasoning`。
3. 保留整数 tool-call ID 和重复完整工具名兼容。
4. 严格拒绝完全缺少 choices 的普通成功响应。
5. 无 finish reason 的内容流统一进入 partial 路径。
6. 核对 OpenRouter upstream 429、Azure URL 和 vLLM context error。
7. 不加入 Gemini、Ollama 或宽松 JSON 修复。

**Acceptance:**

- 兼容行为只覆盖设计列出的目标。
- 损坏响应不会因“兼容”而静默成功。

### Task 13: Reliability 配置接线

**Files:**

- Modify: `src/learn_hermes_agent/providers/retry.py`
- Modify: `src/learn_hermes_agent/providers/stream_supervisor.py`
- Modify: `src/learn_hermes_agent/cli/main.py`
- Modify: project configuration example if one already exists

**Steps:**

1. 从 `model.reliability` 构造两类 policy。
2. 缺失配置时使用设计默认值。
3. 拒绝 bool 冒充数字。
4. `max_attempts=1` 关闭 retry。
5. `stale_timeout_seconds=0` 关闭 watchdog。
6. `continuation_max_attempts=0` 关闭 continuation。
7. 将 policy 显式传入 `AIAgent`，不使用模块级可变全局状态。

**Acceptance:**

- 旧配置无需修改即可运行。
- 非法配置得到明确错误或一致的安全默认值。
- 回滚值能够恢复接近旧版的请求行为。

### Task 14: 全链路轻量验证与文档收尾

**Files:**

- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`

**Steps:**

1. 运行 `uv run python -m compileall -q src`。
2. 验证错误分类和 retry/backoff 矩阵。
3. 验证 401、429、503、`Retry-After` 和 fallback。
4. 验证 watchdog、attempt fence、stale streak 和同步降级。
5. 验证 partial text continuation 无重复。
6. 验证 partial tool call 不执行。
7. 替换 `urlopen` 验证三个目标端点的 URL、认证和响应行为。
8. 回归 `doctor`、工具 definitions、普通 fake chat、tool demo 和 usage。
9. 运行 `git diff --check`。
10. 更新进度文档，并记录 credential rotation 与完整 Azure Foundry 仍未实现。

**Acceptance:**

- 不新增测试文件。
- 所有轻量验证均有可观察结果。
- 同步默认路径和现有工具链无回归。
- 文档与实际实现边界一致。

## 执行顺序

严格按 Task 1 到 Task 14 执行。每次只提供一个小步骤，用户确认“好了”“下一步”或要求验证后再继续。
