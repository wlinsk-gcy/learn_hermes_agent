# Hermes 六 Provider 1:1 复刻设计

## 状态

- 日期：2026-07-24
- 学习项目分支：`feat/phase_10`
- Hermes 参考仓库：`D:\python-develop\project\hermes-agent`
- 参考基线：`a61183b56fdb45b9d2a0f2f6b8482e665ccf702f`
- 状态：已批准
- 本设计取代
  `2026-07-24-provider-reliability-openai-compatible-design.md`
  中尚未完成的目标 Provider 设计。
- 已经完成的 Provider Reliability Task 1 至 Task 7 Step 6 保留，
  新实施计划从当前代码继续，不重复实现。

## 目标

在学习项目中复刻 Hermes 当前 Model Provider 子系统，并只保证以下六个
内置 Provider 的完整行为：

1. OpenRouter
2. Azure Foundry
3. custom
4. Vertex
5. Alibaba
6. DeepSeek

“1:1”表示：

- Provider 名称、alias、环境变量、配置字段和默认端点一致；
- Profile 钩子、Runtime 解析、鉴权、协议路由和请求行为一致；
- 错误分类、retry、credential rotation、fallback、stream supervision 和
  partial continuation 一致；
- 模型发现、模型切换、CLI 配置、doctor、辅助调用和用量行为一致；
- 安全边界和凭据隔离一致；
- 不增加 Hermes 当前源码没有的推测性兼容。

## 固定约束

- 使用现有分支 `feat/phase_10`，不创建 worktree；
- 参考仓库只读；
- 源码由用户手工抄写，Codex 默认一次只提供一个小步骤；
- 文档可由 Codex 直接维护；
- 默认不新增测试文件；
- 不处理既有未跟踪目录 `sandbox/`；
- `ProviderRuntime` 保持 frozen，不保存动态状态。

## 不在范围内

- OpenRouter 图片生成；
- Desktop/Web UI；
- Gemini Provider；
- 独立 Anthropic Provider；
- 独立 OpenAI Codex Provider；
- Bedrock、Nous、OAuth Provider 等其他 Hermes Provider；
- Hermes 其余 Provider 的注册与 setup；
- 与六 Provider 无关的媒体、平台和 Gateway UI；
- 为任意未知第三方端点自动修复请求或响应。

虽然不注册独立 Anthropic、Codex 或 Gemini Provider，以下共享能力必须实现：

- `anthropic_messages` Transport：Azure Foundry Claude 端点需要；
- `codex_responses` Transport：Azure Foundry GPT-5/Codex/o 系列需要；
- Gemini thinking-config helper：Vertex OpenAI-compatible 请求需要。

## 当前差异

当前学习项目已经具有：

- frozen `ProviderProfile`；
- frozen `ProviderRuntime`；
- `ProviderBinding(runtime, client)`；
- `ProviderClient`；
- `ChatCompletionsTransport`；
- OpenAI-compatible HTTP/SSE Client；
- 结构化 Provider 错误；
- retry/backoff 与 `Retry-After`；
- 单 binding retry；
- `ProviderBindingState`；
- streaming 返回完整响应时直接采用并记忆降级。

与当前 Hermes 相比仍缺少：

- 可继承并带 Provider 钩子的完整 `ProviderProfile`；
- bundled/user/legacy Provider 插件发现；
- Hermes 的覆盖式注册和 alias 解析；
- `ProviderDef`、models.dev overlay 和模型目录；
- credential pool、cooldown、lease、rotation 和持久化；
- named custom provider；
- Vertex OAuth2/ADC/服务账号/刷新；
- Azure Foundry Entra ID；
- `anthropic_messages` 和 `codex_responses`；
- OpenRouter、custom、DeepSeek、Vertex 的请求专用钩子；
- DeepSeek `reasoning_content` 回放；
- 完整 stale-stream、attempt fence 和 partial continuation；
- 六 Provider 的 setup/model/doctor/auth/auxiliary/usage 接线。

