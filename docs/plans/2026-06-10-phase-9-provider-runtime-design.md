# Phase 9 Batch 1 Provider Runtime Design

> **For Claude/Codex:** Follow project rules: documentation may be written directly, but implementation code should be provided as snippets unless the user explicitly asks to edit code files.

**Goal:** 接入最小真实 LLM Provider runtime，让当前 agent 不再只能依赖 `FakeProviderTransport`。本批次聚焦 OpenAI-compatible Chat Completions：把现有 tool schema 传给模型，接收模型返回的 `tool_calls`，继续复用现有 conversation loop 执行工具。

**Architecture:** 扩展 `ProviderTransport.complete()`，让 provider 每轮都可接收 `tools`；`AIAgent` 负责从 `ToolRegistry` 取 tool definitions 并传给 provider；新增 `OpenAICompatibleProviderTransport` 只负责 HTTP 请求和响应解析；新增 provider runtime builder 负责根据配置选择 fake / openai-compatible provider。保持 fake provider 默认路径不变。

**Tech Stack:** Python 3.11+、stdlib `urllib.request`、当前 `ChatMessage`、当前 `ToolRegistry`、当前 `AIAgent`、当前 argparse CLI。

---

## 当前目标和约束

目标是向 Hermes 的真实运行路径靠拢：`tools/registry.py -> model_tools.py -> AIAgent.run_conversation() -> Provider` 这条链路要能被真实模型驱动。

约束：

- 不修改参考仓库 `D:\python-develop\project\hermes-agent`。
- 不做大范围 provider 框架重写。
- 不默认新增测试。
- 默认仍能使用 fake provider，保证已有学习闭环不坏。
- 不把 API key 写入 session、日志或 doctor 输出。
- 代码实现仍以片段形式交给用户手抄。

## 参考结论

OpenAI 官方 Function Calling 文档把工具调用描述成多步流程：

1. 请求模型时提供可调用工具。
2. 接收模型返回的 tool call。
3. 应用侧执行工具代码。
4. 把工具输出作为 tool message 再发回模型。
5. 模型返回最终回答，或继续请求更多工具。

当前项目已经有第 3、4、5 步的本地循环雏形；缺口是 provider 层还没有把 tools 传给真实模型，也没有把真实模型返回的 `tool_calls` 转成当前 `ChatMessage`。

