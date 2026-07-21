# 项目总览

## 目标

本项目用于系统学习 Hermes Agent，并用 Python 在当前仓库中从 0 到 1 复刻它。最终目标是达到接近 1:1 的设计和行为理解，而不是只做一个简化聊天机器人。

## 约束

- 使用 `uv` 管理 Python 环境和依赖。
- 文档用中文维护，便于跨 session 继续。
- 参考源码在 `D:\python-develop\project\hermes-agent`，只读分析，不修改。
- 当前实现代码应写在 `D:\python-develop\project\learn_hermes_agent`。
- 默认不主动新增测试；除非用户明确要求。验证以接口一致性、轻量不变量、手工运行和可观察行为为主。

## 决策简报

### 选项 A：逐文件照抄 Hermes

优点是短期最接近 1:1。缺点是学习成本最高，尤其 Hermes 的 `run_agent.py`、`cli.py`、`hermes_state.py`、`gateway/run.py` 都是大文件，直接复制会把 agent loop、provider 适配、工具协议、session 存储、gateway 并发、插件 hook 混在一起，难以理解每个设计为什么存在。

### 选项 B：按承重链路分阶段复刻

先实现最小可运行 agent loop，再逐步加入工具、session、prompt、provider、CLI、memory、gateway、plugins、ACP/TUI 等能力。优点是每阶段都有明确学习目标和可验证边界；缺点是早期不会马上 1:1，但能逐步逼近。

### 选项 C：只写学习笔记，不复刻实现

优点是最快形成概念理解。缺点是无法真正掌握 Hermes 的协议细节，例如 tool call 的消息配对、system prompt 缓存稳定性、SQLite FTS5 session 搜索和 gateway 运行态隔离。

## 推荐

采用选项 B：按承重链路分阶段复刻。

Hermes 的核心不是“有很多工具”，而是一个可跨入口复用的 agent runtime：

```text
CLI / Gateway / ACP / TUI / Cron
        ↓
      AIAgent
        ↓
conversation loop
        ↓
provider transport + tool registry + session store + prompt builder
```

我们应该先实现这个骨架，然后再补全高级能力。这样每一层都能解释清楚、跑起来、再和源码对照。

## 官方与源码证据

官方开发文档将 `AIAgent` 定位为核心运行单元，并把 CLI、Gateway、API、ACP、Cron 等入口视为外围接口。源码中的项目脚本也印证了这一点：

- `hermes = "hermes_cli.main:main"`
- `hermes-agent = "run_agent:main"`
- `hermes-acp = "acp_adapter.entry:main"`

最新版 Hermes 源码中的工具执行承重链路为：

```text
CLI / Gateway / ACP / TUI
  -> AIAgent
  -> agent/conversation_loop.py
  -> agent/tool_executor.py
  -> model_tools.py
  -> tools/registry.py
  -> tools/*.py
```

这条链是复刻的第一优先级。

## 当前实现进度

截至 2026-07-21，学习项目已经完成：

- Phase 0 至 Phase 8 的最小可运行版本。
- Phase 9：最小 Provider Runtime、OpenAI-compatible provider、规范化 response 和 fallback chain。
- Phase 10 Batch 1：ToolExecutionContext、命令审批、文件路径安全和 dispatch preflight。
- Phase 10 Batch 2A 至 2E：`read_file`、`write_file`、文件工具加固、`patch` 和 `search_files`。
- Phase 10 Batch 3：ToolRegistry v2，增加 `toolset`、`check_fn`、registry `generation`、可用定义过滤和 toolset 查询。

下一阶段是 Phase 10 Batch 4：Minimal ToolExecutor。目标是把参数解析、安全 preflight、handler dispatch 和结构化错误从 `model_tools.py` 收口到独立执行层，同时保留兼容包装入口：

```text
Minimal ToolExecutor
  -> Minimal Checkpoint
  -> Local Foreground Terminal
```

当前尚未实现 terminal、checkpoint、并发工具执行或 Hermes 完整 registry 动态能力。

## 文档地图

- `docs/01-architecture-analysis.md`：Hermes 架构分析。
- `docs/02-roadmap.md`：阶段路线图。
- `docs/03-source-reading-index.md`：源码阅读顺序。
- `docs/04-progress-handoff.md`：跨 session 进度交接。
- `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`：实施计划。
- `docs/plans/2026-07-21-phase-10-post-file-tools-route-design.md`：文件工具完成后的架构路线。
- `docs/plans/2026-07-21-phase-10-batch-2e-search-files-closeout-plan.md`：Batch 2E 收尾计划。
- `docs/plans/2026-07-21-phase-10-batch-3-tool-registry-v2-design.md`：Batch 3 Registry v2 设计。
- `docs/plans/2026-07-21-phase-10-batch-3-tool-registry-v2-plan.md`：Batch 3 实施计划。
