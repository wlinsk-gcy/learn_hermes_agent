# learn_hermes_agent

这个项目用于从 0 到 1 用 Python 复刻并学习 Hermes Agent。

参考源码仓库：

- 本地源码：`D:\python-develop\project\hermes-agent`
- 官方文档：https://hermes-agent.nousresearch.com/docs

当前策略不是一开始逐文件照抄，而是先复刻 Hermes 的承重架构：

1. `AIAgent` 对话循环
2. provider/runtime 抽象
3. OpenAI 格式消息协议与 tool call 配对
4. 工具注册、工具分发、toolset
5. SQLite session store 与 FTS5 检索
6. system prompt 分层与缓存稳定性
7. CLI / Gateway / ACP / TUI 等入口逐层接入
8. memory、skills、plugins、context compression、cron、batch trajectory 等高级能力

中文计划与交接文档见：

- [项目总览](docs/00-overview.md)
- [Hermes Agent 架构分析](docs/01-architecture-analysis.md)
- [复刻路线图](docs/02-roadmap.md)
- [源码阅读索引](docs/03-source-reading-index.md)
- [进度与 Session 交接](docs/04-progress-handoff.md)
- [阶段实施计划](docs/plans/2026-06-03-hermes-agent-learning-roadmap.md)
