# Provider Transport / Runtime Foundation 设计

## 状态

- 日期：2026-07-24
- 状态：已确认
- 前置批次：F1 Session Runtime Context 已完成
- 参考 Hermes HEAD：`477c08b44766ace8b890faa72bf82ecbcf2b3ba8`
- 参考模块：
  - `providers/base.py`
  - `providers/__init__.py`
  - `hermes_cli/runtime_provider.py`
  - `agent/agent_init.py`
  - `agent/conversation_loop.py`
  - `agent/chat_completion_helpers.py`
  - `agent/transports/base.py`
  - `agent/transports/__init__.py`
  - `agent/transports/chat_completions.py`
  - `agent/transports/anthropic.py`
  - `agent/transports/codex.py`

## 目标

把当前同时负责配置解析、HTTP 请求、消息转换、响应标准化和 fallback 的
`ProviderTransport.complete()` 黑盒，调整为与最新版 Hermes 相同的职责边界：

```text
ProviderProfile
  -> ProviderRuntime
  -> ProviderClient
  -> ProviderTransport
  -> NormalizedResponse
```

`AIAgent` 负责组织请求生命周期和 fallback；Transport 只负责特定 API family 的
数据格式转换，不负责网络、凭据、重试或流式控制。

本批完成后，后续增加 streaming、Anthropic Messages、Codex Responses 或 Gemini
Native 时，只需扩展对应层，不再推翻 Provider 主链。

## 范围

本批实现：

- 最小 `ProviderProfile`，声明内置 Provider 的静态属性。
- 不可变 `ProviderRuntime`，保存解析后的单个 Provider 运行配置。
- `ProviderClient` 原始请求协议。
- `ProviderBinding`，把一个 runtime 与其 client 绑定为候选请求端点。
- Hermes 风格 `ProviderTransport` 抽象基类。
- 以 `api_mode` 为键的 Transport registry。
- `chat_completions` Transport。
- 仅负责 HTTP I/O 的 OpenAI-compatible client。
- 返回原始 Chat Completions 数据的 fake client。
- 由 `AIAgent` 执行 build kwargs、原始请求、校验、标准化和 fallback。
- 保持现有同步 CLI、tool demo、usage 和 fallback 可观察行为。

本批不实现：

- streaming、流式事件或中断。
- Anthropic Messages、Codex Responses、Bedrock Converse。
- Gemini Native client facade。
- 用户 Provider 插件发现。
- 凭据池、密钥轮换、健康状态和复杂重试。
- prompt caching、reasoning replay 和 thought signature。
- terminal、checkpoint 或 ToolExecutor 扩展。
- 新测试文件。

## 最新 Hermes 设计意图

最新版 Hermes 已把 Provider 拆成三个正交维度：

1. `ProviderProfile` 描述 Provider 身份、别名、默认地址、凭据来源和 API mode。
2. runtime resolver 把配置、环境变量和 Provider profile 解析成当前运行状态。
3. `ProviderTransport` 按 `api_mode` 转换消息、工具、请求参数和响应。

Transport 明确不拥有：

- client 构造；
- credential refresh；
- streaming；
- interrupt；
- retry / failover；
- prompt cache 生命周期。

这些运行时职责由 `AIAgent` 及其 helper 组织。Gemini Native 也没有创建独立
Transport，而是通过 client facade 暴露 Chat Completions 形状，再复用
`chat_completions` Transport。这说明 Transport 的分类依据是 API 协议，不是
Provider 品牌。

## Architecture Decision Brief

### 方案 A：分阶段建立 Hermes 边界

先实现 Profile、Runtime、Client、Transport 和 Agent orchestration 的最小同步
版本，只注册 `chat_completions`。

优点：

- 对齐 Hermes 的职责边界。
- 现有 fake、OpenAI-compatible 和 fallback 能形成完整闭环。
- 后续 API mode 和 streaming 可以独立扩展。
- 每个迁移步骤都可手工验证。

代价：

- `AIAgent` 构造参数和 Provider 工厂需要迁移。
- 旧 `ProviderTransport.complete()` 不能继续作为主接口。

### 方案 B：保留 `complete()` 兼容外壳

在旧 Provider 对象内部使用新 Transport，但继续由该对象完成请求和 fallback。

优点：

- 当前调用方改动少。

问题：

- Transport 仍间接拥有 client、请求生命周期和 fallback。
- 与 Hermes 的 Agent orchestration 边界冲突。
- 实现 streaming 或 Anthropic 时还要再次拆分。

### 方案 C：一次复制完整 Hermes Provider 系统