当前的 `azure-openai` 是早期简化 Profile，不等于 Hermes 的
`azure-foundry`，必须迁移而不是继续扩展。

## 决策

采用“六 Provider 的 Hermes-compatible 完整子集”：

- 行为、配置、错误语义和职责严格对齐 Hermes；
- 只内置六个 Provider；
- 复用当前已经批准的
  `AIAgent -> request lifecycle -> Client -> Transport -> NormalizedResponse`
  分层；
- 不逐文件复制 Hermes 大型 `run_agent.py`；
- 文件布局允许适配当前 `src/learn_hermes_agent` 包结构；
- 每个有意布局差异都必须在实施步骤中说明，不能借布局差异改变行为。

没有采用的方案：

- 只增加六个静态 Profile：不能覆盖 Azure、Vertex、DeepSeek 等真实行为；
- 逐文件复制 Hermes：会连带引入其他 Provider、OAuth、Gateway 和大型
  兼容分支，破坏现有学习边界。

## 总体架构

```text
CLI / doctor / model switch / auxiliary tasks
        |
        v
Provider registry + ProviderProfile + model catalog
        |
        v
runtime resolver
  - config/env
  - credential pool
  - Vertex OAuth2
  - Azure API key / Entra ID
        |
        v
ProviderBinding
  - frozen ProviderRuntime
  - ProviderClient
  - mutable ProviderBindingState（由 AIAgent 持有）
        |
        v
per-binding request lifecycle
  - classification
  - retry/backoff
  - credential rotation
  - stream supervision
        |
        v
ProviderTransport
  - chat_completions
  - anthropic_messages
  - codex_responses
        |
        v
NormalizedResponse
        |
        v
AIAgent fallback / partial continuation / tool loop
```

### Profile

- 声明静态身份、元数据、默认端点和认证类型；
- 提供消息、请求参数、模型发现和模型上限钩子；
- 不保存 credential、retry 或 stream 动态状态。

### Runtime Resolver

- 根据显式参数、配置、环境变量和 credential pool 生成运行配置；
- 处理 Vertex 和 Azure Foundry 专用鉴权；
- 保证凭据只发送给匹配端点；
- 不发送模型请求。

### Client

- 构造 SDK/HTTP 请求；
- 负责 HTTP、JSON、SSE I/O；
- 将底层异常转换成结构化 Provider 错误；
- 不决定 retry、fallback 或 credential rotation。

### Transport

- 转换消息、工具和请求参数；
- 标准化响应、usage、reasoning 和 tool calls；
- 保持无状态。

### Request lifecycle

- 对单个 binding 分类、retry、backoff；
- 监督 streaming；
- 在安全时请求 credential rotation；
- 不跨 binding fallback。

### AIAgent

- 持有每个 binding 的动态状态；
- 选择 fallback；
- 处理 partial continuation；
- 保证残缺 tool call 永不执行。

## Provider Profile 与插件发现

`ProviderProfile` 对齐 Hermes 当前字段：

- `name`
- `api_mode`
- `aliases`
- `display_name`
- `description`
- `signup_url`
- `env_vars`
- `base_url`
- `models_url`
- `auth_type`
- `supports_health_check`
- `supports_vision`
- `supports_vision_tool_messages`
- `fallback_models`
- `hostname`
- `default_headers`
- `fixed_temperature`
- `default_max_tokens`
- `default_aux_model`

对齐的 Profile 钩子：

- `get_hostname()`
- `prepare_messages()`
- `build_extra_body()`
- `build_api_kwargs_extras()`
- `default_vision_model()`
- `get_max_tokens()`
- `fetch_models()`

发现顺序：

1. 学习项目 bundled Provider 插件；
2. `$LEARN_HERMES_HOME/plugins/model-providers/` 用户插件；
3. legacy 单文件 Provider 模块。

后注册同名 canonical Provider 覆盖先注册者；alias 指向最终 canonical
Profile。六个 Provider 是保证的 bundled 集合，用户插件仍可按 Hermes
语义覆盖。

## 六 Provider 静态契约

