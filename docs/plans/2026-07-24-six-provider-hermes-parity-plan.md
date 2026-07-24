# Hermes 六 Provider 1:1 复刻 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在当前学习项目中，按参考仓库固定 HEAD 的实际行为，完整复刻 OpenRouter、Azure Foundry、custom、Vertex、Alibaba、DeepSeek 六个 LLM Provider；明确排除 OpenRouter 图片生成和 Desktop/Web UI。

**Architecture:** 保持 `AIAgent -> per-binding request lifecycle -> ProviderClient -> ProviderTransport -> NormalizedResponse`。Provider Profile 描述静态能力和请求钩子；runtime resolver 解析配置与认证；client 负责 HTTP/JSON/SSE 和结构化底层错误；request lifecycle 负责单 binding retry、backoff、stream supervision；`AIAgent` 负责 binding 状态、credential rotation、fallback 和 partial continuation。`ProviderRuntime` 继续 frozen，不保存 cooldown、失败次数、token 刷新结果等动态状态。

**Tech Stack:** Python 3.11+、dataclasses、typing Protocol、urllib/OpenAI-compatible HTTP、OpenAI SDK、Anthropic SDK、google-auth、azure-identity、PyYAML、uv。

---

## 0. 执行约束

本计划严格遵守以下工作方式：

1. 使用现有分支 `feat/phase_10`，不创建 worktree。
2. 参考仓库 `D:\python-develop\project\hermes-agent` 只读。
3. 参考基线固定为 Hermes HEAD `a61183b56fdb45b9d2a0f2f6b8482e665ccf702f`。
4. 设计基线为 `docs/plans/2026-07-24-six-provider-hermes-parity-design.md`。
5. 源码默认由 Codex 在对话中一次提供一个小步骤，用户手工抄写。
6. 用户回复“好了”“下一步”后，Codex 才继续；用户说“你看看”时，先读取最新代码。
7. 每个源码步骤必须说明文件、插入位置、设计原因和手工验证方法。
8. 用户抄写完成后，由 Codex 自动执行本步骤的轻量验证，不再要求用户复制验证脚本。
9. 默认不新增测试文件。优先使用 `compileall`、一次性 Python 脚本、CLI、schema/接口一致性和轻量不变量。
10. 文档可由 Codex直接维护。
11. 不修改或处理既有 `sandbox/`。
12. 每个 Task 完成、验证通过后再提交；提交只包含该 Task 的相关文件。

## 1. 范围和完成定义

### 包含

- OpenRouter
- Azure Foundry
- custom
- Vertex
- Alibaba
- DeepSeek
- 六个 Provider 所依赖的共享能力：
  - Provider Profile 完整契约与插件发现
  - 模型目录与模型元数据
  - OpenAI-compatible Chat Completions
  - Anthropic Messages
  - Codex Responses
  - credential pool、cooldown、lease 和 rotation
  - retry、`Retry-After`、stream watchdog、attempt fence、partial continuation
  - Vertex ADC/服务账号/OAuth2 access token 刷新
  - Azure API Key/Entra ID
  - CLI Provider 配置、模型选择、状态诊断
  - auxiliary model、usage/pricing/cache 状态

### 明确排除

- OpenRouter 图片生成
- Desktop UI
- Web UI
- 独立 Gemini Provider
- 独立 Anthropic Provider
- 独立 OpenAI/Codex Provider
- Alibaba Coding Plan 独立 Provider
- 六个目标以外的 Provider 插件

### 完成标准

- 六个 canonical name、alias、环境变量、默认 URL、模型发现和请求钩子与固定 Hermes HEAD 一致。
- Azure Foundry 三种 API mode 和两种认证方式可解析并路由。
- Vertex 动态 URL、ADC/服务账号、token 刷新和 401 重建可工作。
- OpenRouter/custom/Alibaba/DeepSeek 的多凭证 pool 可选取、冷却、轮换和持久化。
- streaming 失败不会重复计费；partial output 不会被静默重放；tool-call partial 不会错误续写。
- CLI 能完成六个 Provider 的配置、模型选择、状态检查和 doctor。
- 所有新增模块可编译，关键不变量和离线 fake-client 场景通过。

---

## Task 1：收口当前 streaming 动态降级语义

**Hermes 对齐：**

- `agent/chat_completion_helpers.py`
- `agent/conversation_loop.py`
- `agent/error_classifier.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/providers/state.py`
- Modify: `src/learn_hermes_agent/providers/errors.py`（仅在现有类型缺少必要分类时）

### Step 1：核对当前代码与旧计划剩余项

读取上述三个学习项目文件和旧计划 Task 7，列出“已实现、缺失、实现差异”。本步骤不修改源码。

**Verify:** 确认不会重复 Task 1 至当前已提交步骤，也不改动已经稳定的错误原语。

### Step 2：区分 stream options 拒绝与 streaming 拒绝

在现有错误决策中补齐两个互不混淆的降级建议：

- 仅禁用 `stream_options`
- 对该 binding 后续禁用 streaming

不要把临时网络错误当作协议不支持。

**Why:** Hermes 的降级是能力记忆，不是所有 streaming 错误的统一 fallback。

**Verify:** 一次性不变量脚本构造两类错误，确认得到不同决策。

### Step 3：实现单 binding 的动态降级

在 `request_provider_completion()` 中：

- stream options 明确被拒绝时，只重试一次不带 stream options 的 streaming 请求；
- streaming 明确被拒绝时，后续请求改用 non-stream；
- 状态只写入当前 `ProviderBindingState`。

**Why:** 防止一个 endpoint 的限制污染其他 binding。

**Verify:** 两个 fake binding 中只有命中的 binding 状态发生变化。

### Step 4：保持“完整响应不重发”不变量

如果 `stream=True` 返回完整响应对象：

