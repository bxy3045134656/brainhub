# -*- coding: utf-8 -*-
"""BrainHub 配置 + 路径助手。

复用 brainmem 的环境变量约定（协议宪法数据路径）：
- BRAIN_ROOT  知识库根（d:\\Brain 或 HX470:/data/Brain）
- BRAIN_DATA  三款共享数据根（d:\\braindata 或 HX470:/var/braindata）
  ├── memory.db        与 brainmem 共享（BrainHub 追加 files/projects/tasks/ops_log 表）
  ├── config.toml      BrainHub 配置（brainmem 的 load_config 也读这个，各读各的段）
  ├── logs\\
  └── cache\\thumbs\\   缩略图缓存

config.toml 可选：无 tomli（Py3.10 未装）/ 文件不存在时返回默认 config（默认值已能跑）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BRAIN_ROOT = r"d:\Brain"
DEFAULT_BRAIN_DATA = r"d:\braindata"
DEFAULT_PORT = 7788

# OpenClaw 对话日志根（extract-memories 数据源）。PLAN 未指明，探查确认在此。
DEFAULT_OPENCLAW_DATA = r"d:\openclaw\data\.openclaw"

# BrainHub 追加表 schema（与 brainmem 的 memory.db 同库，纯表无 vec/FTS，幂等建）。

# 读写路径拒绝清单（统一入口：mcp.py / api.py / files.py 共用，防漂移）。
# 相对 BRAIN_ROOT 的路径（正斜杠）命中任一子串即拒绝。
READ_BLOCKED_PATTERNS = [".trash/", "secrets.json", ".git/", ".venv/", ".uv/", "__pycache__/"]


def is_blocked_path(rel_str: str) -> bool:
    """相对路径是否被拒（统一实现，各处引用避免重复定义漂移）。"""
    rel_str = rel_str.replace("\\", "/")
    return any(p in rel_str for p in READ_BLOCKED_PATTERNS)


SCHEMA_HUB = """
-- 网盘文件元数据
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INT,
    mtime TEXT,
    sha256 TEXT,
    sync_state TEXT,
    thumb_path TEXT
);

-- 项目看板
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT,
    status TEXT,
    assignee TEXT,
    ord INT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- 运维日志
CREATE TABLE IF NOT EXISTS ops_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    task TEXT,
    action TEXT,
    result TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_ops_log_ts ON ops_log(ts);
CREATE INDEX IF NOT EXISTS idx_ops_log_task ON ops_log(task);
"""


# ---------------------------------------------------------------------------
# 路径助手（读 env，与 brainmem store._brain_data_root 同源）
# ---------------------------------------------------------------------------

def brain_root() -> Path:
    """知识库根（BRAIN_ROOT env，默认 d:\\Brain）。"""
    return Path(os.environ.get("BRAIN_ROOT", DEFAULT_BRAIN_ROOT))


def brain_data() -> Path:
    """共享数据根（BRAIN_DATA env，默认 d:\\braindata）。"""
    return Path(os.environ.get("BRAIN_DATA", DEFAULT_BRAIN_DATA))


def db_path() -> Path:
    """memory.db 路径（与 brainmem Store 默认一致，同库共享）。"""
    return brain_data() / "memory.db"


def config_path() -> Path:
    """config.toml 路径（brainmem load_config 也读这个）。"""
    return brain_data() / "config.toml"


def logs_dir() -> Path:
    p = brain_data() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def thumbs_dir() -> Path:
    """缩略图缓存目录。"""
    p = brain_data() / "cache" / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def openclaw_data() -> Path:
    """OpenClaw data 根（extract-memories 日志源，OPENCLAW_DATA env 可覆盖）。"""
    return Path(os.environ.get("OPENCLAW_DATA", DEFAULT_OPENCLAW_DATA))


def openclaw_log_dir() -> Path:
    """OpenClaw 对话 trajectory 日志根（agents/{main,ace,sentinel}/sessions/）。

    OPENCLAW_LOG_DIR env 优先（用户可指向别处），否则用 openclaw_data()/agents。
    """
    env_dir = os.environ.get("OPENCLAW_LOG_DIR")
    if env_dir:
        return Path(env_dir)
    return openclaw_data() / "agents"


def static_dir() -> Path:
    """Web 静态资源目录。"""
    return Path(__file__).resolve().parent / "web" / "static"


def templates_dir() -> Path:
    """Jinja2 模板目录。"""
    return Path(__file__).resolve().parent / "web" / "templates"


# ---------------------------------------------------------------------------
# config.toml 加载（与 brainmem searcher.load_config 同样的 tomllib/tomli 回退）
# ---------------------------------------------------------------------------

_TOML_AVAILABLE = False
try:
    import tomllib  # Py3.11+
    _TOML_AVAILABLE = True
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore
        _TOML_AVAILABLE = True
    except ModuleNotFoundError:
        _TOML_AVAILABLE = False


def _default_config() -> dict[str, Any]:
    """BrainHub 默认配置（不依赖 config.toml 文件存在）。"""
    return {
        "brainhub": {
            "port": DEFAULT_PORT,
            "host": "127.0.0.1",
        },
        "ops": {
            "model": os.environ.get("ANTHROPIC_MODEL", "xopglm52"),
            "max_steps": 10,           # ReAct 步数上限（Phase 3 落地）
            "fail_switch_threshold": 2,  # 连续失败 N 次换方法（Phase 3）
        },
        "cron": {
            "archive": "30 2 * * *",        # 每日 02:30
            "reindex": "0 3 * * *",         # 每日 03:00
            "extract_memories": "0 23 * * *",  # 每日 23:00
            "health_check": "*/5 * * * *",   # 每 5min
            "index_update": "30 3 * * *",   # 每日 03:30
        },
        "archive": {
            "fallback_dir": "Toolchain",
        },
    }


def load_hub_config(config_path_str: str | Path | None = None) -> dict[str, Any]:
    """加载 config.toml 的 BrainHub 段（与 brainmem 各读各的段，互不干扰）。

    config.toml 可选：无 toml 解析器 / 文件不存在时返回默认 config。
    """
    cfg = _default_config()
    p = Path(config_path_str) if config_path_str else config_path()
    if not _TOML_AVAILABLE or not p.exists():
        return cfg
    with open(p, "rb") as f:
        loaded = tomllib.load(f)
    # 合并：用户段覆盖默认段（深一层）
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def ensure_config() -> None:
    """幂等写 config.toml 默认 [brainhub]/[ops]/[cron] 段（不覆盖已有键）。

    首次启动调用，保证用户有一个可编辑的配置文件。无 toml 解析器时跳过（默认值已能跑）。
    """
    p = config_path()
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    # 写一个最小默认 config（纯文本，不依赖 toml 库）
    default_toml = """# BrainHub + BrainMem 共享配置（BRAIN_DATA/config.toml）
# brainmem 读 [retrieval]/[layers] 段，BrainHub 读 [brainhub]/[ops]/[cron]/[archive] 段。

[brainhub]
port = 7788
host = "127.0.0.1"

[ops]
model = "xopglm52"
max_steps = 10
fail_switch_threshold = 2

[cron]
archive = "30 2 * * *"
reindex = "0 3 * * *"
extract_memories = "0 23 * * *"
health_check = "*/5 * * * *"
index_update = "30 3 * * *"

[archive]
fallback_dir = "Toolchain"
"""
    try:
        p.write_text(default_toml, encoding="utf-8")
        logger.info(f"写入默认 config: {p}")
    except Exception as e:
        logger.warning(f"写 config 失败（不影响运行）: {e}")
