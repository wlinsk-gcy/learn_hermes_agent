# learn_hermes_agent 工作约定

本项目的目标是用 Python 从 0 到 1 复刻 Hermes Agent，并通过手写实现理解它的设计。

## 固定上下文

- 学习项目：`D:\python-develop\project\learn_hermes_agent`
- 参考源码：`D:\python-develop\project\hermes-agent`
- 官方文档：https://hermes-agent.nousresearch.com/docs
- 包管理器：`uv`
- 文档语言：中文

## 工作原则

1. 先理解 Hermes 的架构意图，再写代码。
2. 每当开始一轮新 phase 的设计时，必须先对齐 `D:\python-develop\project\hermes-agent` 中对应模块的设计和实现，再给出本项目的简化复刻方案。
3. 不直接修改参考源码仓库。
4. 当前学习项目按阶段实现，不做无边界大重构。
5. 默认不主动新增测试；除非用户明确要求测试工作。验证优先使用接口一致性、手工运行、schema 检查、运行行为观察和轻量不变量。
6. 每个新 session 开始时先阅读：
   - `docs/00-overview.md`
   - `docs/02-roadmap.md`
   - `docs/04-progress-handoff.md`
   - 最新的 `docs/plans/*.md`

## 代码输出原则

- 文档的编写和维护可以由 Codex 直接写入文件。
- 代码实现默认只由 Codex 在对话中输出代码块，不直接写入项目文件。
- 用户会手动抄写代码；遇到不懂或不理解的地方会主动询问。
- 只有当用户明确要求“帮我写文件”“帮我改代码”“帮我写测试”等操作时，Codex 才写入代码文件或测试文件。
- 因此后续实现阶段的默认交付物是：设计说明、文件路径建议、代码片段、运行命令和手工验证步骤。

## 架构复刻方向

推荐路线是“阶段式功能等价”，不是“一开始逐文件复制”。

原因：

- Hermes 的核心入口 `run_agent.py`、`cli.py`、`hermes_state.py` 都是大型文件，直接复制会掩盖模块边界。
- 最新参考源码的承重链路是：`CLI/Gateway/ACP/TUI -> AIAgent -> agent/conversation_loop.py -> agent/tool_executor.py -> model_tools.py -> tools/registry.py -> tools/*.py`。
- 逐层复刻可以在每个阶段形成可运行闭环，便于验证和纠偏。

## 当前状态

截至 2026-07-22：

- 已重新分析最新版 Hermes 源码及 Registry / ToolExecutor 承重链路。
- 已建立中文路线图和 session 交接文档。
- Phase 0 至 Phase 8 已完成当前学习路线的最小版本。
- Phase 9 Provider Runtime 已完成最小版本。
- Phase 10 Batch 1 Safety / Approval Primitives 已完成。
- Phase 10 Batch 2A 至 2E File Tools With Safety 已完成：`read_file`、`write_file`、`patch`、`search_files`。
- Phase 10 Batch 3 ToolRegistry v2 已完成：`toolset`、`check_fn`、`generation` 和可用定义过滤已接入。
- Phase 10 Batch 4 Minimal ToolExecutor 已完成：已建立 Provider definitions 范围快照和模块级顺序执行器。
- Phase 10 Batch 5 Minimal Checkpoint 已完成：配置、共享 shadow Git store、快照/去重/列举/裁剪、Manager 级恢复、`AIAgent` iteration 生命周期和安全 preflight 后的自动写前 checkpoint 已实现。
- `terminal`、checkpoint CLI、rollback UX、并发或 segmented ToolExecutor 尚未实现。

下一步是 Phase 10 Batch 6 Local Foreground Terminal。开始设计前必须重新对齐最新版 Hermes 的 terminal、approval 和 execution backend 源码；先完成独立设计，不直接实现。
