# BrainHub 实现计划

> 摘自产品族 plan（`../brainmem/PRODUCT_FAMILY_PLAN.md`），只保留 BrainHub 相关部分。
> 新会话读这份就够开工。前置：BrainMem 已验收完成（接口已稳）。

## 当前进度

**Phase 1-3 已完成**，验收要点：
- ① Web 目录树 + 语义搜索 ② Inbox 归档到 3-Knowledge/FPGA ③ extract-memories 写 memory.db ④ query_memory 返回健康事实 + 心跳异常实体 ⑤ 看板建项目 + 拖拽状态机 ⑥ MCP 工具全注册 + health_check 全绿
- 运维 agent 按分阶段降级：Phase 2 只留 OpsAgent 接口壳，extract-memories 走直调 AsyncAnthropic，ReAct 循环留 Phase 3
- 与 BrainMem 接口以实际代码为准（Memorize 是类不是模块函数）
- 管道客户端就绪（BrainHub↔BrainBridge 命名管道，envelope 对齐宪法 §3；send_matrix_msg 实现方按宪法修订归 BrainHub）
- 近期（2026-08）：JSON API v1 路由（桌面端 React 用）、/app 挂载 brainhub-desktop dist、启动 PID+端口双重探活清理残留
- 27 个单测全过（archive/projects/db + 管道客户端回环）

## BrainHub 定位

Brain 产品族三款之一，自用主入口。统一 Web 前端聚合所有面板；网盘文件 CRUD；MCP 网关聚合对外；项目看板；运维 agent。

**依赖**：import brainmem 当库用 + 共享 memory.db。不重新实现检索/记忆，调 BrainMem 的 API。

**对外**：HTTP :7788 + WS /ws（Web UI）；MCP 网关聚合（可选 :7789）。

## Phase 2 交付（统一前端 + 网盘 + 运维 agent）