- 直接标准化并返回当前响应；
- 标记该 binding 后续禁用 streaming；
- 当前调用不得再发一次 non-stream 请求。

**Verify:** fake client 请求计数严格为 1。

### Step 5：Task 验证与提交

运行：

```powershell
uv run python -m compileall -q src
```

再运行一次性 fake-client 场景覆盖两个降级分支和完整响应分支。

**Commit:** `feat: align streaming capability downgrade`

---

## Task 2：建立 stale-stream supervisor 原语

**Hermes 对齐：**

- `agent/chat_completion_helpers.py`
- `agent/retry_utils.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/stream_supervisor.py`
- Modify: `src/learn_hermes_agent/providers/errors.py`

### Step 1：定义 attempt identity

新增 frozen attempt token，至少包含 binding identity 和单调递增 attempt number。

**Why:** 后续 timeout 或旧 iterator 返回时，必须能识别它是否仍属于当前请求。

**Verify:** token 可比较、不可修改，两个 attempt 不相等。

### Step 2：定义 stream timing policy

新增 frozen policy，包含：

- 首 chunk timeout
- chunk 间 stale timeout
- 本地与远程默认值

配置值必须大于 0。

**Verify:** 非法值被拒绝；local/remote 默认值不同。

### Step 3：定义 supervisor 事件

定义可区分：

- 首 chunk
- 正常 chunk
- 完成
- stale timeout
- iterator error

保持事件对象不携带 client 动态状态。

**Verify:** 事件枚举/数据类 schema 与 request lifecycle 需要一致。

### Step 4：实现有界 next-chunk supervision

将阻塞的 iterator `next()` 包装为可超时等待的操作；超时后抛出结构化 stale-stream 错误。

不得在 timeout 后把后台返回的旧 chunk 交给新 attempt。

**Verify:** fake iterator 延迟超过阈值时按时失败，快速 iterator 正常完成。

### Step 5：Task 验证与提交

运行 compileall 和一次性 iterator 脚本，确认线程/队列资源最终可回收。

**Commit:** `feat: add stale stream supervisor primitives`

---

## Task 3：接入 watchdog、attempt fence 与 stale binding 状态

**Files:**

- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/providers/state.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/config.py`

### Step 1：把 watchdog 配置接入 request policy

配置层加入远程与本地 stale timeout，并保持未知配置字段不被 normalization 丢弃。

**Verify:** YAML 读取后配置值完整保留。

### Step 2：每次请求分配新 attempt

attempt number 由当前 binding 的 mutable state 管理，不写入 frozen runtime。

**Verify:** 同 binding 连续请求单调递增，不同 binding 独立。

### Step 3：在 streaming iterator 外层接入 supervisor

首 chunk 使用 first-chunk timeout，后续 chunk 使用 stale timeout。

**Verify:** 两种 timeout 能分别触发。

### Step 4：建立 attempt fence

所有 chunk、错误和完成事件在交付 accumulator 前检查 attempt token；过期 attempt 只能丢弃。

**Verify:** attempt 1 超时后晚到的 chunk 不会进入 attempt 2 的结果。

### Step 5：更新 stale 计数和 streaming downgrade

仅 stale-stream 增加 `consecutive_stale_streams`；成功请求清零；达到 Hermes 阈值后只禁用当前 binding 的 streaming。

**Verify:** 网络错误不会增加 stale 计数。

### Step 6：Task 验证与提交

运行 compileall 和一次性双 attempt 场景。

**Commit:** `feat: supervise provider streams with attempt fences`

---

## Task 4：扩展 partial snapshot 与 provider_data

**Hermes 对齐：**

- `agent/transports/types.py`
- `agent/transports/chat_completions.py`
- `agent/conversation_loop.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/types.py`
- Modify: `src/learn_hermes_agent/providers/transports/base.py`
- Modify: `src/learn_hermes_agent/providers/transports/chat_completions.py`
- Modify: `src/learn_hermes_agent/providers/request.py`

### Step 1：定义不可变 partial snapshot

snapshot 至少保存：

- 已产生文本
- 已完成与未完成 tool calls
- reasoning/reasoning_content
- provider_data
- 是否已有用户可见输出

**Verify:** snapshot frozen，默认容器不可共享。

### Step 2：扩展 normalized message schema

在不破坏现有字段的前提下加入 Hermes 跨 transport 需要的 `provider_data`。

**Why:** DeepSeek reasoning content、Anthropic thinking blocks 和 Responses item 必须可回放。

**Verify:** 旧构造方式仍可用，新增字段 round-trip 保真。

### Step 3：让 accumulator 生成 snapshot

每次 chunk 后可获取当前快照；错误发生时快照与结构化 stream error 一起上抛。

**Verify:** 中途失败时文本和 tool-call fragment 不丢失。

### Step 4：保持完整响应与 streaming 响应统一

无论完整响应还是 chunk iterator，最终都返回同一种 `NormalizedResponse`。

**Verify:** 两条路径的 schema 完全一致。

### Step 5：Task 验证与提交

运行 compileall 和一次性 mixed text/tool/reasoning chunk 脚本。

**Commit:** `feat: preserve partial provider response state`

---

## Task 5：在 AIAgent 接入 partial continuation

**Hermes 对齐：**

- `agent/conversation_loop.py`
- `agent/chat_completion_helpers.py`

**Files:**

- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/agent/messages.py`（若当前消息模型需要）
- Modify: `src/learn_hermes_agent/providers/errors.py`

### Step 1：定义 continuation eligibility

只有“已有安全文本、没有不完整 tool call、没有已提交工具副作用”才允许 continuation。

**Verify:** 纯文本 partial 为 true；tool-call partial 为 false。

### Step 2：保存 partial assistant message

将安全 partial 作为 assistant history 写入一次，并附带 provider_data。