| Provider | aliases | 凭据/配置 | 默认端点 | api_mode |
|---|---|---|---|---|
| `openrouter` | `or` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `chat_completions` |
| `azure-foundry` | `azure`, `azure-ai-foundry`, `azure-ai` | `AZURE_FOUNDRY_API_KEY`, `AZURE_FOUNDRY_BASE_URL` | 用户提供 | 动态 |
| `custom` | `ollama`, `local`, `vllm`, `llamacpp`, `llama.cpp`, `llama-cpp` | 无固定 Key | 用户提供 | 动态 |
| `vertex` | `google-vertex`, `vertex-ai`, `gcp-vertex` | OAuth2、`VERTEX_CREDENTIALS_PATH` 或 ADC | 动态生成 | `chat_completions` |
| `alibaba` | `dashscope`, `alibaba-cloud`, `qwen-dashscope` | `DASHSCOPE_API_KEY` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `chat_completions` |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | `chat_completions` |

`vertexai` 继续按 Hermes runtime resolver 的兼容入口解析为 `vertex`，
但不加入 Profile 声明的 alias 元组。

## 配置模型

核心配置需要支持：

```yaml
model:
  provider: openrouter
  default: anthropic/claude-sonnet-4.6
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
  auth_mode: api_key
  timeout_seconds: 60
  max_tokens: null
  fallbacks: []
  reliability: {}
```

Azure Foundry：

```yaml
model:
  provider: azure-foundry
  default: gpt-5.4
  base_url: https://example.openai.azure.com/openai/v1
  api_mode: codex_responses
  auth_mode: entra_id
  entra:
    scope: https://ai.azure.com/.default
```

Vertex 非敏感配置：

```yaml
vertex:
  project_id: my-project
  region: global
```

Custom：

- 支持当前 `model.provider: custom`；
- 支持 `providers.<name>`；
- 支持 Hermes legacy `custom_providers`；
- 支持 `model`、`models`、`base_url`、`key_env`、`api_mode`、
  `extra_headers`、TLS、`discover_models`、`supports_vision`、
  `context_length`、`max_output_tokens` 和 request overrides；
- Key 可选；
- 不为 `ollama`、`local` 或 `vllm` 设置固定地址。

配置归一化必须保留批准的 Provider 配置字段，不能像当前
`_normalize_config()` 一样重建固定小字典并丢弃扩展字段。

## Runtime 与凭据解析

统一顺序：

1. 显式调用参数；
2. `config.yaml` 当前 Provider 配置；
3. Provider 专属环境变量；
4. credential pool；
5. Profile 默认值。

规则：

- disabled Provider 在网络请求前失败；
- alias 在读取配置前 canonicalize；
- OpenRouter Key 只发送给 OpenRouter 或明确配置的 OpenRouter proxy；
- Custom 不继承无关 OpenAI/OpenRouter Key；
- 自定义端点的环境变量 Key 必须经过精确 hostname 匹配；
- Custom 无 Key 时允许无认证，内部 SDK 占位值不得被解释成真实凭据；
- Vertex credential path 不能被当成 API Key；
- Azure Foundry 必须在 generic/custom/pool 路径前解析；
- Runtime 敏感字段 `repr=False`；
- pool、lease、streaming streak 等动态状态不进入 Runtime。

## Credential Pool

只复刻与六 Provider 有关的 pool 能力：

- API Key entry；
- custom endpoint entry；
- 环境变量和配置 seed；
- canonical Provider 与 base URL 匹配；
- priority 和当前 entry；
- cooldown/exhausted 状态；
- `Retry-After` 到期时间；
- select、peek、rotate、reset、add、remove；
- credential lease；
- 原子持久化；
- secret 不进入 `repr`、日志或错误文本。

适用于 OpenRouter、custom、Alibaba 和 DeepSeek。

Vertex 使用 google-auth credential cache，不进入 API Key pool。
Azure Foundry按 Hermes 的 runtime short-circuit 使用当前 API Key 或
Entra token provider，不经过通用 pool。

## Vertex

必须复刻：

