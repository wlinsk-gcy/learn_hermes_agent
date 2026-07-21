# Phase 10 文件工具完成后的架构路线设计

## 目标

在 Phase 10 文件工具闭环完成后，重新对齐最新版 Hermes 的工具注册和执行链路，确定 checkpoint 与 terminal 之前必须补齐的承重层，避免继续把高风险工具逻辑堆入 `model_tools.py`。

## 当前状态

- Batch 1 已完成 Safety / Approval Primitives。
- Batch 2A 已完成最小 `read_file`。
- Batch 2B 已完成受控 `write_file`。
- Batch 2C 已完成文件展示文本回写防护。
- Batch 2D 已完成精确字符串替换版 `patch`。
- Batch 2E 已完成最小 `search_files`，包括正则搜索、结果限制、统一读路径 preflight 和候选文件级安全过滤。
- 尚未实现 terminal、checkpoint、并发工具执行、middleware 或 guardrails。

## 最新 Hermes 对齐结论

最新版 Hermes 的工具链已经形成两层明确边界：

- `tools/registry.py` 负责工具元数据、toolset、可用性检查和 registry generation。
- `agent/tool_executor.py` 负责参数解析、阻断判断、guardrail、checkpoint preflight 和实际执行。

当前学习项目仍然是：

```text
model_tools.py
  -> 参数解析
  -> 安全 preflight
  -> registry lookup
  -> handler execution
```

如果现在直接加入 terminal 或 checkpoint，`model_tools.py` 会继续承担越来越多运行态职责，不利于后续复刻 Hermes 的 executor 设计。

## Decision Brief

### 选项 A：直接实现 terminal

优点：可以最快观察命令执行。

风险：审批、阻断、timeout、checkpoint 和执行结果组装会集中到现有 dispatch 层，后续拆分成本高。

### 选项 B：先实现 checkpoint

优点：文件修改具备回滚基础。

风险：当前没有独立 executor，checkpoint 的触发点和“一轮只创建一次”等状态容易侵入 `model_tools.py` 或工具 handler。

### 选项 C：先升级 Registry，再提取 ToolExecutor

优点：先稳定元数据和执行边界，后续 checkpoint、terminal、guardrails 都有统一接入点；与最新版 Hermes 的设计意图一致。

代价：terminal 的可见能力继续延后两个小批次。

## 决策

采用选项 C。

推荐顺序：

1. Batch 2E 收尾：完整验收并更新交接文档。
2. Batch 3：ToolRegistry v2。
3. Batch 4：最小 ToolExecutor。
4. Batch 5：最小 checkpoint。
5. Batch 6：local foreground terminal。

## Batch 3 边界

只实现：

- `ToolEntry.toolset`，提供兼容默认值。
- `ToolEntry.check_fn`，允许工具声明轻量可用性检查。
- `ToolRegistry.generation`，本批在每次成功注册或覆盖工具时递增；移除工具的接口推迟到动态工具阶段。
- 按 toolset 查询工具和可用工具定义的最小接口。

暂不实现：

- check_fn TTL / failure grace cache。
- dynamic schema overrides。
- MCP ownership、toolset alias、deregister 权限策略。
- plugin 动态工具注册。

## Batch 4 边界

只实现：

- 模块级顺序 tool-call executor，与最新版 Hermes 的 `agent/tool_executor.py` 结构对齐。
- 从实际发送给 Provider 的 definitions 形成 `valid_tool_names`。
- 模型工具名称范围检查和参数 JSON object 解析。
- 顺序调用现有 `model_tools.safe_handle_function_call()`，并追加配对 tool result。
- `model_tools` 继续保留 registry dispatch、统一安全 preflight 和 CLI 兼容入口。

暂不实现：

- 并发或 segmented execution。
- middleware、hooks、tool search scope。
- guardrail loop detection。
- checkpoint。

## 风险与回滚

- Registry v2 使用带默认值的新字段，现有工具注册代码无需一次性迁移；如有问题可忽略新增字段。
- ToolExecutor 提取期间保留 `model_tools.handle_function_call()`，调用方无需同步迁移。
- checkpoint 和 terminal 继续保持未注册状态，前两批出现问题时不会扩大执行权限。

## 验收标准

- Batch 2E 的文件工具行为有完整、可重复的手工验证记录。
- roadmap 与 handoff 不再停留在 Batch 1 / Batch 2C。
- 下一批计划明确先做 ToolRegistry v2，不实现 terminal 或 checkpoint。
- 后续每批仍按接口一致性、`compileall`、CLI 手工行为和轻量不变量验证，不默认新增测试文件。
