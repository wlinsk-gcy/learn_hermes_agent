# Phase 10 Batch 4 Minimal ToolExecutor 设计

## 目标

按最新版 Hermes 的实际工具执行分层，把 AIAgent 中逐个执行 tool call 的循环提取到 `agent/tool_executor.py`，建立模型工具范围检查、参数解析、顺序执行和 tool result 追加的统一入口。

本批不把 Hermes 当前 1800 多行执行器一次性复制进学习项目，只复刻当前承重链路所需的最小职责。

## 最新 Hermes 源码对齐

最新版 Hermes 的实际结构是：

- `agent/conversation_loop.py`
  - 从实际工具定义形成 `agent.valid_tool_names`。
  - 在执行前识别模型生成的无效工具名称。
  - 保证 assistant tool call 与 tool result 配对。
- `agent/tool_executor.py`
  - 使用模块级 `execute_tool_calls_sequential()`、`execute_tool_calls_concurrent()` 和 `execute_tool_calls_segmented()`，第一个参数是 `agent`。
  - `_parse_tool_arguments()` 将模型生成的非法参数转换为 tool result，而不是让对话循环崩溃。
  - 顺序执行器调用 `handle_function_call()` 完成 registry dispatch。
  - 负责把执行结果追加为 tool message。
- `model_tools.py`
  - 继续保留 `handle_function_call()` 主分发入口。
  - 调用 registry dispatch，并承载 plugin/middleware/edit approval 等分发边界能力。

因此，本项目不采用先前讨论的 `ToolExecutor` 类。类方案可以工作，但不是最新版 Hermes 当前使用的结构，也会在只有一个 Registry 依赖时过早引入对象状态。

## Decision Brief

### 方案 A：新增 ToolExecutor 类

优点：依赖注入直观，未来可保存 checkpoint 等状态。

风险：与 Hermes 当前模块级函数结构不一致；本批没有需要保存在实例中的执行状态。

### 方案 B：模块级顺序执行器

新增 `agent/tool_executor.py`，使用接收 `agent` 的模块级函数；AIAgent 保留薄调用入口，registry dispatch 继续由 `model_tools` 承担。

优点：与 Hermes 当前职责和依赖方向一致；改动小；后续可在同一模块增加 checkpoint、并发或 segmented execution，而不改变本批入口。

风险：执行器会读取 AIAgent 的 `registry`、`valid_tool_names` 和现有解析 helper，模块与 agent 协议存在显式耦合。这与 Hermes 当前设计相同，需要通过小而稳定的调用面控制。

### 方案 C：只在现有 AIAgent 循环中增加名称检查

优点：代码改动最少。

风险：参数解析、范围阻断、执行和 tool message 组装仍留在 conversation loop，checkpoint 和 terminal 接入时没有统一执行位置。

## 决策

采用方案 B。

复刻重点是 Hermes 的职责边界：conversation loop 负责 Provider 轮次，tool executor 负责一批 tool call 的执行编排，model_tools/registry 负责单个工具的实际分发。

## 文件和接口

新增：

```text
src/learn_hermes_agent/agent/tool_executor.py
```

最小接口：

```python
def execute_tool_calls_sequential(
    agent: AIAgent,
    assistant_message: ChatMessage,
    messages: list[ChatMessage],
    *,
    tool_context: ToolExecutionContext | None = None,
) -> None:
    ...
```

内部 helper：

```python
def _parse_tool_arguments(
    raw_arguments: Any,
) -> tuple[dict[str, Any], str | None]:
    ...
```

本批不暴露 executor 类，不创建 executor 实例，也不增加并发入口。

## AIAgent 变化

`AIAgent.__init__()` 增加：

```python
self.valid_tool_names: set[str] = set()
```

每次 Provider 请求前：

1. 调用 `registry.get_definitions()`。
2. 从这批实际定义中生成 `valid_tool_names`。
3. 将同一批定义作为 `tools` 参数发送给 Provider。

该名称集合必须来自实际发送的 definitions，不能使用 `registry.names()`，也不能在收到 tool call 后重新运行 `check_fn`。否则，被 `check_fn` 隐藏的工具仍可能执行，或者环境变化会让发送时和执行时的范围不一致。

Provider 返回 tool calls 后，AIAgent 仍先追加 assistant message，再调用 `execute_tool_calls_sequential()`；逐个调用和 tool message 追加从 AIAgent 移入 executor。

## 顺序执行数据流