- `google-auth` lazy dependency；
- `VERTEX_CREDENTIALS_PATH` 优先于
  `GOOGLE_APPLICATION_CREDENTIALS`；
- service-account JSON 和 ADC；
- project ID：环境变量/配置/credential 的优先级；
- region：环境变量/配置/`global` 的优先级；
- multiplex secret scope，拒绝借用其他 profile credential path；
- credential object cache；
- token 缺失、过期或五分钟内过期时刷新；
- ADC 失败后的 service-account 重试；
- global 和 regional 动态 URL：

```text
https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/global/endpoints/openapi
https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{region}/endpoints/openapi
```

- 主 Client 401 后重新取 token 并重建；
- auxiliary Client 401 后清理缓存并重建；
- 无 `/models` 在线发现，使用 curated list；
- thinking config 复用 Gemini wire helper，但不注册 Gemini Provider；
- `default_aux_model=google/gemini-3-flash-preview`。

## Azure Foundry

必须支持：

- 用户提供 endpoint；
- `AZURE_FOUNDRY_BASE_URL`；
- `AZURE_FOUNDRY_API_KEY`；
- `auth_mode: api_key`；
- `auth_mode: entra_id`；
- `azure-identity` lazy dependency；
- `DefaultAzureCredential`；
- 默认 scope `https://ai.azure.com/.default`；
- callable token provider；
- OpenAI Client 每请求取 token；
- Anthropic Client 通过 `httpx` request hook 注入 Bearer token；
- doctor 结构状态与 live credential probe 分离；
- endpoint/model 探测；
- `/anthropic` 和显式配置解析为 `anthropic_messages`；
- Anthropic base URL 去除尾部 `/v1`；
- GPT-5、Codex、o1/o3/o4 family 解析为 `codex_responses`；
- 其他模型使用 `chat_completions`；
- mid-session model switch 重新推导 api_mode。

旧 `azure-openai` 不注册为 alias。现有配置显式迁移到
`azure-foundry`，避免隐藏语义变化。

## OpenRouter

必须复刻：

- 公共模型目录，无需 Key；
- remote curated catalog、本地 fallback snapshot 和进程缓存；
- live catalog tool-capability 过滤；
- free/recommended/default 标记；
- Profile fallback models；
- `session_id` 和 `provider_preferences`；
- Pareto Code `plugins` 参数及 0..1 score 校验；
- reasoning config；
- Claude 4.6+ mandatory/adaptive reasoning 特例；
- mandatory Anthropic 模型的 `verbosity` effort 路由；
- xAI/Grok `x-grok-conv-id`；
- OpenRouter upstream error 识别；
- upstream 429 不轮换健康 OpenRouter Key；
- account 429 可 cooldown/rotation；
- response cache header 统计；
- pricing、session cost 和账户用量；
- setup、model picker、doctor 和 auxiliary Client。

不包含 OpenRouter 图片生成。

## Custom

必须复刻：

- 所有声明 alias；
- bare custom 和 named custom；
- 用户提供 base URL；
- API Key 可选；
- `/models` 探测和 `/v1` 提示；
- 手工 model 输入；
- 三种 api_mode 的显式选择和 URL 推导；
- `ollama_num_ctx -> extra_body.options.num_ctx`；
- reasoning disabled 同时生成顶层 `reasoning_effort="none"` 和
  `extra_body.think=false`；
- reasoning enabled + effort 使用顶层 `reasoning_effort`；
- 不主动发送 `think=true`；
- `default_max_tokens=65536`；
- extra headers、TLS、request overrides；
- named custom model、context、vision 和 max output metadata；
- 多 custom endpoint 的 pool 隔离；
- localhost/LAN/cloud custom 的 stale timeout 选择；
- model switch 后配置持久化。

## DeepSeek

必须复刻：