写（`d:\braincode\brainhub\`）：
- `pyproject.toml`（uv init，包名 brainhub，depends brainmem editable）
- `brainhub/cli.py` — `brainhub start/stop/status`
- `brainhub/web/app.py` — FastAPI 主应用，路由：`/` 知识库浏览、`/search` 搜索、`/files` 网盘、`/board` 看板、`/memory` 记忆、`/agents` agent状态、`/ops` 运维日志、`/ws` 状态推送
- `brainhub/web/templates/` — Jinja2 + HTMX + Alpine + Tailwind CDN（base.html + 各面板 partial）
- `brainhub/storage/files.py` — 文件 CRUD + `files` 元数据表 + 缩略图（Pillow/pdf2image）
- `brainhub/storage/archive.py` — `write_note` 归档（硬编码 Index.md 关键词→目录表，命中即停，兜底 Toolchain/）
- `brainhub/projects/models.py` — `projects` + `tasks` 表，状态机 todo→doing→blocked→done
- `brainhub/ops/agent.py` — 运维 agent 接口壳（Anthropic SDK + xopglm52，ReAct 留 Phase 3）。Phase 2 的 LLM 路径走 extract.py 直调 AsyncAnthropic，非 ReAct 循环。预留 max_steps=10、fail_switch=2，动作写 ops_log 可回溯
- `brainhub/ops/cron.py` — APScheduler Cron（归档/索引/记忆提取/健康检查/Index.md 更新）
- `brainhub/mcp.py` — fastmcp 网关，暴露 BrainHub 工具 + 转发 BrainMem 工具

## 验收标准

1. `brainhub start` 后 `http://localhost:7788` 能看 d:\Brain PARA 目录树，点 .md 预览，顶栏语义搜索（调 BrainMem search_knowledge）。
2. 往 `2-Inbox\` 丢 `2026-07-05_test.md` 含"FPGA Zynq CORDIC"，`brainhub ops archive` 后移到 `3-Knowledge\FPGA\`。
3. `brainhub ops extract-memories --date 2026-07-05`，memory.db 多出 episodic/semantic 记忆节点（六层：core/semantic/episodic/procedural/preference/working）。
4. `query_memory("鑫宇最近身体怎么样")` 返回体检事实 + 心脏问题实体关系（调 BrainMem）。
5. 看板 UI 能创建项目、拖拽任务改状态，持久化。
6. 运维日志面板实时显示 ops agent 动作（WS 推送）。

## 面板布局

```
┌──────────────────────────────────────────────────────────┐
│ BrainHub  [全局语义搜索框]              [agent状态灯●●●]   │
├──────────┬───────────────────────────────────────────────┤
│ 导航      │  主区（HTMX hx-target，tab 切换不刷新整页）     │
│ 知识库    │  ┌─知识库面板 ─────────────────────────────┐  │
│ 搜索      │  │ 目录树(PARA) │ 文件预览(MD渲染/PDF首页)   │  │
│ 网盘      │  │              │ 相关记忆(query_memory)     │  │
│ 项目看板  │  └──────────────┴────────────────────────────┘  │
│ 记忆      │  ┌─搜索面板─┐ ┌─网盘─┐ ┌─看板─┐ ┌─agent状态─┐  │
│ agent状态 │  └──────────┘ └──────┘ └──────┘ └───────────┘  │
│ 运维日志  │                                                │
└──────────┴───────────────────────────────────────────────┘
```

全局搜索框调 BrainMem `search_knowledge`；agent 灯接 WS `/ws`；目录树 HTMX `hx-get` 按需加载；看板 Alpine `x-data` 拖拽 + `hx-post` 改状态；运维日志面板 WS 推 ops_agent 实时输出。

## 运维 agent Cron

| 任务 | Cron | 动作 |
|---|---|---|
| Inbox 归档 | 每日 02:30 | 扫 `2-Inbox/`，按 Index.md 规则分类，移到 `3-Knowledge/{分类}/` |
| 索引重建 | 每日 03:00 | mtime 对比差异，调 BrainMem 增量摄取 |
| 记忆提取 | 每日 23:00 | 拉 OpenClaw 当天对话日志（`d:\openclaw\data\.openclaw\agents\{main,ace,sentinel}\sessions\*.trajectory.jsonl`，按 `sessions.json` 的 `lastActiveAt` 过滤），AsyncAnthropic（xopglm52）直调抽取，写 memory.db |
| 健康检查 | 每 5min | OpenClaw:18789 + HiClaw:18888 + 向量库 + 磁盘；异常 WS+Matrix 通知 |
| Index.md 更新 | 每日 03:30 | 重统计目录文件数，重写速览表 |

## MCP 工具签名（BrainHub 实现的部分）

| Tool | 输入 | 输出 |
|---|---|---|
| `write_note` | `{title, content, category?}` | `{path, archived_to}` |
| `read_file` | `{path, range?}` | `{path, content, mtime, sha256}` |
| `list_files` | `{dir, pattern?, recursive?=true}` | `{files:[...]}` |
| `list_projects` / `query_project` / `update_project` | `{...}` | `{...}` |
| `health_check` | `{}` | `{checks:[{name, ok, detail}]}` |

转发 BrainMem 的（BrainHub 网关代理）：`search_knowledge` / `query_memory` / `write_memory` / `reindex`。

**安全**：`read_file` 拒绝 `.trash/`、`secrets.json`；`list_files` 限制 d:\Brain 根下。

## 归档规则（硬编码自 Index.md）

按 `d:\Brain\Index.md` 的关键词→目录表，命中即停，兜底 Toolchain/：

| 规则关键词 | 目标目录 |
|---|---|
| OpenClaw/QwenPaw/Agent/记忆系统/DreamWeave/Heartbeat/Cron/技能/安全红线/harness | `Agent/` |
| LLM/DeepSeek/ViT/国产生图/AI市场/EvoMap/扩散模型/生成模型 | `AI-LLM/` |
| STM32/GD32/嵌入式/RTOS/UCOS/DMA/ADC/UART/SPI/TideMemo/油介损/电网监测 | `Embedded/` |
| FPGA/Verilog/CORDIC/FFT/FIR/Zynq/相位差/PID/pcm_audio/DDIO/FIFO/PLL | `FPGA/` |
| 芯片手册/原理图/数据手册/AD9248/AD7616/AD8132/ina818/SP3485/DM542/TJA1050/MCP2515 | `Hardware/` |
| 激光/清障仪/光路/FFRC/创鑫/杰普特/锐科/大族光子/波长光电 | `Laser/` |
| CAN/CANopen/USB转CAN/BLE/蓝牙/I2C/RS-485/通信协议 | `Protocol/` |
| OpenClaw运维/FFmpeg/Python/SQLite/pptx/QQBot/剪映/CUDA/Ubuntu/踩坑/代理 | `Toolchain/` |

匹配原则：上到下命中第一条即停；最具体领域优先；无法匹配→Toolchain/ 兜底。文件名格式 `YYYY-MM-DD_简短关键词.md`，重名加 `_v2`。

## memory.db 共享

BrainHub 与 BrainMem 共享 `d:\braindata\memory.db`（同路径打开，SQLite WAL 并发）：
- BrainMem 拥有写权（知识库/记忆/图谱）
- BrainHub ops agent 也写（记忆提取/归档日志）
- 同库可 JOIN，不重复存

BrainHub 追加的表（自己的，不碰 BrainMem 的表）：
```sql
-- 网盘文件元数据
CREATE TABLE files (path TEXT PRIMARY KEY, size INT, mtime TEXT, sha256 TEXT,
                    sync_state TEXT, thumb_path TEXT);