同时实现插件发现、credential pool、streaming、全部 API mode 和 retry/failover
状态机。

优点：

- 短期表面上最接近参考仓库。

风险：

- 改动面过大，无法逐层理解和验证。
- 会引入当前 CLI 没有消费者的复杂状态。
- 参考源码继续变化时难以判断偏差来源。

### 决策

采用方案 A。

迁移时先并行增加新结构，再切换 `AIAgent`，最后删除旧 `complete()` 和
`FallbackProviderTransport`。若切换阶段发现回归，可以暂时恢复旧 Agent 调用，
已经新增的 Profile、Runtime 和 Transport 文件不会影响旧路径。

## 组件边界

### ProviderProfile

建议文件：
`src/learn_hermes_agent/providers/profiles.py`

Profile 只保存静态声明：

- `name`
- `api_mode`
- `aliases`
- `default_base_url`
- `api_key_env`
- `requires_api_key`

本批只注册：

- `fake`
- `openai-compatible`
- `openai` 作为别名

Profile 不读取环境变量，不持有 API key，不创建 client，也不发送请求。注册表仅包含
内置 Profile，不提前复制 Hermes 的用户插件扫描。

### ProviderRuntime

文件：
`src/learn_hermes_agent/providers/runtime.py`

Runtime 是单个候选 Provider 已解析后的不可变状态：

- `provider`
- `model`
- `api_mode`
- `base_url`
- `api_key`
- `timeout_seconds`

`api_key` 不参与对象 repr。runtime builder 按“配置显式值优先，Profile 默认值兜底”
解析 primary 和 fallbacks，并在需要密钥但环境变量为空时立即失败。

Runtime 不执行请求，也不包含 fallback 状态。

### ProviderClient

建议文件：
`src/learn_hermes_agent/providers/client.py`

Client 协议只暴露原始请求操作：

```text
create(**request_kwargs) -> object
```

`OpenAICompatibleClient` 负责：

- 构造 `/chat/completions` URL。
- JSON 编码。
- Authorization 和 Content-Type headers。
- timeout。
- HTTP / URL / timeout 错误映射。
- JSON 解码。

它不读取 `ChatMessage`，不解析 tool call、usage 或 finish reason，也不返回
`NormalizedResponse`。

`FakeProviderClient` 返回 Chat Completions 形状的原始字典，使 fake 和真实 HTTP
请求经过同一个 `chat_completions` Transport。scripted response 也改为原始字典，
避免 fake 绕过标准化层。

### ProviderBinding

建议文件：
`src/learn_hermes_agent/providers/runtime.py`

一个 binding 表示一个可请求候选项：

```text
ProviderBinding
  - runtime: ProviderRuntime
  - client: ProviderClient
```

Hermes 在 `AIAgent` 上分别保存 provider/runtime 字段和 client。本学习项目使用
binding 打包单个候选项，避免为 primary 和每个 fallback 维护平行列表；请求选择、
异常处理和 fallback 仍由 `AIAgent` 负责，因此没有改变 Hermes 的职责归属。

### ProviderTransport

建议目录：
`src/learn_hermes_agent/providers/transports/`

抽象基类包含：

- `api_mode`
- `convert_messages()`
- `convert_tools()`
- `build_kwargs()`
- `validate_response()`
- `normalize_response()`
- `map_finish_reason()`

Transport 必须无凭据、无网络连接、无重试状态。相同 `api_mode` 的不同 Provider
可以复用同一个实例。

本批的 `ChatCompletionsTransport`：

- 校验 system/user/assistant/tool role。
- 保留 assistant content 和 tool calls。
- tool message 只发送 content 和 `tool_call_id`。
- 构造 `model/messages/tools` 请求参数。
- 把原始 `choices[0].message` 标准化为 `NormalizedResponse`。
- 解析 tool calls、finish reason 和 usage。

### Transport registry

建议文件：
`src/learn_hermes_agent/providers/transports/__init__.py`

提供：

- `register_transport(api_mode, transport_cls)`
- `get_transport(api_mode)`

注册键是 API 协议，例如 `chat_completions`，不是 `openai`。未知 `api_mode` 必须明确
失败，不能静默回退到 Chat Completions。

本批只有一个内置 Transport，因此只做内置注册，不做入口点或用户目录扫描。

### AIAgent

文件：
`src/learn_hermes_agent/agent/core.py`

`AIAgent` 接收非空 `ProviderBinding` 序列，并拥有：