- `deepseek-chat` alias；
- fallback models：`deepseek-chat`、`deepseek-reasoner`；
- `default_aux_model=deepseek-chat`；
- model normalization；
- thinking-capable family 检测；
- `thinking.type=enabled|disabled`；
- `low|medium|high` 原样传递；
- `xhigh|max|ultra -> max`；
- 未配置 effort 时使用服务端默认；
- 保存响应 `reasoning_content`；
- assistant tool-call 历史回放时携带 `reasoning_content`；
- 缺失时使用 Hermes 安全占位语义；
- 从 raw/provider_data 回填 tool reasoning；
- 非 DeepSeek 请求清理不兼容 thinking 字段；
- context、402、rate-limit 和 server error 分类；
- setup、model picker、doctor、auxiliary 和 usage。

## Alibaba

必须复刻：

- canonical name、aliases 和 `DASHSCOPE_API_KEY`；
- 国际 OpenAI-compatible 默认端点；
- generic API Key setup；
- models.dev/curated/live 模型解析；
- endpoint override；
- Anthropic-compatible endpoint override 时选择
  `anthropic_messages`；
- Qwen model metadata、tool/reasoning/context 行为；
- Alibaba 专属 rate-limit 文本分类；
- prompt cache、auxiliary 和 usage 的共享路径。

Alibaba Coding Plan 是另一个 Provider，本批不实现。

## Transport 与标准化

三个 Transport 均输出：

- `NormalizedResponse.content`
- `tool_calls`
- `finish_reason`
- `reasoning`
- `usage`
- `provider_data`

Chat Completions 还要处理：

- usage-only chunk；
- `reasoning_content`/`reasoning`；
- Vertex `extra_content.google.thought_signature`；
- 整数或缺失 tool-call ID；
- 重复完整函数名；
- response validation。

Anthropic Messages 处理：

- system/messages/tool schema；
- tool use/result；
- signed thinking blocks及顺序保存；
- Azure Entra Bearer hook；
- usage/cache 字段。

Codex Responses 处理：

- input items；
- function call/result；
- reasoning items；
- response item ID/call ID；
- streaming event；
- usage 和 finish reason。

Transport 不拥有 credential refresh、retry 或 fallback。

## 错误、Retry、Rotation 与 Fallback

```text
底层结构化错误
  -> Provider/状态/正文分类
  -> credential cooldown/rotate
  -> 当前 binding retry
  -> 当前 binding 耗尽
  -> AIAgent fallback
```

必须区分：

- auth；
- billing/quota；
- account rate limit；
- OpenRouter upstream rate limit；
- overload；
- timeout/network；
- server；
- context overflow；
- invalid request/format；
- unknown。

规则：

- `Retry-After` 优先且受 cap 限制；
- partial 可见输出后不重放完整请求；
- OpenRouter upstream error 不轮换 Key；
- 401/402/403 不盲目 retry；
- Vertex 401 优先刷新并重建 Client；
- pool rotation 与 fallback 不形成乘法嵌套；
- 编程错误不进入 Provider fallback。

## Streaming 与 Partial Continuation

- daemon producer 消费原始 iterator；
- caller thread 执行 callback 和状态变化；
- queue read 带 stale timeout；
- attempt fence 丢弃旧 attempt 迟到事件；
- stale 时 best-effort close；
- 达阈值后只禁用当前 binding streaming；
- streaming 返回完整响应时直接使用，当前请求不重发；
- partial text 转换为明确 partial response；
- incomplete tool call 只记录 marker，永不执行；
- AIAgent 加入 partial text 并发送 continuation instruction；
- continuation 次数独立有界；
- callback 只接收新增 delta。

## CLI、Doctor、Model、Auxiliary 与 Usage

包含：

- 六 Provider setup flow；
- Key 安全输入和 `.env` 持久化；
- Vertex project/region；
- Azure endpoint/auth/api_mode 探测；
- Custom endpoint/model 探测；
- provider/model picker；
- `/model` 切换后重建 Runtime、Client、Transport 和状态；
- alias/canonical 显示；
- auth status；
- doctor 结构检查和 live probe；
- primary 与 auxiliary 使用同一 Runtime 解析；
- auxiliary Client cache；
- Vertex auxiliary 401 refresh；
- fallback auxiliary model；
- canonical usage、cache tokens、session cost；
- OpenRouter account usage/cache header。