-- 项目看板
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, status TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT, status TEXT,
                    assignee TEXT, ord INT, created_at TEXT, updated_at TEXT);
-- 运维日志
CREATE TABLE ops_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, task TEXT,
                      action TEXT, result TEXT, detail TEXT);
```

## 技术要点

- 运维 agent：**Phase 2 已降级为接口壳**（OpsAgent 类骨架，`async def run(task)` 占位抛 NotImplementedError，ReAct 主体留 Phase 3）。Phase 2 的 LLM 路径是 extract-memories 直调 `AsyncAnthropic`（单次 `messages.create`，无循环）。Phase 3 落地时再加：ReAct 循环（thought→action→observation），最多 10 步，失败 2 次换方法，动作写 ops_log 可回溯。注：仓库无 AGENTS.md 文件，红线（max_steps=10/fail_switch=2）作硬编码规格写进 config，不读外部文件。
- 单进程并发：ops LLM 走 `anthropic.AsyncAnthropic`（httpx 异步）不阻塞；sync 的 brainmem 调用（`Searcher.search`/`query_memory` 可能加载 bge）用 `anyio.to_thread.run_sync` 丢线程池；web `/search`、`/memory` 路由同理包。重活交 BrainBridge Go（Phase 3）。
- WS 推送：agent 状态灯 + 运维日志实时输出，单 `/ws` 连接按 topic（ops_log/agent_status）分发，`WSBroker` 内存广播，无订阅者 publish no-op。
- 缩略图：Pillow 处理图片，pdf2image 处理 PDF 首页（需 poppler 在 PATH，缺失降级 broken-image），**懒生成**按预览请求、按 sha256 缓存到 `d:\braindata\cache\thumbs\`。
- 模板：Starlette 1.x 的 `Jinja2Templates.TemplateResponse` 签名要求 `TemplateResponse(request, name, context)`（不是旧的 `TemplateResponse(name, context)`），所有路由按新签名调。
- Store 共享：单例 Store（镜像 brainmem.mcp 懒加载）+ 独立 hub_conn 连同 memory.db（纯表无 vec/FTS，WAL 双连接，永不把 vec0 写和纯表写放同一 `BEGIN`）。

## 管道客户端（BrainHub ↔ BrainBridge，2026-07-14 加）

集成联调前先把 BrainHub 端命名管道就绪。对齐 BrainBridge `matrix/pipe.go` 顶部注释 + 协议宪法 §3.2。

**管道 + 方向**：

| 管道名 | 方向 | BrainHub 端 | 内容 |
|---|---|---|---|
| `\\.\pipe\brain-matrix-out` | BrainHub→Bridge | client（`pipe/writer.py`，CreateFile 连入写） | envelope 每行 JSON + `\n` |
| `\\.\pipe\brain-matrix-in` | Bridge→BrainHub | listener（`pipe/listener.py`，CreateNamedPipe 等连入） | Matrix 收到的消息（envelope） |
| `\\.\pipe\brain-agent-status` | Bridge→BrainHub | listener（同上） | `{agents:[...], ts}` 帧 |

方向策略：BrainHub 当 listener（in + agent_status 两条，CreateNamedPipe），BrainBridge 当 client 连入写；matrix-out 反向（BrainHub client 连 Bridge listener）。这样 Python 端用 pywin32 当 server，避免当 client 的麻烦。

**envelope**（协议宪法 §3，对齐 BrainBridge `matrix/envelope.go`）：`{type, from, to, task_id, spec_ref, text, ts}`，type ∈ `task_assign`/`task_result`/`heartbeat`/`notify`，`task_id`/`spec_ref` 空 `omitempty`。帧：每行一个 UTF-8 JSON，`\n` 分隔，fire-and-forget 无 ack，写失败仅 log。

**实现要点**：
- `pipe/protocol.py` — Envelope dataclass + `build_envelope()` + 管道名常量 + `pipe_path()`（Windows `\\.\pipe\<name>` / Linux `/tmp/<name>.sock`）。
- `pipe/writer.py` — pywin32 `CreateFile` 连 `brain-matrix-out`，`WriteFile`+`FlushFileBuffers`；连不上重试 5 次（`WaitNamedPipe` 等对端 listener 过渡期，ERROR_FILE_NOT_FOUND=2 / ERROR_PIPE_BUSY=231）；缺 pywin32 降级 `open(path,'r+b')`。进程级单例 + `send_matrix()` 便捷函数。fire-and-forget：对端没起返回 False 不抛。
- `pipe/listener.py` — `_PipeListener` 后台线程：`CreateNamedPipe`（PIPE_TYPE_BYTE|READMODE_BYTE|WAIT，单实例）→ `ConnectNamedPipe` 阻塞等连入 → `_read_loop` 按行读（`ReadFile` 返回 `(hr, data)` 不是 `(data, size)`！坑）→ `_handle_line` 解析 JSON。读到帧经 `run_coroutine_threadsafe` 调度进主事件循环推 WSBroker（topic=`matrix_in`/`agent_status`）。非 Windows / 缺 pywin32 退化到不启，不阻塞 web。
- `mcp.py` — 第 12 工具 `send_matrix_msg(to, text, type="notify", task_id?, spec_ref?, from?)` 调 `send_matrix()`。
- `web/app.py` — lifespan 起 `start_listeners()`（两个 listener），关闭时 `stop_listeners()`。
- `pyproject.toml` — 加 `pywin32>=305 ; sys_platform == 'win32'`。

**踩的坑（已修）**：
1. `pipe_path` Windows 反斜杠：`r"\\.\pipe\\"` raw string 实际是 `\\.\pipe\\`（pipe 后两个反斜杠，错），改普通字符串 `"\\\\.\\pipe\\"` 拼出标准 `\\.\pipe\`。
2. pywin32 `ReadFile` 返回 `(hr, data)` 不是 `(data, size)`——原写法把 error code 当 chunk，listener 读到"0 字节"。修成 `hr, data = ReadFile(...)`。

**待联调**：BrainBridge `matrix/pipe.go` Windows 端 go-winio listener 未接（返回 errWindowsPipeNotImpl）。BrainHub 端就绪，Bridge 补 go-winio listener（matrix-out）+ client（in/agent-status 连入）即可对上。验证标志：`send_matrix_msg` 的 `ok` 变 True；前端 `/ws` 订阅 `matrix_in`/`agent_status` 收到 Bridge 推来的帧。

## 数据路径

```
d:\braindata\              # 三款共享数据根
├── memory.db              # 与 BrainMem 共享（BrainHub 追加 files/projects/tasks/ops_log 表）
├── config.toml            # BrainHub 配置
├── logs\
└── cache\thumbs\          # 缩略图缓存
```

环境变量：`BRAIN_ROOT=D:\Brain`，`BRAIN_DATA=D:\braindata`（与 BrainMem 同一套）。

## 依赖

```
fastapi, uvicorn[standard], jinja2, python-multipart, anthropic,
watchdog, Pillow, pdf2image, httpx, apscheduler
+ brainmem (editable install from ../brainmem)
```
