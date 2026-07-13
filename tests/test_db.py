# -*- coding: utf-8 -*-
"""db schema 单测 — hub_conn 幂等建表 + 不破坏 brainmem 表 + ops_log 读写。"""

from brainhub.storage.db import get_hub_conn, log_ops, recent_ops_log


def test_hub_conn_creates_tables():
    conn = get_hub_conn()
    # 4 张表都应在
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in ("files", "projects", "tasks", "ops_log"):
        assert t in tables, f"缺表 {t}"


def test_hub_conn_idempotent():
    """反复 get_hub_conn 不重复建表、不报错。"""
    conn1 = get_hub_conn()
    conn2 = get_hub_conn()  # 单例，同一对象
    assert conn1 is conn2


def test_ops_log_write_read():
    log_ops("test-task", "test-action", result="ok", detail="单测")
    logs = recent_ops_log(limit=10)
    assert any(l["task"] == "test-task" for l in logs)


def test_no_brainmem_tables_clobbered():
    """建 BrainHub 表不应碰 brainmem 的表（knowledge_chunks 等可能在也可能不在，
    但建 Hub 表动作本身不应报错或删既有表）。"""
    conn = get_hub_conn()
    # 主动建一遍 Hub schema（executescript IF NOT EXISTS，幂等）
    from brainhub.config import SCHEMA_HUB
    conn.executescript(SCHEMA_HUB)
    # files 表仍在
    n = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    assert n == 0
