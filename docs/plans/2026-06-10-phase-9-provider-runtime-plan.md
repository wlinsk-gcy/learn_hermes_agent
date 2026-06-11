# Phase 9 Batch 1 Provider Runtime Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Follow project rules: provide implementation code as snippets unless the user explicitly asks to edit code files.

**Goal:** Add a minimal OpenAI-compatible provider runtime so the existing agent loop can send tool definitions to a real model and parse returned `tool_calls`.

**Architecture:** Extend the provider protocol with optional `tools`, keep fake provider backward-compatible, pass registry tool definitions from `AIAgent`, add an OpenAI-compatible HTTP provider, then route CLI provider construction through a small runtime builder.

**Tech Stack:** Python 3.11+、stdlib `urllib.request`、当前 config/session/tool/prompt 架构。

---

## Ground Rules

- Do not modify the reference Hermes repository.
- Do not add tests unless explicitly requested.
- Documentation may be written directly.
- Implementation code should be given as snippets for the user to copy.
- Keep fake provider as the default.
- Never print API key values.

## Task 1: Extend Provider Protocol

**Files:**

- Modify: `src/learn_hermes_agent/providers/base.py`
- Modify: `src/learn_hermes_agent/providers/fake.py`
- Modify: `src/learn_hermes_agent/agent/core.py`

**Purpose:** 让 provider 能接收 tool definitions，同时保持 fake provider 不变。

**Manual Validation:**

```powershell
uv run python -m compileall -q src
uv run learn-hermes-agent chat "hello"
uv run learn-hermes-agent chat --tool-demo "please use a tool"
```

Expected:

- compile 成功。
- 默认 chat 仍返回 fake echo。
- tool-demo 仍能进入工具调用闭环。

## Task 2: Add OpenAI-compatible Provider

**Files:**

- Create: `src/learn_hermes_agent/providers/openai_compatible.py`

**Purpose:** 用最小 HTTP client 调用 OpenAI-compatible `/chat/completions`。

Implementation notes:

- 用 stdlib `urllib.request`，本批次不新增依赖。
- POST URL = `base_url.rstrip("/") + "/chat/completions"`。
- Headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer <api_key>`
- Payload:
  - `model`
  - `messages`
  - `tools` only when non-empty
- Parse:
  - `choices[0].message.content`
  - `choices[0].message.tool_calls`
- Return:
  - `assistant_message(content, tool_calls=tool_calls or None)`

**Manual Validation:**

```powershell
uv run python -m compileall -q src
```

Optional later, after config wiring:

```powershell
$env:OPENAI_API_KEY="..."
uv run learn-hermes-agent chat "只回复一句话：provider ok"
```

## Task 3: Add Provider Runtime Builder

**Files:**

- Create: `src/learn_hermes_agent/providers/runtime.py`

**Purpose:** 把 provider 选择逻辑从 CLI 中抽出来，集中处理 fake / tool-demo / openai-compatible。

Supported providers:

- `fake`
- `openai-compatible`
- optional alias: `openai`

Behavior:

- `tool_demo=True` always returns `tool_demo_provider(...)`。
- `provider == "fake"` returns `FakeProviderTransport(...)`。
- `provider in {"openai-compatible", "openai"}` returns `OpenAICompatibleProviderTransport(...)`。
- missing API key env raises a readable `ValueError`。

**Manual Validation:**

```powershell
uv run python -m compileall -q src
```

## Task 4: Extend Config

**Files:**

- Modify: `src/learn_hermes_agent/config.py`

**Purpose:** 让 provider runtime 可以从配置读取 endpoint、key env 和 timeout。

Suggested default model config:

```python
"model": {
    "provider": "fake",
    "default": "fake-model",
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "timeout_seconds": 60,
},
```

Normalization:

- `provider`: non-empty string, default `fake`。
- `default`: non-empty string, default `fake-model`。
- `base_url`: string, default `https://api.openai.com/v1`。
- `api_key_env`: string, default `OPENAI_API_KEY`。
- `timeout_seconds`: positive int/float, default `60`。

**Manual Validation:**

```powershell
uv run python -c "from learn_hermes_agent.config import load_config; print(load_config()['model'])"
```

Expected: model dict includes new fields.

## Task 5: Wire CLI Through Runtime Builder

**Files:**

- Modify: `src/learn_hermes_agent/cli/main.py`

**Purpose:** CLI 不再手动 new fake provider，而是调用 runtime builder。

Change areas:

- Imports。
- `build_agent(config, *, tool_demo=False)`。
- `doctor` output。

Doctor should show:

- provider
- model
- base_url
- api_key_env
- whether env var is set, as boolean/status only

Do not show:

- actual API key。

**Manual Validation:**

```powershell
uv run learn-hermes-agent doctor
uv run learn-hermes-agent chat "hello"
uv run learn-hermes-agent chat --tool-demo "please use a tool"
```

## Task 6: Manual Real Provider Check

**Files:** no code changes.

**Purpose:** 可选地用真实 OpenAI-compatible endpoint 验证 provider path。

Manual config example:

```toml
[model]
provider = "openai-compatible"
default = "gpt-4.1"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
timeout_seconds = 60
```

Commands:

```powershell
$env:OPENAI_API_KEY="..."
uv run learn-hermes-agent chat "只回复 provider ok"
uv run learn-hermes-agent chat "请用 echo 工具复述 hello"
```

Expected:

- 普通 chat 能返回真实模型结果。
- 工具请求能走到 `tool_calls -> tool execution -> final assistant response`。

## Out of Scope

- Streaming。
- Anthropic / Bedrock / Responses API。
- Provider fallback chain。
- Model catalog。
- Retry/backoff。
- Token usage persistence。
- API key storage。
- Tests。

## Completion Checklist

- Provider protocol accepts optional `tools`。
- Fake provider remains compatible。
- Agent passes registry tool definitions to provider。
- OpenAI-compatible provider parses content and `tool_calls`。
- Runtime builder selects provider from config。
- CLI uses runtime builder。
- Doctor shows safe provider status。
- Compile/manual CLI checks pass。
