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
2. 不直接修改参考源码仓库。
3. 当前学习项目按阶段实现，不做无边界大重构。
4. 默认不主动新增测试；除非用户明确要求测试工作。验证优先使用接口一致性、手工运行、schema 检查、运行行为观察和轻量不变量。
5. 每个新 session 开始时先阅读：
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
- 真正需要学习的是承重链路：`tools/registry.py -> model_tools.py -> AIAgent.run_conversation() -> CLI/Gateway/ACP/TUI`。
- 逐层复刻可以在每个阶段形成可运行闭环，便于验证和纠偏。

## 当前状态

截至 2026-06-04：

- 已分析官方文档和本地源码结构。
- 已建立中文路线图和 session 交接文档。
- Phase 0 已完成：uv 项目、`pyproject.toml`、`src/` 包结构、最小 CLI、`doctor` 命令。
- Phase 1 已完成：最小 `AIAgent`、OpenAI 风格 message、`ProviderTransport` 协议、`FakeProviderTransport`、`chat` 命令。
- Phase 2 已完成：最小 `ToolRegistry`、`ToolEntry`、内置 `echo` 工具、tool schema 输出、`model_tools.handle_function_call()`、`tools` / `call-tool` CLI 命令。

下一步应执行 `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md` 的 Phase 3：Tool Calling Conversation Loop。
