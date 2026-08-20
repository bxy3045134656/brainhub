# BrainHub — 统一 Web 前端 + 网盘 + MCP 网关 + 运维 agent

> Brain 产品族的**自用主入口**：一个 FastAPI 单进程聚合所有面板（知识库 / 搜索 / 网盘 / 看板 / 记忆 / agent 状态 / 运维日志），对外提供**单一 MCP 网关**，内置运维 agent 自动维护知识库与记忆。

## 简介

BrainHub 是 Brain 产品族的使用入口，负责把产品族全部能力收拢到一个可操作的界面与一套 API 里：

- **Web 面板**：Jinja2 + HTMX 零前端构建，知识库浏览 / 语义搜索 / 网盘 CRUD / 看板任务状态机 / 记忆 / agent 状态 / 运维日志
- **JSON API v1**：`/api/v1/*` 供桌面端 React 前端调用
- **MCP 网关**：聚合 BrainHub 自有工具 + BrainMem 检索工具共 12 个，任何 agent 只连这一个入口
- **运维 agent**：定时归档、索引重建、记忆提取、健康检查

自用期一个 `brainhub start` 把主进程全部拉起：import brainmem 当检索后端、拉起 brain-bridge 子进程，对外像一个软件。

## 特性

- 🖥️ **统一面板** — 7 类页面聚合在一个服务，HTMX 局部刷新 + Alpine 轻交互，无需前端构建链
- 🔌 **单一 MCP 入口** — `write_note` / `read_file` / `list_files` / 项目状态机 / `health_check` / `send_matrix_msg` + 转发 BrainMem 检索工具
- 🤖 **运维 agent** — APScheduler 定时：归档 / 索引重建 / 记忆提取 / 健康检查，无需外部编排
- 📁 **网盘** — BRAIN_ROOT 文件浏览、上传下载、缩略图懒生成、越界与敏感路径拦截
- 📋 **看板** — 项目/任务状态机（todo → doing → blocked → done，严格流转）
- 🔗 **协作对接** — 经命名管道把 `send_matrix_msg` 转发给 BrainBridge daemon，前端 WS 实时收 Matrix 消息与 agent 状态
- 💻 **桌面端同源挂载** — `/app` 挂载 brainhub-desktop 构建产物，同源免 CORS

## 架构组成

| 目录 | 职责 |
|---|---|
| `web/` | FastAPI 应用 + Jinja2 模板 + WS；`routes/api.py` 为桌面端 JSON API v1 |
| `storage/` | 文件 CRUD + 缩略图、`write_note` 归档、共享 memory.db 的懒加载 Store |
| `projects/` | 项目/任务状态机模型 |
| `ops/` | 运维 agent：记忆提取、OpsAgent 接口、APScheduler 定时 |
| `pipe/` | 命名管道客户端（与 BrainBridge 通信，envelope 对齐协议宪法 §3） |
| `mcp.py` | fastmcp 网关：自有工具 + 转发 BrainMem 工具 |
| `cli.py` | `brainhub start / stop / status` + `ops` 子命令 |

## 快速开始

```bash
# 安装（Python 3.10+，uv 管理；依赖 BrainMem editable 安装）
uv sync
uv pip install -e ../brainmem

# 启动（拉起 Web + MCP 网关 + brain-bridge 子进程）
uv run brainhub start

# 运维子命令
uv run brainhub ops archive
uv run brainhub ops reindex
uv run brainhub ops health
```

## 文档索引

- [PLAN.md](PLAN.md) — 实现计划与**当前进度**（Phase 1-3 验收、面板、运维 Cron、MCP 工具、依赖）
- [docs/protocol-constitution.md](docs/protocol-constitution.md) — 协议宪法：MCP 工具签名 / memory.db schema / Matrix envelope
- [../brainmem/PRODUCT_FAMILY_PLAN.md](../brainmem/PRODUCT_FAMILY_PLAN.md) — 产品族全局 plan（跨产品边界、HX470 迁移、风险权衡）

## 相关项目

Brain 产品族：[BrainMem](https://github.com/bxy3045134656/brainmem)（记忆引擎）· **BrainHub**（本仓库）· [BrainBridge](https://github.com/bxy3045134656/brainbridge)（协作网关）