- 当前候选 binding。
- Transport cache，按 `api_mode` 复用无状态 Transport。
- 最近一次成功的 model 和候选 index。
- 最近一次 Provider 错误。

Agent 每轮请求执行：

```text
读取本轮 messages 和 tools
  -> 遍历 primary / fallback bindings
  -> get_transport(runtime.api_mode)
  -> convert_messages / convert_tools
  -> build_kwargs
  -> client.create(**kwargs)
  -> validate_response
  -> normalize_response
  -> 记录成功候选和 usage
```

工具执行、iteration budget、compression 和 checkpoint 生命周期不因本批改变。

## 配置解析

现有配置键继续有效：

```yaml
model:
  provider: fake
  default: fake-basic
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  timeout_seconds: 60
  fallbacks: []
```

本批不要求用户新增 `api_mode`。它由 Profile 解析：

```text
fake -> chat_completions
openai-compatible -> chat_completions
openai -> openai-compatible alias -> chat_completions
```

后续引入 Provider 自定义 profile 时，再允许 Profile 或高级配置决定其他
`api_mode`。

## 错误语义

配置和运行时错误：

- 未知 Provider：`ValueError`。
- 必需 API key 缺失：`ValueError`。
- 非法 runtime 字段：`ValueError`。
- 未注册 API mode：`ValueError`。

请求错误：

- HTTP 状态、网络失败、timeout、无效 JSON：client 抛 `RuntimeError`。
- 响应 schema 不合法：Transport 抛 `RuntimeError`。

fallback：

- `AIAgent` 只捕获候选请求中的 `RuntimeError` 和 `ValueError`。
- 每个失败记录 `model: error`，然后尝试下一个 binding。
- 成功后记录候选 index、model，并清空最近错误。
- 全部失败时抛包含各候选错误的聚合 `RuntimeError`。
- `TypeError`、`AssertionError` 等编程错误不应被 fallback 吞掉。

该行为保持当前最小 fallback 语义；Hermes 的单 Provider 重试、健康状态和凭据轮换
留到独立批次。

## NormalizedResponse 边界

现有：

- `ToolCall`
- `Usage`
- `NormalizedResponse`

继续作为 Agent 与所有 API mode 之间的统一协议。本批不改变字段语义。

Transport 可以把 API 特有信息保存到 `provider_data`，但
`ChatCompletionsTransport` 暂不提前实现 thought signature 或 reasoning replay。
Agent 只消费统一字段，不读取原始响应对象。

## 兼容与迁移顺序

1. 新增 Profile 和 Runtime 数据类型，不切换调用方。
2. 新增 Transport base、registry 和 Chat Completions 实现。
3. 把 OpenAI-compatible 类拆为纯 I/O client。
4. 把 fake 改为返回原始 Chat Completions 字典。
5. runtime builder 改为构建 `ProviderBinding` 列表。
6. `AIAgent` 改为拥有请求和 fallback 编排。
7. 更新 CLI 构造链和 usage snapshot。
8. 验证通过后删除旧 `ProviderTransport.complete()` 与
   `FallbackProviderTransport`。

CLI 命令、config schema、工具 schema、消息链和 session 行为保持不变。内部 Provider
Python API 允许在本批发生一次明确迁移，不保留长期双接口。

## 验收标准

1. Profile 与 Runtime 不执行任何网络操作。
2. Transport 不持有 model、base URL、API key、timeout 或 client。
3. OpenAI-compatible client 不导入 `ChatMessage` 或 `NormalizedResponse`。
4. fake 与 HTTP client 都返回原始响应，由同一个 Transport 标准化。
5. `AIAgent` 不再调用 `provider.complete()`。
6. fallback 顺序、成功 index、最近 model 和聚合错误保持可观察。
7. tool demo 保持 `user -> assistant(tool_calls) -> tool -> assistant`。
8. usage 和 finish reason 统计保持有效。
9. 未知 Provider 和未知 `api_mode` fail fast。
10. CLI 和 model config schema 不变。
11. 未新增测试文件。
12. 未实现任何本批明确排除的 Provider 能力。

## 验证方式

默认不新增测试文件。使用：

- `uv run python -m compileall -q src`
- `uv run learn-hermes-agent doctor`
- `uv run learn-hermes-agent tools`
- fake 普通对话和 `chat --tool-demo --show-messages`
- 一次性 Python 脚本检查 Profile alias、Runtime、Transport registry 和响应标准化
- 一次性 recording client 检查 Agent 构造的原始请求参数
- 一次性失败 client 检查 primary / fallback 顺序和聚合错误
- import / schema 一致性检查
- `git diff --check`

