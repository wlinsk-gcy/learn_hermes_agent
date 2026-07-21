# Phase 10 Batch 3 ToolRegistry v2 设计

## 目标

在不改变现有工具执行链路的前提下，为 `ToolRegistry` 增加 toolset 元数据、轻量可用性检查和 registry generation，为下一批提取最小 `ToolExecutor` 建立稳定边界。

本批只增强“工具如何被描述、分类和暴露给模型”，不增强“工具如何被执行”。

## 当前结构

当前学习项目的 `ToolEntry` 只包含：

- `name`
- `description`
- `parameters`
- `handler`

`ToolRegistry.list_definitions()` 会无条件向模型返回所有已注册工具。`model_tools.handle_function_call()` 则通过 `registry.get()` 查找并直接调用 handler。

最新版 Hermes 已将这两个职责分开：

- `tools/registry.py` 管理工具元数据、toolset、可用性和 generation。
- `agent/tool_executor.py` 管理调用范围、安全 preflight 和实际执行。

因此，`check_fn` 在本项目中也只表示“当前环境是否具备使用该工具的条件”，不能被误用为审批或安全边界。

## 约束

- 不实现 `ToolExecutor`。
- 不实现 terminal、checkpoint、并发执行、middleware 或 guardrails。
- 不修改参考仓库。
- 不新增测试文件。
- 保持现有 `ToolEntry(...)` 注册方式和 `list_definitions()` 调用兼容。
- 代码仍由用户逐任务手抄，Codex 只直接维护文档。

## Decision Brief

### 方案 A：增量扩展当前 Registry

保留 `description + parameters + handler`，增加带默认值的 `toolset` 和 `check_fn`，并增加 generation、定义过滤和 toolset 查询接口。

优点：迁移范围小，现有注册代码继续有效；可以逐个不变量验证；与阶段式学习目标一致。

风险：不会立即拥有 Hermes Registry 的全部插件和并发能力。

### 方案 B：一次性复刻 Hermes Registry

将 `ToolEntry` 改为完整 schema 表示，并同时引入异步 dispatch、插件归属、TTL 缓存、动态 schema 和覆盖策略。

优点：表面接口最接近 Hermes。

风险：一次引入大量当前没有调用方的机制，难以区分承重能力与扩展能力，也会扩大回归范围。

### 方案 C：长期保留新旧两套注册 API

同时支持传入 `ToolEntry` 和 Hermes 风格的关键字注册。

优点：迁移灵活。

风险：同一份工具元数据存在两套表达和校验路径，增加理解与维护成本。

## 决策

采用方案 A。

这是一个兼容性增量，不复制 Hermes 当前尚未被学习项目使用的复杂机制。未来需要动态插件时，可以在不改变本批字段语义的情况下继续扩展。

## 数据模型

保留现有字段顺序，并在 `handler` 后增加：

```python
toolset: str = "other"
check_fn: ToolAvailabilityCheck | None = None
```

其中：

- `toolset` 是稳定的分类标签，不是权限边界。
- `check_fn` 是无参数、返回布尔值的轻量可用性检查。
- `check_fn=None` 表示工具始终可用。
- 现有工具不填写新字段时保持原行为。

本批内置工具分类为：

| 工具 | toolset |
| --- | --- |
| `echo` | `other`（使用默认值） |
| `read_file` / `write_file` / `patch` / `search_files` | `file` |
| `memory` | `memory` |
| `skills_list` / `skill_view` | `skills` |

当前所有内置工具都不设置 `check_fn`，因为它们没有可选 SDK、外部服务或独立运行时依赖。

## Registry API

### generation

`ToolRegistry` 使用私有 `_generation` 保存版本，并通过只读 `generation` property 暴露。

规则：

- 新 Registry 从 `0` 开始。
- 成功注册新工具后加 `1`。
- 使用 `override=True` 成功覆盖后加 `1`。
- 重复注册被拒绝时不增加。
- 本批不增加 `deregister()`；动态工具进入路线后再定义移除和所有权策略。

generation 在本批不驱动缓存。它先建立“Registry 结构发生变化”的稳定版本协议，下一批及后续缓存可以直接依赖该协议。

### get_definitions

新增主接口：

```python
get_definitions(tool_names: set[str] | None = None) -> list[dict[str, Any]]
```

规则：

- `tool_names=None` 时检查所有已注册工具。
- 传入名称集合时只考虑集合中的已注册工具；未知名称被忽略。
- 返回顺序继续按工具名排序。
- `check_fn=None` 的工具直接可见。
- `check_fn()` 返回 truthy 时可见，返回 falsy 时隐藏。
- `check_fn()` 抛出异常时记录 warning，并将工具视为不可用，不中断整个定义列表。
- 同一次 `get_definitions()` 中，共享同一个 `check_fn` 对象的工具只检查一次。
- 不做跨调用 TTL 缓存，避免外部环境变化后结果长期过期。

现有 `list_definitions()` 保留，内部委托给不带参数的 `get_definitions()`。

### toolset 查询

增加三个只读查询：

```python
get_registered_toolset_names() -> list[str]
get_tool_names_for_toolset(toolset: str) -> list[str]
get_toolset_for_tool(tool_name: str) -> str | None
```

所有列表都稳定排序。未知 toolset 返回空列表，未知工具返回 `None`。

## 数据流与安全边界

模型定义链路变为：

```text
ToolRegistry 中的全部 ToolEntry
  -> 可选名称过滤
  -> check_fn 可用性过滤
  -> OpenAI function definitions
  -> Provider
```

直接执行链路在本批保持不变：

```text
model_tools.handle_function_call()
  -> registry.get()
  -> safety preflight
  -> handler
```

因此，一个因 `check_fn=False` 而没有暴露给模型的工具，仍能被 `registry.get()` 和 CLI `call-tool` 找到。这是刻意保留的兼容行为，不是安全保证。

如果模型凭空返回一个本轮未暴露的工具名，当前链路仍可能执行它。该缺口必须在 Batch 4 的 `ToolExecutor` 中通过“本轮允许调用的工具名称集合”关闭，不能把审批职责塞进 `check_fn`。

## 错误处理

- 工具重复注册继续抛出当前 `ValueError`。
- 未知工具的 `get()` 继续抛出当前 `KeyError`。
- 可用性检查异常采用 fail-closed：隐藏对应工具并记录 warning。
- 一个工具检查失败不会阻止其他工具定义生成。
- handler 异常、参数解析和安全 preflight 均不在本批调整。

## 兼容与回滚

- 新字段都有默认值，现有工具可以分步添加 toolset。
- `list_definitions()` 保留，旧调用方不会立刻失效。
- `get()`、`names()`、`entries()` 的语义不变，仍包含当前不可用的工具。
- 若新过滤行为出现问题，可让调用方临时退回 `list_definitions()` 的旧实现；ToolEntry 的新增默认字段不会破坏注册代码。
- 本批不增加删除、插件所有权或动态注册机制，因此回滚不会涉及运行态资源清理。

## 验收标准

- 现有八个内置工具仍能发现和直接调用。
- `generation` 满足初始、成功注册、失败注册和覆盖四个不变量。
- 可用、不可用、检查异常三类工具得到正确定义过滤。
- 共享 `check_fn` 在一次定义查询中只执行一次。
- toolset 查询结果稳定且符合内置工具分类。
- `AIAgent` 和 `model_tools.get_tool_definitions()` 使用新主接口。
- `compileall`、`tools`、`call-tool echo` 和 tool demo 行为通过。
- 不新增测试文件，不实现 Batch 4 及之后的能力。

