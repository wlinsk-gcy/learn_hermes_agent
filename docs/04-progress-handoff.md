# 进度与 Session 交接

## 当前日期

2026-06-03

## 当前状态

已完成：

- 读取官方文档入口：https://hermes-agent.nousresearch.com/docs
- 读取官方开发文档中的架构、agent loop、provider runtime、session storage、gateway internals。
- 使用本地代码索引分析 `D:\python-develop\project\hermes-agent`。
- 确认本地源码规模：
  - 约 2951 个文件。
  - Python 文件约 2049 个。
  - 核心模块包括 `run_agent.py`、`agent/`、`tools/`、`model_tools.py`、`hermes_state.py`、`cli.py`、`hermes_cli/`、`gateway/`、`acp_adapter/`、`tui_gateway/`、`plugins/`。
- 确认当前学习项目基本为空：
  - `README.md`
  - `.gitignore`
  - 未跟踪 `.idea/`
- 已创建中文文档：
  - `AGENTS.md`
  - `docs/00-overview.md`
  - `docs/01-architecture-analysis.md`
  - `docs/02-roadmap.md`
  - `docs/03-source-reading-index.md`
  - `docs/04-progress-handoff.md`
  - `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`

尚未完成：

- 尚未初始化 uv 项目。
- 尚未写任何实现代码。
- 尚未建立 Python 包目录。

## 已确认的关键设计结论

1. Hermes 的核心是 `AIAgent`，不是 CLI 或 Gateway。
2. `run_agent.py` 中的 `AIAgent.run_conversation()` 已经转发到 `agent/conversation_loop.py`，真实 loop 在后者。
3. 工具承重链路是：

   ```text
   tools/registry.py
     -> tools/*.py
     -> model_tools.py
     -> run_agent.py / cli.py / gateway / acp / batch
   ```

4. OpenAI 风格消息协议是工具调用的核心：

   ```text
   assistant(tool_calls) -> tool(tool_call_id)
   ```

   任何复刻实现都必须维护这个配对，不然后续 provider 请求会坏。

5. system prompt 必须 session 内稳定。Hermes 通过缓存和 session DB 持久化 system prompt 来保护 prefix cache。
6. session store 是跨 CLI/Gateway/ACP/TUI 的基础。SQLite WAL + FTS5 是必须理解的设计。
7. Gateway 的难点在运行态控制，不是平台 API 本身。
8. Memory 和 Skills 是 Hermes “自改进”定位的核心，但应该在 agent loop、tool loop、session、prompt 稳定后实现。

## 下一个 session 的推荐操作

从 Phase 0 开始，不要直接写 agent loop。

建议步骤：

1. 阅读 `docs/00-overview.md`。
2. 阅读 `docs/02-roadmap.md`。
3. 阅读 `docs/plans/2026-06-03-hermes-agent-learning-roadmap.md`。
4. 初始化 uv 项目。
5. 建立 `src/learn_hermes_agent/` 包结构。
6. 实现最小命令入口，保证 `uv run python -m learn_hermes_agent --help` 可运行。

## 重要约束

- 不修改 `D:\python-develop\project\hermes-agent`。
- 不把 Hermes 源码整块复制进来。需要先按模块边界复刻。
- 默认不写测试，除非用户明确要求。
- 除文档编写和维护外，代码实现默认只在对话中输出，不写入文件。用户会手动抄写代码；只有用户明确要求写文件、改代码或写测试时，才执行文件写入。
- 每阶段完成后更新本文件，写清楚：
  - 完成了什么。
  - 哪些源码文件已对照。
  - 当前行为如何验证。
  - 下一个阶段是什么。

## 2026-06-03 进度更新

### 本次目标

同步协作原则：文档可由 Codex 维护，代码实现默认只输出不落盘。

### 已完成

- 更新 `AGENTS.md`，新增“代码输出原则”。
- 更新本交接文档的重要约束。

### 设计结论

后续进入实现阶段时，Codex 默认提供代码片段、文件路径建议、运行命令和手工验证步骤；不主动写入代码文件。

### 下一步

继续从 Phase 0 开始，但默认以代码块形式输出 `pyproject.toml`、包入口和最小 CLI 的内容，等用户手动抄写。

## 后续进度模板

复制以下模板追加到本文件末尾：

```markdown
## YYYY-MM-DD 进度更新

### 本次目标

### 已完成

### 修改文件

### 对照的 Hermes 源码

### 验证方式

### 设计结论

### 下一步
```