不新增 Desktop/Web UI。

## 安全边界

- Key、token、service-account path 和 extra headers 不进入公开输出；
- credentialed redirect 不向其他 origin 转发 Authorization；
- host 匹配使用解析后的 hostname；
- Custom 不继承无关云 Provider Key；
- Vertex secret scope 防止 multiplex 串号；
- credential pool 原子写入；
- TLS override 只来自用户明确配置；
- Client、doctor、model probe 使用相同凭据隔离；
- Provider secret 加入 terminal/environment 输出过滤。

## 兼容与迁移

- `fake` 保留用于学习和离线验证；
- 现有 `openai-compatible` 不属于六 Provider 保证对象，可作为内部兼容
  Profile 保留；
- `azure-openai` 配置迁移为 `azure-foundry`；
- 旧 `api_key_env` 可在迁移期读取，保存时写入 Hermes 对齐字段；
- 每个任务独立提交，可按任务回滚；
- Provider 默认可继续配置为 `fake`；
- 未完成的 Provider 不应假装可用。

## 验收标准

共享：

- 六 Provider canonical name、alias 和配置契约一致；
- 三个 api_mode 经过同一个 `NormalizedResponse` 边界；
- Runtime frozen，动态状态不进入 Runtime；
- retry、rotation、fallback 和 continuation 均有上限；
- secret 不泄漏；
- fake、tool calling、usage 和 session 行为不回归。

Provider：

- OpenRouter：模型过滤、reasoning、upstream 429、pool 与用量一致；
- Azure Foundry：API Key/Entra 与三协议路由一致；
- Custom：无固定地址、Key 可选、alias/named endpoint/reasoning 一致；
- Vertex：service account/ADC、动态 URL 和 token refresh 一致；
- Alibaba：端点、模型、限流和协议 override 一致；
- DeepSeek：thinking 与 `reasoning_content` tool replay 一致。

默认验证：

1. `uv run python -m compileall -q src`
2. 一次性 Profile/alias/config/schema 不变量脚本
3. 注入 fake HTTP/SDK Client 验证 URL、Header 和 payload
4. no-op sleep/clock 验证 retry、cooldown 和 rotation
5. 短 stale timeout iterator 验证 watchdog 与 attempt fence
6. partial text/tool-call 脚本验证 continuation
7. 临时凭据文件验证 Vertex/Azure，不访问真实云端
8. CLI `doctor`、model setup/switch 和 fake chat 回归
9. `git diff --check`

除非用户明确要求，不创建新测试文件。

## Hermes 对齐索引

共享模块：

- `providers/base.py`
- `providers/__init__.py`
- `hermes_cli/providers.py`
- `hermes_cli/auth.py`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/models.py`
- `hermes_cli/model_catalog.py`
- `hermes_cli/model_normalize.py`
- `hermes_cli/model_setup_flows.py`
- `hermes_cli/model_switch.py`
- `hermes_cli/doctor.py`
- `agent/credential_pool.py`
- `agent/error_classifier.py`
- `agent/retry_utils.py`
- `agent/chat_completion_helpers.py`
- `agent/conversation_loop.py`
- `agent/auxiliary_client.py`
- `agent/transports/`
- `agent/usage_pricing.py`
- `agent/account_usage.py`
- `agent/model_metadata.py`
- `run_agent.py`

Provider 模块：

- `plugins/model-providers/openrouter/`
- `plugins/model-providers/azure-foundry/`
- `plugins/model-providers/custom/`
- `plugins/model-providers/vertex/`
- `plugins/model-providers/alibaba/`
- `plugins/model-providers/deepseek/`
- `agent/vertex_adapter.py`
- `agent/azure_identity_adapter.py`
- `hermes_cli/azure_detect.py`

每个实施 Task 开始前必须使用 Codegraph 重新读取对应 Hermes 模块。
如果参考 HEAD 已变化，先列出差异并更新设计/计划，再提供源码步骤。