Chat Completions 的工具定义形态是：

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "...",
    "parameters": { "type": "object", "properties": {} }
  }
}
```

这和当前 `ToolRegistry.list_definitions()` 的输出方向一致，所以本批次不需要重做 tool schema。

## Decision Brief

### Option A: 一次性复刻 Hermes provider runtime

包括 provider registry、fallback chain、OpenAI / Anthropic / Bedrock / Codex Responses / streaming / model metadata / auto-detection。

优点是最接近 Hermes。缺点是跨度过大，会把学习重点从 tool-calling conversation loop 转移到大量 provider 兼容细节；而且当前项目还没有真实 CLI/Gateway 多入口压力，过早引入 fallback chain 容易造成抽象空转。

### Option B: 最小 OpenAI-compatible Chat Completions provider

只实现一个 `openai-compatible` provider：读取 `base_url`、`model`、`api_key_env`，POST 到 `/chat/completions`，发送 `messages` 和 `tools`，解析 `choices[0].message.content/tool_calls`。

优点是能立刻打通真实工具调用闭环；同时兼容 OpenAI 官方接口和多数本地/第三方 OpenAI-compatible 服务。缺点是暂时不覆盖 Anthropic、Responses API、streaming 和 provider fallback。

### Option C: 先只做 provider 配置，不接 HTTP

只把 config/CLI/runtime 骨架建好，真实 provider 继续留空。

优点是风险最低。缺点是对齐 Hermes 的价值有限，仍然无法观察真实模型如何选择工具。

**Recommendation:** 选择 Option B。

理由：当前 Memory / Skills 已经形成工具和 prompt 能力，但没有真实模型时很难验证“模型主动调用工具”的核心行为。最小 OpenAI-compatible provider 是当前最小、可运行、可回滚的对齐路径。

## 范围

实现：

- `ProviderTransport.complete(messages, *, tools=None)`。
- `FakeProviderTransport` 兼容新签名并忽略 tools。
- `AIAgent.run_conversation()` 每轮把 `registry.list_definitions()` 传给 provider。
- 新增 `OpenAICompatibleProviderTransport`。
- 新增 provider runtime builder。
- config 增加 provider runtime 字段：
  - `model.provider`
  - `model.default`
  - `model.base_url`
  - `model.api_key_env`
  - `model.timeout_seconds`
- CLI `build_agent()` 改为通过 runtime builder 构建 provider。
- `doctor` 输出 provider 配置状态，但不输出 API key。

不实现：

- Anthropic Messages API。
- OpenAI Responses API。
- streaming。
- fallback chain。
- model catalog / metadata。
- retry / backoff。
- proxy / custom headers。
- provider 自动探测。
- token usage persistence。
- config 写入命令。
- 测试文件。

## 模块边界

### `providers/base.py`

只定义 provider 协议。新增 `tools` 参数后，agent 可以把 tool definitions 交给任意 provider，而 provider 不需要知道 registry 的存在。

### `providers/fake.py`

保持学习和离线验证路径稳定。fake provider 接受 `tools` 参数但忽略它。

### `providers/openai_compatible.py`

负责：

- 构造 HTTP payload。
- 发送 POST 请求。
- 解析 JSON 响应。
- 把 assistant response 转成当前 `ChatMessage`。

不负责：

- 读取全局 config。
- 访问 `ToolRegistry`。
- 执行工具。
- 记录 session。

### `providers/runtime.py`

负责把 config 转成具体 provider instance。这里是 provider 选择的唯一入口，避免 CLI 里散落 provider 判断逻辑。

### `agent/core.py`

只做 conversation loop 编排：准备 messages、传 tools、接收 assistant response、执行工具、追加 tool result。

## 数据流

1. CLI 读取 config。
2. CLI 调用 provider runtime builder。
3. `AIAgent.run_conversation()` 构造 request messages。
4. `AIAgent` 从 registry 读取 tool definitions。
5. Provider 收到 `messages + tools`。
6. 真实模型返回普通 assistant message 或 `tool_calls`。
7. `AIAgent` 复用现有 `safe_handle_function_call()` 执行工具。
8. tool result 作为 `role="tool"` message 进入下一轮 provider call。

## 错误处理原则

- 缺 API key：在 provider 构建阶段报清楚缺哪个 env var。
- HTTP 4xx/5xx：报状态码和响应 body 的摘要，不暴露 Authorization header。
- JSON 解析失败：报 provider 返回非 JSON。
- 响应缺 choices/message：报 provider 响应结构不符合 Chat Completions。
- assistant message 无 content 且无 tool_calls：允许返回空字符串，但后续可以收紧。

## 回滚路径

- config 默认仍是 `provider = "fake"`。
- `--tool-demo` 继续强制使用 scripted fake provider。
- 如果 `openai-compatible` 出错，只需要把 config 改回 fake，当前 CLI 和工具链仍可运行。

## Acceptance Criteria

- `uv run python -m compileall -q src` 通过。
- 默认 fake `chat` 仍可运行。
- `--tool-demo` 仍可触发工具调用闭环。
- `tools` / `call-tool` 不受影响。
- `doctor` 能显示 provider/model/base_url/api_key_env 状态，不泄露 key。
- 当 provider 配成 `openai-compatible` 且 env var 缺失时，CLI 给出可读错误。
- 当提供真实 OpenAI-compatible endpoint 和 key 时，模型能看到当前 tools 并返回可执行的 `tool_calls`。
