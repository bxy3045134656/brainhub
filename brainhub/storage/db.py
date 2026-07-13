# -*- coding: utf-8 -*-
"""BrainHub 存储层 — 懒加载单例 Store + 独立 hub_conn。

复用 brainmem 的懒加载单例模式（brainmem/mcp.py 的 _get_store/_get_searcher/
_get_memorize/_get_indexer），web + ops + cron 全在同一个 uvicorn 进程里共享一个 Store
（一个 embedder ~1GB，不开第二个）。

BrainHub 自有表（files/projects/tasks/ops_log）用**另一个 sqlite3.Connection 连同一个
memory.db**：纯表无 vec0/FTS5，免疫 brainmem 踩过的「vec0 + FTS5 external-content + WAL
同事务 corrupt」坑；autocommit + WAL 与 brainmem 的 Store 并发安全。

铁律：永远不在一个显式 BEGIN 里同时写 brainmem 的 vec0/FTS5 表和 BrainHub 的纯表。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from brainmem import Store, Searcher, Memorize, Indexer
from brainmem.searcher import load_config as load_mem_config

from brainhub.config import db_path, brain_data, SCHEMA_HUB

logger = logging.getLogger(__name__)

# ─── brainmem 侧懒加载单例（镜像 brainmem/mcp.py）───
_hub_store: Store | None = None
_hub_searcher: Searcher | None = None
_hub_memorize: Memorize | None = None
_hub_indexer: Indexer | None = None
_mem_config: dict[str, Any] | None = None


def get_store() -> Store:
    """共享 brainmem Store（同 memory.db，WAL + autocommit）。

    与 brainmem Store 默认构造一致：db_path=BRAIN_DATA/memory.db，brain_data=BRAIN_DATA。
    Store.__init__ 自动建 Phase1 + Phase2 schema（IF NOT EXISTS，幂等）。
    """
    global _hub_store, _mem_config
    if _hub_store is None:
        _mem_config = load_mem_config()
        _hub_store = Store(db_path=db_path(), brain_data=brain_data())
        logger.info(f"BrainHub Store opened: {db_path()}")
    return _hub_store


def get_searcher() -> Searcher:
    """共享 Searcher（search_knowledge / query_memory 用）。"""
    global _hub_searcher
    if _hub_searcher is None:
        _hub_searcher = Searcher(get_store(), _mem_config)
    return _hub_searcher


def get_memorize() -> Memorize:
    """共享 Memorize（write_memory / forget / compress_working_to_episodic 用）。"""
    global _hub_memorize
    if _hub_memorize is None:
        _hub_memorize = Memorize(get_store())
    return _hub_memorize


def get_indexer() -> Indexer:
    """共享 Indexer（reindex 用）。"""
    global _hub_indexer
    if _hub_indexer is None:
        _hub_indexer = Indexer(get_store())
    return _hub_indexer


# ─── BrainHub 自有表连接（独立 conn，同 memory.db）───
_hub_conn: sqlite3.Connection | None = None


def get_hub_conn() -> sqlite3.Connection:
    """BrainHub 自有表连接（files/projects/tasks/ops_log）。

    与 brainmem Store 同一个 memory.db，但独立连接：
    - isolation_level=None（autocommit，与 Store 一致，避免 vec0+FTS5 同事务问题）
    - journal_mode=WAL + synchronous=NORMAL（与 Store 一致，并发安全）
    - check_same_thread=False（web anyio 线程池可跨线程用）
    - 幂等建 SCHEMA_HUB（IF NOT EXISTS，不动 brainmem 的表）
    """
    global _hub_conn
    if _hub_conn is None:
        conn = sqlite3.connect(
            str(db_path()),
            check_same_thread=False,
            isolation_level=None,  # autocommit，与 brainmem Store 一致
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_HUB)
        _hub_conn = conn
        logger.info(f"BrainHub hub_conn opened: {db_path()}")
    return _hub_conn


def close_all() -> None:
    """关闭所有连接（进程退出时调）。Store.close() 关 brainmem 的 conn。"""
    global _hub_store, _hub_searcher, _hub_memorize, _hub_indexer, _hub_conn
    if _hub_store is not None:
        try:
            _hub_store.close()
        except Exception:
            pass
    if _hub_conn is not None:
        try:
            _hub_conn.close()
        except Exception:
            pass
    _hub_store = None
    _hub_searcher = None
    _hub_memorize = None
    _hub_indexer = None
    _hub_conn = None


# ---------------------------------------------------------------------------
# ops_log 写入助手（cron / ops agent / archive 共用，写 BrainHub 自有表）
# ---------------------------------------------------------------------------

def log_ops(task: str, action: str, result: str = "ok", detail: str = "") -> None:
    """写一条 ops_log（autocommit，独立于 brainmem 的 vec0/FTS5 事务）。

    result 约定：ok / fail / warn。detail 放失败原因或步骤摘要。
    """
    from brainmem.time_sense import now_shanghai
    conn = get_hub_conn()
    conn.execute(
        "INSERT INTO ops_log(ts, task, action, result, detail) VALUES (?,?,?,?,?)",
        (now_shanghai().isoformat(), task, action, result, detail),
    )


def recent_ops_log(limit: int = 100) -> list[dict[str, Any]]:
    """读最近 N 条 ops_log（运维日志面板用）。"""
    conn = get_hub_conn()
    rows = conn.execute(
        "SELECT id, ts, task, action, result, detail FROM ops_log "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