**Why:** fallback Provider 必须知道已经输出了什么，避免全文重答。

**Verify:** history 不会重复插入同一 partial。

### Step 3：构造最小 continuation instruction

指示 fallback 从中断处继续，不重复已有文本；不得伪造 tool result。

**Verify:** instruction 只在 eligible 分支出现。

### Step 4：实现 fallback 边界

- 无 partial：可正常 fallback；
- 安全文本 partial：带 continuation fallback；
- tool-call partial：停止自动 fallback，返回结构化失败；
- attempt fence 拒绝的旧 partial：不得进入 history。

**Verify:** 四个 fake 场景行为清晰且请求次数符合预期。

### Step 5：Task 验证与提交

运行 compileall 和一次性多 binding continuation 场景。

**Commit:** `feat: continue safe partial provider responses`

---

## Task 6：完成共享 reliability 配置与回归门

**Files:**

- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/providers/retry.py`
- Modify: `docs/04-progress-handoff.md`

### Step 1：统一 reliability 配置入口

整理 retry 次数、base/cap backoff、jitter、`Retry-After` 上限、stale timeout 和降级阈值。

**Verify:** 所有值有边界检查，默认值与 Hermes 当前行为有可追溯说明。

### Step 2：核对错误决策矩阵

覆盖：

- timeout/network
- 408/409/429/5xx
- auth/permission
- invalid request
- context overflow
- upstream/account rate limit
- stale stream

**Verify:** 一次性表驱动脚本打印的决策符合设计文档。

### Step 3：运行 Reliability 全量轻量回归

运行 compileall、错误分类不变量、Retry-After 两种格式、重试次数、attempt fence 和 partial continuation。

### Step 4：更新交接文档并提交

标记旧 OpenAI-compatible reliability 计划已由本计划吸收，后续以本计划为准。

**Commit:** `feat: complete shared provider reliability lifecycle`

---

## Task 7：扩展 ProviderProfile 为 Hermes 完整契约

**Hermes 对齐：**

- `providers/base.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/types.py`

### Step 1：补齐静态 Profile 字段

加入 Hermes 对应字段：

- `display_name`
- `description`
- `signup_url`
- `env_vars`
- `base_url`
- `models_url`
- `auth_type`
- health/vision capability
- `fallback_models`
- `hostname`
- `fixed_temperature`
- `default_max_tokens`
- `default_aux_model`

保留兼容 property，使旧字段迁移期间仍可读取。

**Verify:** 旧 Profile 构造不立即失效。

### Step 2：定义请求上下文

建立 frozen `ProviderRequestContext`，承载 model、session、reasoning、provider preferences 和可选 Provider 设置。

**Verify:** 不把 cooldown、计数器或 client 放进上下文。

### Step 3：加入 Hermes Profile hooks

实现默认 hook：

- `get_hostname`
- `prepare_messages`
- `build_extra_body`
- `build_api_kwargs_extras`
- `default_vision_model`
- `get_max_tokens`
- `fetch_models`

默认实现必须安全且无副作用。

### Step 4：Task 验证与提交

运行 compileall 和 Profile schema/默认 hook 不变量。

**Commit:** `refactor: align provider profile contract with Hermes`

---

## Task 8：实现 bundled/user/legacy Provider 插件发现

**Hermes 对齐：**

- `providers/__init__.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/__init__.py`
- Create: `src/learn_hermes_agent/providers/discovery.py`
- Create: `src/learn_hermes_agent/providers/plugins/__init__.py`

### Step 1：定义注册表覆盖语义

同名 Profile 后注册者覆盖前者；alias 始终解析到最终 canonical Profile。

**Why:** Hermes 允许用户插件覆盖 bundled Profile。

**Verify:** 覆盖后 canonical 和 alias 指向同一新对象。

### Step 2：发现 bundled plugins

从包内 `providers/plugins/*` 加载 `ProviderProfile` 实例。

### Step 3：发现 user plugins

从学习项目 home 下 `plugins/model-providers` 加载用户 Profile；不扫描任意工作区。

### Step 4：保留 legacy module 兼容层

按 Hermes 顺序最后加载 legacy Provider module，并明确同名覆盖行为。

### Step 5：隔离坏插件

单个插件 import 失败应产生可诊断记录，不应让所有 Provider 消失。

### Step 6：Task 验证与提交

用临时目录一次性创建两个同名插件，验证加载顺序、alias 和错误隔离。

**Commit:** `feat: discover Hermes-style provider plugins`

---

## Task 9：建立六个 bundled Provider 静态 Profile

**Hermes 对齐：**

- `plugins/model-providers/openrouter/`
- `plugins/model-providers/azure-foundry/`
- `plugins/model-providers/custom/`
- `plugins/model-providers/vertex/`
- `plugins/model-providers/alibaba/`
- `plugins/model-providers/deepseek/`

**Files:**

- Create: `src/learn_hermes_agent/providers/plugins/openrouter/__init__.py`
- Create: `src/learn_hermes_agent/providers/plugins/azure_foundry/__init__.py`
- Create: `src/learn_hermes_agent/providers/plugins/custom/__init__.py`
- Create: `src/learn_hermes_agent/providers/plugins/vertex/__init__.py`
- Create: `src/learn_hermes_agent/providers/plugins/alibaba/__init__.py`
- Create: `src/learn_hermes_agent/providers/plugins/deepseek/__init__.py`

### Step 1：OpenRouter Profile

对齐 canonical `openrouter`、alias `or`、`OPENROUTER_API_KEY`、默认 URL、models URL、fallback models 和默认能力。

### Step 2：Azure Foundry Profile

对齐 canonical `azure-foundry`、三个 alias、两个环境变量、空默认 endpoint 和 `auth_type`。

### Step 3：custom Profile

对齐 `ollama/local/vllm/llamacpp/llama.cpp/llama-cpp` aliases；不设置固定地址；API key 可选；默认 max tokens 为 65536。

### Step 4：Vertex Profile

对齐 canonical、三个注册 alias、placeholder base URL、curated models 和默认 aux model。

### Step 5：Alibaba Profile

对齐 aliases、`DASHSCOPE_API_KEY` 和国际站 compatible-mode URL。

### Step 6：DeepSeek Profile

对齐 alias、`DEEPSEEK_API_KEY`、默认 URL、fallback models 和默认 aux model。

### Step 7：Task 验证与提交

一次性脚本检查六个 canonical name、所有 alias、env vars、URL 和 fallback models。

**Commit:** `feat: add six Hermes provider profiles`

---

## Task 10：扩展 Provider 配置、环境变量与 secret 边界

**Hermes 对齐：**

- `hermes_cli/providers.py`
- `hermes_cli/auth.py`
- `run_agent.py`

**Files:**

- Modify: `src/learn_hermes_agent/config.py`
- Modify: `src/learn_hermes_agent/tools/terminal.py`（以实际 secret filter 所在文件为准）
- Create: `src/learn_hermes_agent/providers/configuration.py`

### Step 1：保留未知 Provider 配置

修正 config normalization，不能因重建固定 schema 丢失：

- provider
- model
- base_url
- api_key
- api_mode
- headers
- TLS/request overrides
- provider-specific options

### Step 2：定义配置优先级

按 Hermes 语义统一：

1. 显式调用参数
2. provider-specific 配置
3. 通用配置
4. 环境变量
5. Profile 默认值

### Step 3：集中 secret 名称

将六个 Provider 的 key/token/client secret 加入 terminal 输出和持久环境快照的过滤集合。

### Step 4：防止空 Authorization

custom 无 key 时不得生成空 Bearer header。

### Step 5：Task 验证与提交

运行 config round-trip、优先级和 secret 过滤不变量。

**Commit:** `feat: preserve provider configuration and secrets`

---

## Task 11：实现 ProviderDef 与模型目录加载

**Hermes 对齐：**

- `hermes_cli/providers.py`
- `hermes_cli/model_catalog.py`
- `hermes_cli/models.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/catalog.py`
- Create: `src/learn_hermes_agent/providers/model_catalog.py`
- Modify: `src/learn_hermes_agent/providers/base.py`

### Step 1：定义 ProviderDef

将 Profile、来源、配置状态、endpoint、认证状态和模型来源标准化为 frozen 定义。

### Step 2：加载内置和外部目录

模型来源优先级：

- Provider `fetch_models`
- models.dev/本地缓存元数据
- Profile fallback models

### Step 3：实现缓存元数据

缓存记录来源、获取时间和 schema version；过期或损坏时安全回退。

### Step 4：实现公共 `/models` 获取

OpenRouter 可无 key 读取公开 models；其他 endpoint 按各自认证规则处理。

### Step 5：Task 验证与提交

用 fake HTTP 响应验证成功、空结果、损坏缓存和 fallback。

**Commit:** `feat: add provider definitions and model catalog`

---

## Task 12：模型归一化、能力和上下文元数据

**Hermes 对齐：**

- `hermes_cli/model_normalize.py`
- `agent/model_metadata.py`
- `hermes_cli/models.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/model_normalize.py`
- Create: `src/learn_hermes_agent/providers/model_metadata.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`

### Step 1：实现模型 ID 归一化

处理 Provider prefix、OpenRouter vendor prefix、Azure deployment name 和 custom 裸模型名，避免错误剥离有效路径。

### Step 2：实现 capability 查询

至少标准化：

- context window
- output limit
- vision
- reasoning
- tool use
- input/output pricing

### Step 3：实现 Profile 覆盖

`fixed_temperature`、`default_max_tokens`、`default_aux_model` 优先于不可靠目录值。

### Step 4：Task 验证与提交

用六个 Provider 的代表模型做一次性表驱动验证。

**Commit:** `feat: normalize provider model metadata`

---

## Task 13：建立 credential pool 数据模型与原子持久化

**Hermes 对齐：**

- `agent/credential_pool.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/credential_pool.py`
- Modify: `src/learn_hermes_agent/config.py`

### Step 1：定义 credential entry

字段覆盖：

- stable id
- provider
- base_url scope
- api key
- priority
- enabled
- cooldown deadline
- exhausted state
- failure metadata

secret 不得进入 `repr()`。

### Step 2：定义 pool snapshot

持久格式带 schema version；读取时验证类型和 Provider/base 匹配。

### Step 3：实现原子 load/save

使用临时文件加原子替换；损坏文件不得覆盖内存中的可用 pool。

### Step 4：从 env/config seed

只为 OpenRouter、custom、Alibaba、DeepSeek 建池；Vertex 和 Azure Foundry 不进入通用 pool。

### Step 5：Task 验证与提交

用临时目录验证 round-trip、secret repr、损坏文件和 provider scope。

**Commit:** `feat: persist provider credential pools`

---

## Task 14：实现 credential 选择、cooldown、rotation 与 lease

**Files:**

- Modify: `src/learn_hermes_agent/providers/credential_pool.py`
- Modify: `src/learn_hermes_agent/providers/state.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

### Step 1：实现 select/peek

按 enabled、scope、cooldown、exhausted 和 priority 选择；相同优先级保持确定性。

### Step 2：实现 mark success/failure

429/配额/认证错误分别更新 Hermes 对应状态；支持 `Retry-After` cooldown。

### Step 3：实现 rotate/reset/add/remove

操作后原子持久化；不得删除当前唯一有效凭证而不返回明确错误。

### Step 4：实现 lease

并发 attempt 不能同时选择已经被独占 lease 的条目；lease 必须在成功、失败和异常路径释放。

### Step 5：接入 AIAgent binding 动态状态

pool 和 current credential 放在 `AIAgent`/binding state；rotation 时生成新的 frozen binding/runtime，不修改原 runtime。

### Step 6：Task 验证与提交

用一次性双 binding 和双 lease 场景验证隔离、轮换、cooldown 和释放。

**Commit:** `feat: rotate provider credentials safely`

---

## Task 15：重构通用 runtime resolver

**Hermes 对齐：**

- `hermes_cli/runtime_provider.py`
- `run_agent.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/state.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

### Step 1：定义认证值协议

允许 runtime 持有：

- API key 字符串
- token provider callable
- 无认证

字段必须从 `repr()` 隐藏。

### Step 2：统一 canonical/alias 解析

所有入口先解析 canonical Profile，再解析 runtime；不得在不同 CLI 路径重复维护 alias 表。

### Step 3：解析 endpoint、headers 和 request overrides

合并 Profile 默认值与显式配置，header 名大小写不影响覆盖。

### Step 4：把 Profile 绑定到 ProviderBinding

transport 可调用 Profile hooks；Profile 是静态对象，动态状态仍在 binding state。

### Step 5：定义 mode-specific client factory

runtime resolver 输出 api mode 和构建 client 所需的不可变参数，不直接执行请求。

### Step 6：Task 验证与提交

验证六个 canonical/alias 解析、无 secret repr 和 frozen runtime。

**Commit:** `refactor: resolve provider runtimes through profiles`

---

## Task 16：完整 custom runtime 和 named custom 配置

**Hermes 对齐：**

- `plugins/model-providers/custom/`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/providers.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/plugins/custom/__init__.py`
- Modify: `src/learn_hermes_agent/providers/configuration.py`

### Step 1：要求用户提供 endpoint

custom 及其 aliases 无固定地址；缺失 base URL 时返回明确配置错误。

### Step 2：支持 named custom Provider

允许配置多个命名 endpoint，各自拥有 model、base URL、key、headers、TLS/request overrides。

### Step 3：按 hostname/base 匹配 credential pool

相同 Provider 名但不同主机的凭证不得串用。

### Step 4：支持无 API key

无 key 时不发送 Authorization；有 key 时正常 Bearer 认证。

### Step 5：Task 验证与提交

验证 ollama/local/vllm aliases、两个 named custom endpoint 和无 key header。

**Commit:** `feat: resolve Hermes-compatible custom providers`

---

## Task 17：完整 OpenRouter runtime 与模型入口

**Files:**

- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/plugins/openrouter/__init__.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`

### Step 1：接入环境变量和 credential pool

`OPENROUTER_API_KEY` 可 seed pool；显式配置遵守统一优先级。

### Step 2：接入默认 headers

按 Hermes 当前 Profile 行为设置必要 header；用户配置可按 header 名覆盖。

### Step 3：实现公开模型列表与缓存

网络失败回退到缓存，再回退 Profile fallback models。

### Step 4：保留图片生成排除边界

不注册任何 image capability、image model flow 或 image request 参数。

### Step 5：Task 验证与提交

验证默认 URL、alias、pool、公开模型 fallback 和无图片入口。

**Commit:** `feat: resolve OpenRouter provider runtime`

---

## Task 18：实现 Vertex 认证适配器与动态 URL

**Hermes 对齐：**

- `agent/vertex_adapter.py`
- `plugins/model-providers/vertex/`

**Files:**

- Create: `src/learn_hermes_agent/providers/vertex_adapter.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/plugins/vertex/__init__.py`

### Step 1：定义 Vertex 配置来源

实现 project、region、service-account file/config 和 ADC 的 Hermes 优先级。

### Step 2：安全加载 google-auth

依赖缺失时提供可执行安装提示；service-account secret 不进入日志或异常 repr。

### Step 3：实现 credentials cache 和提前刷新

token 距到期不足 5 分钟时刷新；cache key 必须包含有效身份范围。

### Step 4：构建动态 base URL

对齐：

- global：`https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/global/endpoints/openapi`
- regional：`https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{region}/endpoints/openapi`

### Step 5：提供 token provider callable

runtime 保存稳定 callable，而不是把会过期的 token 固化在 frozen runtime。

### Step 6：Task 验证与提交

通过 fake credentials 验证 URL、刷新阈值、cache 和 secret 隐藏；不要求真实 GCP 网络。

**Commit:** `feat: authenticate Vertex provider dynamically`

---

## Task 19：实现 Azure Identity 与 Azure Foundry runtime 路由

**Hermes 对齐：**

- `agent/azure_identity_adapter.py`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/azure_detect.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/azure_identity_adapter.py`
- Create: `src/learn_hermes_agent/providers/azure_detect.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/plugins/azure_foundry/__init__.py`

### Step 1：实现 endpoint 规范化

接受 Azure Foundry 支持的 endpoint 形态；拒绝无主机或明显非 Azure endpoint。

### Step 2：实现 API key 认证

解析 `AZURE_FOUNDRY_API_KEY` 和配置 key，遵守统一优先级。

### Step 3：实现 Entra token provider

使用 azure-identity 获取/刷新 token；runtime 只保存 callable。

### Step 4：实现 api mode 判定

对齐 Hermes：

- GPT-5/codex/o1/o3/o4 等路由 `codex_responses`
- `/anthropic` 或显式配置路由 `anthropic_messages`
- 其余使用 `chat_completions`

### Step 5：规范化 Anthropic endpoint

Anthropic SDK 使用前移除 Hermes 对应的尾部 `/v1`，不影响其他 mode。

### Step 6：Task 验证与提交

用 endpoint/model/auth 矩阵验证三种 mode 和两种认证。

**Commit:** `feat: resolve Azure Foundry authentication and modes`

---

## Task 20：建立多 API mode client factory 与可选依赖

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/learn_hermes_agent/providers/client.py`
- Create: `src/learn_hermes_agent/providers/client_factory.py`
- Create: `src/learn_hermes_agent/providers/dependencies.py`

### Step 1：定义 mode-neutral client protocol

client 只暴露 transport 所需调用，不把 SDK response 泄漏给 `AIAgent`。

### Step 2：加入延迟依赖检查

OpenAI、Anthropic、google-auth、azure-identity 在实际需要时导入；错误提示包含对应功能和安装方式。

### Step 3：实现 client factory

按 `api_mode` 构造：

- Chat Completions client
- Anthropic Messages client
- Codex Responses client

### Step 4：处理 callable credential

每次请求从 token provider 获取有效 token；不得在构造 client 后永久缓存旧 token。

### Step 5：Task 验证与提交

使用 fake SDK modules 验证三种 client、无依赖错误和 callable token。

**Commit:** `feat: create clients for all Hermes API modes`

---

## Task 21：实现 Anthropic Messages transport

**Hermes 对齐：**

- `agent/transports/anthropic.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/transports/anthropic_messages.py`
- Modify: `src/learn_hermes_agent/providers/transports/__init__.py`
- Modify: `src/learn_hermes_agent/providers/types.py`

### Step 1：注册 `anthropic_messages`

保持 transport 无状态，只做协议转换和标准化。

### Step 2：转换 system/messages/tools

对齐 Hermes 的 system 拆分、content blocks、tool schema 和 tool result 结构。

### Step 3：标准化完整响应

提取文本、thinking、tool use、finish reason 和 usage。

### Step 4：标准化 streaming 事件

支持文本、thinking、tool input JSON fragment、usage 和停止事件。

### Step 5：保存 provider_data

thinking/signature 等信息必须可进入下一轮历史。

### Step 6：Task 验证与提交

用 fake Anthropic 完整响应与 event stream 做 schema 一致性验证。

**Commit:** `feat: add Anthropic Messages transport`

---

## Task 22：实现 Codex Responses transport

**Hermes 对齐：**

- `agent/transports/codex_responses.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/transports/codex_responses.py`
- Modify: `src/learn_hermes_agent/providers/transports/__init__.py`
- Modify: `src/learn_hermes_agent/providers/types.py`

### Step 1：注册 `codex_responses`

只实现 Azure Foundry 本范围需要的 Responses 能力，不注册独立 Codex Provider。

### Step 2：转换 input items 和 tools

保持 function call id、arguments、function output 和 reasoning item 的关联。

### Step 3：标准化完整响应

输出统一文本、tool calls、reasoning/provider_data、finish reason 和 usage。

### Step 4：标准化 streaming events

覆盖 output text delta、function arguments delta、reasoning 和 completion。

### Step 5：处理 encrypted/provider replay data

需要回放的 provider-specific item 进入 `provider_data`，不塞入用户可见文本。

### Step 6：Task 验证与提交

用 fake Responses item/event 序列验证 round-trip。

**Commit:** `feat: add Codex Responses transport`

---

## Task 23：让 Chat Completions transport 执行完整 Profile hooks

**Hermes 对齐：**

- `agent/transports/chat_completions.py`
- `agent/chat_completion_helpers.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/transports/base.py`
- Modify: `src/learn_hermes_agent/providers/transports/chat_completions.py`
- Modify: `src/learn_hermes_agent/providers/request.py`

### Step 1：调用 `prepare_messages`

Profile 可在请求前修正消息，但不得原地修改 conversation history。

### Step 2：合并 extra body 和 kwargs extras

明确合并优先级，避免 Profile 默认覆盖用户显式值。

### Step 3：接入 reasoning/thinking

将标准 reasoning 设置交给 Profile 映射为具体 Provider 参数。

### Step 4：回放 provider_data

DeepSeek reasoning、thinking signature 等只能按原 Provider 协议回放。

### Step 5：统一完整和 streaming 解析

两条路径保留 reasoning、usage、cache 和 provider-specific metadata。

### Step 6：Task 验证与提交

用 fake Profile 记录 hook 调用顺序和最终 kwargs。

**Commit:** `feat: apply provider hooks in chat transport`

---

## Task 24：复刻 OpenRouter 请求、模型和限流行为

**Hermes 对齐：**

- `plugins/model-providers/openrouter/__init__.py`
- `agent/error_classifier.py`
- `agent/account_usage.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/plugins/openrouter/__init__.py`
- Modify: `src/learn_hermes_agent/providers/errors.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`

### Step 1：实现 `build_extra_body`

对齐 session id、provider preferences 和 pareto-code plugin score，score 限制在 0..1。

### Step 2：实现 `build_api_kwargs_extras`

对齐 reasoning、Claude 4.6+ adaptive 行为、verbosity 和 xAI `x-grok-conv-id`。

### Step 3：区分账户 429 与上游 429

分类结果影响 credential cooldown/rotation，但不实现真正的上游 Provider 切换。

### Step 4：对齐模型列表标准化

保留 OpenRouter vendor/model id，不错误当成 Provider prefix 删除。

### Step 5：再次确认图片生成不可达

不存在 image transport、image CLI、image model type 或相关依赖入口。

### Step 6：Task 验证与提交

运行 kwargs、429 分类和模型 ID 表驱动不变量。

**Commit:** `feat: align OpenRouter provider behavior`

---

## Task 25：复刻 custom 请求参数和 endpoint 兼容行为

**Hermes 对齐：**

- `plugins/model-providers/custom/__init__.py`
- `hermes_cli/providers.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/plugins/custom/__init__.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`

### Step 1：实现 reasoning 参数映射

- disabled：顶层 `reasoning_effort="none"`，并设置 `extra_body.think=false`
- enabled：只设置 Hermes 对应顶层 effort
- 不主动发送 `think=true`

### Step 2：接入 `num_ctx`

仅在配置存在时发送，用户显式 extra body 优先。

### Step 3：支持 headers/TLS/request overrides

这些设置只作用于目标 named custom endpoint。

### Step 4：模型发现和 fallback

支持兼容 `/models`；失败时保留用户配置模型，不假设固定 vLLM/Ollama 地址。

### Step 5：context overflow 兼容分类

识别 vLLM/Ollama 常见错误文本，但不把所有 400 都归类为 context overflow。

### Step 6：Task 验证与提交

验证 reasoning、num_ctx、无 key、两个 host 隔离和 overflow 样例。

**Commit:** `feat: align custom provider behavior`

---

## Task 26：复刻 DeepSeek reasoning_content 生命周期

**Hermes 对齐：**

- `plugins/model-providers/deepseek/`
- `agent/conversation_loop.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/plugins/deepseek/__init__.py`
- Modify: `src/learn_hermes_agent/providers/transports/chat_completions.py`
- Modify: `src/learn_hermes_agent/agent/messages.py`

### Step 1：实现 thinking config

按 Hermes 把通用 reasoning 设置映射到 DeepSeek 支持参数。

### Step 2：保存 assistant `reasoning_content`

完整响应和 streaming 都进入 provider_data，并与对应 assistant message 绑定。

### Step 3：在后续请求正确回放

tool call assistant message 的 reasoning content、tool results 和下一轮消息顺序必须保持。

### Step 4：实现缺失 reasoning padding/sanitization

只在 Hermes 要求的历史形态补齐；不得伪造用户可见 reasoning。

### Step 5：避免跨 Provider 泄漏

切换到其他 Provider 时，DeepSeek 私有字段不得作为未知顶层字段发送。

### Step 6：Task 验证与提交

验证 text-only、reasoning、tool-call 和 fallback 后历史。

**Commit:** `feat: preserve DeepSeek reasoning history`

---

## Task 27：复刻 Vertex thinking、401 refresh 和 aux client

**Hermes 对齐：**

- `agent/vertex_adapter.py`
- `agent/conversation_loop.py`
- `agent/auxiliary_client.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/plugins/vertex/__init__.py`
- Modify: `src/learn_hermes_agent/providers/vertex_adapter.py`
- Modify: `src/learn_hermes_agent/providers/request.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

### Step 1：实现 Gemini thinking config helper

只作为 Vertex 内部模型参数映射，不注册独立 Gemini Provider。

### Step 2：主 client 遇到 401 时刷新

使旧 token 失效，刷新 credentials，重建当前 binding client，并按 Hermes 限制重试。

### Step 3：aux client 遇到 401 时同步重建

主 client 和 auxiliary client 不能继续持有同一过期 token。

### Step 4：限制 refresh 范围

只有 Vertex 认证失败触发 token refresh；普通 400/429/5xx 走共享分类。

### Step 5：Task 验证与提交

fake credentials 第一次返回旧 token、401 后刷新，确认主/aux 都使用新 token。

**Commit:** `feat: refresh Vertex clients on authentication failure`

---

## Task 28：复刻 Azure Foundry 探测、路由和 mode-specific 请求

**Hermes 对齐：**

- `hermes_cli/model_setup_flows.py`
- `hermes_cli/azure_detect.py`
- `run_agent.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/azure_detect.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

### Step 1：实现 endpoint/auth probe

probe 必须有 timeout、结构化错误和 secret 过滤。

### Step 2：实现 mode/model probe

按显式 mode、endpoint 和模型名选择 Chat/Anthropic/Responses 探测路径。

### Step 3：实现模型配置保存

保存最终 endpoint、auth mode、api mode 和 deployment/model；不得把 Entra access token 持久化。

### Step 4：接入三种 transport

同一个 `azure-foundry` Profile 根据 runtime mode 选择对应 transport。

### Step 5：对齐 retry/auth refresh

API key 认证错误直接分类；Entra 401 允许一次 token refresh/rebuild，再失败才 fallback。

### Step 6：Task 验证与提交

用 fake endpoint 矩阵验证三种 mode、两种认证和一次 refresh 上限。

**Commit:** `feat: align Azure Foundry mode routing`

---

## Task 29：复刻 Alibaba 通用行为和边界

**Hermes 对齐：**

- `plugins/model-providers/alibaba/`
- `hermes_cli/models.py`

**Files:**

- Modify: `src/learn_hermes_agent/providers/plugins/alibaba/__init__.py`
- Modify: `src/learn_hermes_agent/providers/runtime.py`
- Modify: `src/learn_hermes_agent/providers/catalog.py`

### Step 1：完成 DashScope compatible runtime

默认国际站 URL，可显式覆盖；API key 进入通用 pool。

### Step 2：对齐 alias 和模型 ID

`dashscope`、`alibaba-cloud`、`qwen-dashscope` 都解析到 `alibaba`。

### Step 3：接入模型列表/cache/fallback

使用 Hermes 当前可用路径；不可把 Alibaba Coding Plan 模型流混入本 Profile。

### Step 4：支持显式 Anthropic endpoint override

仅当 Hermes 配置明确选择对应 mode/endpoint 时启用共享 Anthropic transport。

### Step 5：Task 验证与提交

验证默认 URL、aliases、pool scope、模型 fallback 和 Coding Plan 排除。

**Commit:** `feat: align Alibaba provider behavior`

---

## Task 30：复刻六 Provider CLI 配置、模型切换与 doctor

**Hermes 对齐：**

- `hermes_cli/providers.py`
- `hermes_cli/model_setup_flows.py`
- `hermes_cli/model_switch.py`
- `hermes_cli/auth.py`
- `hermes_cli/doctor.py`

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`
- Create: `src/learn_hermes_agent/cli/provider_setup.py`
- Create: `src/learn_hermes_agent/cli/model_switch.py`
- Create: `src/learn_hermes_agent/cli/provider_auth.py`
- Create: `src/learn_hermes_agent/cli/doctor.py`

### Step 1：Provider 列表和选择

只展示本范围六个 bundled Provider，加上动态发现的 user custom plugins。

### Step 2：实现六个 setup flow

分别收集 Hermes 所需 endpoint、key/identity、project/region、mode 和 model，不询问图片/UI 配置。

### Step 3：实现模型列表和切换

支持 catalog/cache/fallback，保存 canonical provider 和规范化模型配置。

### Step 4：实现 auth status

只显示凭证来源和状态，不打印 key、token、client secret 或 service-account 内容。

### Step 5：实现 doctor

检查：

- 配置完整性
- 依赖
- endpoint
- auth
- model
- api mode
- credential pool

### Step 6：保持非交互 CLI 可用

命令应支持脚本化参数，不强制 Desktop/Web UI。

### Step 7：Task 验证与提交

在临时配置目录运行六 Provider 的离线 CLI smoke 场景。

**Commit:** `feat: add six-provider setup and diagnostics CLI`

---

## Task 31：接入 auxiliary client、usage、pricing 和 cache 状态

**Hermes 对齐：**

- `agent/auxiliary_client.py`
- `agent/usage_pricing.py`
- `agent/account_usage.py`
- `agent/model_metadata.py`

**Files:**

- Create: `src/learn_hermes_agent/providers/auxiliary.py`
- Create: `src/learn_hermes_agent/providers/usage.py`
- Modify: `src/learn_hermes_agent/agent/core.py`
- Modify: `src/learn_hermes_agent/providers/types.py`
- Modify: `src/learn_hermes_agent/cli/doctor.py`

### Step 1：构建 auxiliary binding

按 Profile `default_aux_model` 或显式配置创建；复用认证机制但保持独立 client 生命周期。

### Step 2：接入 usage 归一化

统一 prompt/input、completion/output、cache read/write 和 reasoning token 字段。

### Step 3：接入 pricing

从模型元数据计算可用成本；未知价格显示 unknown，不默认为 0。

### Step 4：接入 OpenRouter account/cache 状态

只实现文本 LLM 使用相关状态；不调用图片 generation endpoint。

### Step 5：处理认证重建

Vertex/Azure token refresh、credential rotation 后，相关 auxiliary binding 同步更新。

### Step 6：Task 验证与提交

fake usage 响应验证六 Provider 的 schema、未知价格和 aux rebuild。

**Commit:** `feat: track provider auxiliary usage and pricing`

---

## Task 32：六 Provider 端到端验收与文档交接

**Files:**

- Modify: `docs/00-overview.md`
- Modify: `docs/02-roadmap.md`
- Modify: `docs/04-progress-handoff.md`
- Modify: `docs/plans/2026-07-24-six-provider-hermes-parity-design.md`（仅记录经实现验证后的差异）

### Step 1：执行结构和编译检查

运行：

```powershell
uv run python -m compileall -q src
```

检查 Profile、registry、resolver、client factory 和 transport 注册表 schema。

### Step 2：执行六 Provider 离线矩阵

使用一次性 fake clients，不新增测试文件，覆盖：

- canonical/alias
- config/env/pool
- model catalog
- full response
- streaming
- retry/429/Retry-After
- stale timeout/attempt fence
- partial continuation
- tool call
- reasoning/provider_data
- auth refresh
- auxiliary usage

### Step 3：执行可用的真实 CLI smoke

只使用用户已配置且明确可访问的 endpoint。没有真实凭证时不把网络验证作为完成阻塞，但必须明确标记“未做真实网络验证”。

### Step 4：执行安全边界检查

确认：

- log/repr/异常不泄漏 secret
- custom 无 key 不发送空 Authorization
- Entra/Vertex access token 不持久化
- 参考仓库无改动
- `sandbox/` 未处理

### Step 5：执行排除项检查

全仓搜索确认未新增：

- OpenRouter image generation
- Desktop UI
- Web UI
- 独立 Gemini/Anthropic/Codex Provider
- Alibaba Coding Plan Provider

共享 transport/helper 不视为独立 Provider。

### Step 6：更新路线图和交接

记录：

- Hermes 固定参考 HEAD
- 六 Provider 完成矩阵
- 已知差异
- 已完成验证
- 未执行的真实网络验证
- 下一阶段建议

### Step 7：最终提交

先运行 `git diff --check` 和 `git status --short`，确认不包含用户的 `AGENTS.md` 改动或 `sandbox/`。

**Commit:** `docs: complete six-provider Hermes parity handoff`

---

## 实施时的逐步输出模板

每次 `superpowers:executing-plans` 只交付当前一个 Step，并使用以下格式：

1. **修改文件：** 精确路径。
2. **代码位置：** 类、函数、字段或相邻代码锚点。
3. **本步代码：** 只给当前小步骤所需片段，不提前输出后续 Task。
4. **设计原因：** 说明对应 Hermes 行为和本项目职责边界。
5. **手工验证：** 告知用户抄写后回复“好了”；随后由 Codex 自动读取和验证。
6. **差异门：** 如果当前代码与计划不一致，先报告差异并调整本步骤，不直接扩大重构。

## 提交纪律

- 每个 Task 至少通过 compileall 和其列出的轻量不变量后再提交。
- 不使用 `git add .`。
- 不提交 `AGENTS.md` 的用户改动。
- 不提交或清理 `sandbox/`。
- 不修改参考仓库。
- 如果 Hermes 参考仓库 HEAD 再次变化，先停止“1:1”声明，重新做差异审计并在设计文档记录新基线。
