# BrainHub — 统一 Web 前端 + 网盘 + MCP 网关 + 运维 agent

> Brain 产品族的自用主入口产品。聚合所有面板（知识库/搜索/网盘/看板/记忆/agent状态/运维日志），网盘文件 CRUD，MCP 网关聚合对外，内置运维 agent（自动归档/索引重建/记忆提取/健康检查）。
> 依赖 [BrainMem](../brainmem/)（import 当库用，调其检索/记忆 API + 共享 memory.db）。

## 当前状态

**Phase 2 — 统一前端 + 网盘 + 运维 agent（待开工）**

BrainMem 已验收完成（Phase 1+2+3 全做完：知识库 RAG + 记忆六层 + 图谱 + 三路混合检索 + 6 个 MCP 工具 + 测试）。BrainHub 现在可以安全地 import brainmem 当库用，接口已稳。

交付物：
- `brainhub/cli.py` — `brainhub start/stop/status`
- `brainhub/web/` — FastAPI + Jinja2 + HTMX + Alpine + Tailwind(CDN)，面板：知识库浏览/搜索/网盘/看板/记忆/agent状态/运维日志
- `brainhub/storage/` — 文件 CRUD + 元数据 + 缩略图 + `write_note` 归档（硬编码 Index.md 规则）
- `brainhub/projects/` — 项目/任务 CRUD + 状态机（todo→doing→blocked→done）
- `brainhub/ops/` — 运维 agent（Anthropic SDK + xopglm52）+ Cron（归档/索引/记忆提取/健康检查/Index.md 更新）
- MCP 工具：`write_note` / `read_file` / `list_files` / `list_projects` / `query_project` / `update_project` / `health_check`（聚合 BrainMem 的 search_knowledge/query_memory/write_memory/reindex 对外）

## 关键决策（已确认）

| 项 | 决策 | 理由 |
|---|---|---|
| 前端栈 | FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind(CDN) | 一人开发，零前端构建。HTMX 局部刷新 + Alpine 轻交互（拖拽/debounce/WS） |
| 运维 agent | 核心应用内起，Anthropic SDK + xopglm52 | 不复用 OpenClaw Gateway（链路长、Cron 自控、工具是内部 API 不绕 MCP）。ReAct 最多 10 步，失败 2 次换方法 |
| 归档规则 | 硬编码 Index.md 关键词→目录表 | 确定性、可解释，不靠 LLM 推断目录。LLM 只用于记忆提取 |
| 与 BrainMem 关系 | import brainmem 当库用 + 共享 memory.db | BrainMem 拥有 memory.db 写权，BrainHub ops 也写（同库靠 SQLite WAL 并发）。不重新实现检索 |
| 与 OpenClaw 边界 | OpenClaw 诺诺=对话/社交/梦境；BrainHub ops=知识库索引/记忆维护/归档 | 避免双写冲突。诺诺调知识库走 MCP 只读为主，写操作由 ops 统一 |
| 部署形态 | BrainHub 当主进程，拉起 brain-bridge 子进程（Phase 3） | 自用期一个 `brainhub start` 全拉起，对外像一个软件 |

## 文档索引

- [README.md](README.md) — 本文件，BrainHub 定位 + Phase 2 范围 + 关键决策
- [PLAN.md](PLAN.md) — BrainHub 实现计划（验收/面板/运维Cron/MCP工具/依赖）
- [docs/protocol-constitution.md](docs/protocol-constitution.md) — 协议宪法（MCP 工具签名 + memory.db schema + Matrix envelope，写代码前必读；与 BrainMem 同一份）
- [../brainmem/PRODUCT_FAMILY_PLAN.md](../brainmem/PRODUCT_FAMILY_PLAN.md) — 产品族全局 plan（跨产品参考，查 BrainBridge 边界/HX470 迁移/风险权衡）

## 与 BrainMem 的接口

BrainHub import brainmem，直接调这些（不是走 MCP，是进程内 Python 调用）：
- `brainmem.store.Store` — 共享 memory.db（同路径打开，WAL 并发）
- `brainmem.searcher.Searcher.search()` / `query_memory()` — 检索
- `brainmem.memorize.write_memory()` / `forget()` / `compress_working_to_episodic()` — 记忆写入
- `brainmem.indexer.Indexer.index_root()` — 增量摄取（ops Cron 调）

对外 MCP 工具：BrainHub 自己实现的（write_note/read_file/list_files/projects/health_check）+ 转发 BrainMem 的（search_knowledge/query_memory/write_memory/reindex）。

## 开发环境

- Windows 11, Python 3.10.20, uv 0.11.17
- 依赖：fastapi, uvicorn[standard], jinja2, python-multipart, anthropic, watchdog, Pillow, pdf2image, httpx, apscheduler
- 前置：BrainMem 已 `pip install -e ../brainmem`（本地 editable 装好）