```text
Provider tool_calls
  -> AIAgent append assistant(tool_calls)
  -> execute_tool_calls_sequential(agent, ...)
       -> 解析 tool_call 的 name / arguments / id
       -> name 是否属于 agent.valid_tool_names
       -> 解析 arguments 为 JSON object
       -> safe_handle_function_call(...)
            -> registry lookup
            -> 现有安全 preflight
            -> handler dispatch
            -> JSON 序列化
       -> append role=tool message
  -> 下一轮 Provider 请求
```

执行顺序严格遵守模型输出顺序。本批不分析工具是否可并发。

## 工具范围语义

- `valid_tool_names` 是当前 Provider 请求实际看到的工具名称快照。
- 名称不在快照中时，不调用 registry、preflight 或 handler。
- 阻断结果作为该 `tool_call_id` 对应的 tool message 返回给模型。
- 被 `check_fn=False` 隐藏的工具仍保留在 Registry，但模型不能通过 tool call 执行。
- CLI `call-tool` 不经过 `AIAgent.valid_tool_names`，继续允许直接调用 Registry 中的工具，但仍经过现有安全 preflight。

这关闭了 Batch 3 明确保留的缺口：模型凭空生成一个已注册但本轮未暴露的工具名时，不再执行该工具。

## 参数解析语义

模型参数解析遵守以下规则：

- `None`、空字符串或纯空白字符串视为 `{}`。
- 已经是 `dict` 时直接使用，兼容当前内部调用。
- JSON object 字符串解析为字典。
- 非法 JSON、JSON array、字符串、数字或其他非 object 值均不执行工具。
- 解析失败返回 Hermes 风格结构化错误：

```json
{
  "error": "Invalid tool arguments",
  "message": "Tool arguments must be a valid JSON object; tool was not executed."
}
```

`model_tools._parse_arguments()` 暂时保留，因为 CLI `call-tool` 仍直接传入 JSON 字符串。executor 解析的是模型输出边界，model_tools 解析的是兼容分发入口，两者调用场景不同。

## 错误与兼容行为

- 无效模型工具名称：返回 tool error，不执行 handler。
- 无效模型参数：返回 tool error，不执行 handler。
- Registry lookup、preflight 或 handler 异常：继续由 `safe_handle_function_call()` 转成 `{"error": ...}`。
- 每个具有有效 `tool_call_id` 的 assistant tool call 必须追加且只追加一个 tool result。
- 一个工具失败不阻止本批后续工具继续顺序执行。
- Provider 返回缺少 id 或 function object 的畸形调用仍视为 Provider 协议错误；没有可靠 id 时不能伪造 tool result 配对。
- `model_tools.handle_function_call()` 保持严格入口，CLI 参数或未知工具错误继续由 CLI 捕获并返回退出码 1。
- `model_tools.safe_handle_function_call()` 保持模型执行的安全包装入口。

## 本批不实现

- concurrent 或 segmented execution。
- tool name 自动修复、重试计数或 fuzzy matching。
- middleware、plugin hooks 或 guardrails。
- checkpoint。
- terminal。
- tool result 持久化、预算裁剪、进度 callback 或 interrupt。
- dynamic registry refresh、MCP 或 tool search bridge。

## 风险与回滚

- 风险：AIAgent 与 executor 通过少量属性和 helper 耦合。控制方式是只使用 `registry`、`valid_tool_names`、`_get_tool_calls()` 和 `_parse_tool_call()`。
- 风险：定义和名称快照若分两次重算会不一致。实现必须从同一个 `tools` 列表派生名称。
- 风险：空集合不能被当作“未限制”。`valid_tool_names=set()` 必须表示模型没有任何可调用工具。
- 回滚：AIAgent 可恢复原 for-loop；`model_tools` 和所有 handler 接口没有变化，不需要回滚工具实现。

## 验收标准

- `AIAgent` 从实际 definitions 形成 `valid_tool_names`。
- 可见工具正常执行并产生配对 tool message。
- `check_fn=False` 的已注册工具不能被模型调用，handler 未执行。
- 非法 JSON 和非 object JSON 返回结构化 tool error，handler 未执行。
- 同一批中一个调用失败后，后续有效调用仍执行。
- CLI `call-tool` 的成功和退出码错误语义保持不变。
- 现有路径 preflight、`tools`、tool demo 和八个内置工具行为保持不变。
- `compileall` 通过，不新增测试文件。

