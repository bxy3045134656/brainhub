# BrainHub — 统一 Web 前端 + 网盘 + MCP 网关 + 运维 agent

> Brain 产品族的自用主入口产品。聚合所有面板（知识库/搜索/网盘/看板/记忆/agent状态/运维日志），网盘文件 CRUD，MCP 网关聚合对外，内置运维 agent（自动归档/索引重建/记忆提取/健康检查）。
> 依赖 [BrainMem](../brainmem/)（import 当库用，调其检索/记忆 API + 共享 memory.db）。

## 当前状态

**Phase 2 — 统一前端 + 网盘 + 运维 agent（已交付，6 项验收通过）**

BrainMem 已验收完成（Phase 1+2+3 全做完：知识库 RAG + 记忆六层 + 图谱 + 三路混合检索 + MCP 工具 + 测试）。BrainHub import brainmem 当库用，共享 `d:\braindata\memory.db`。

交付物（均已实现）：
- [brainhub/cli.py](brainhub/cli.py) — `brainhub start/stop/status` + `brainhub ops archive/extract-memories/reindex/health`（typer，PID 文件管进程）
- [brainhub/web/](brainhub/web/) — FastAPI + Jinja2 + HTMX + Alpine + Tailwind(CDN)，7 面板 + WS：知识库浏览/搜索/网盘/看板/记忆/agent状态/运维日志
- [brainhub/storage/](brainhub/storage/) — [files.py](brainhub/storage/files.py) 文件 CRUD+缩略图（Pillow/pdf2image 懒生成）+ [archive.py](brainhub/storage/archive.py) `write_note` 归档（硬编码完整 Index.md 规则 + 漂移单测）+ [db.py](brainhub/storage/db.py) 懒加载单例 Store（镜像 brainmem.mcp）+ 独立 hub_conn（WAL 双连接同库）
- [brainhub/projects/](brainhub/projects/models.py) — 项目/任务状态机（todo→doing→blocked→done，严格流，非法转移 raise）+ 拖拽落点
- [brainhub/ops/](brainhub/ops/) — [extract.py](brainhub/ops/extract.py) 记忆提取（OpenClaw trajectory.jsonl → AsyncAnthropic 直调 → memory.db）+ [agent.py](brainhub/ops/agent.py) OpsAgent 接口壳（ReAct 留 Phase 3）+ [cron.py](brainhub/ops/cron.py) APScheduler（归档02:30/索引03:00/提取23:00/健康5min/Index更新03:30）
- [brainhub/mcp.py](brainhub/mcp.py) — fastmcp 网关 11 工具：BrainHub 自有 7 个（write_note/read_file/list_files/list_projects/query_project/update_project/health_check）+ 转发 brainmem 4 个（search_knowledge/query_memory/write_memory/reindex）

**验收**（已跑通）：① Web 目录树+语义搜索 ② Inbox 归档到 3-Knowledge/FPGA ③ extract-memories 写 memory.db ④ query_memory 返回健康事实+心跳异常实体 ⑤ 看板建项目+拖拽状态机 ⑥ MCP 11 工具全注册+health_check 全绿。18 个单测全过。

**按计划降级**：OpsAgent 只留接口壳（ReAct 循环主体留 Phase 3），extract-memories 走直调 AsyncAnthropic 路径（已验证 LLM 网关 + bge 编码 + 实体抽取跑通）。详见 [PLAN.md](PLAN.md)。

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
- `brainmem.store.Store` — 共享 memory.db（WAL + autocommit，同路径打开）。构造：`Store(db_path=BRAIN_DATA/memory.db, brain_data=BRAIN_DATA)`
- `brainmem.searcher.Searcher` — `Searcher(store, config).search(query, k, time_range)` / `.query_memory(query, k, layers, time_range)`
- `brainmem.memorize.Memorize` — `Memorize(store).write_memory(content, layer, entities, importance, tags, source)` / `.forget(layer, ...)` / `.compress_working_to_episodic(...)`。注意是**类**不是模块函数
- `brainmem.indexer.Indexer` — `Indexer(store).index_root(root, full)` / `.index_roots([roots], full)`（多根）
- `brainmem.searcher.load_config()` — 读 `BRAIN_DATA/config.toml`（可选，有默认值）

对外 MCP 工具：BrainHub 自己实现的（write_note/read_file/list_files/projects/health_check）+ 转发 BrainMem 的（search_knowledge/query_memory/write_memory/reindex）。

## 开发环境

- Windows 11, Python 3.10.20, uv 0.11.17
- 依赖：fastapi, uvicorn[standard], jinja2, python-multipart, anthropic, watchdog, Pillow, pdf2image, httpx, apscheduler
- 前置：BrainMem 已 `pip install -e ../brainmem`（本地 editable 装好）
