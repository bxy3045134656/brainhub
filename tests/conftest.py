# -*- coding: utf-8 -*-
"""测试夹具 — 用临时 BRAIN_DATA/BRAIN_ROOT，不碰真实 d:\\braindata / d:\\Brain。

单测纯逻辑（archive 分类、projects 状态机、db schema 幂等），不加载 bge 模型。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """每个测试用独立临时目录，隔离 BRAIN_ROOT / BRAIN_DATA env。"""
    brain_root = tmp_path / "Brain"
    brain_data = tmp_path / "braindata"
    # 复刻 PARA 结构
    (brain_root / "2-Inbox").mkdir(parents=True)
    (brain_root / "3-Knowledge" / "Toolchain").mkdir(parents=True)
    (brain_root / "3-Knowledge" / "FPGA").mkdir(parents=True)
    (brain_root / "3-Knowledge" / "Embedded").mkdir(parents=True)
    (brain_root / "3-Knowledge" / "Agent").mkdir(parents=True)
    brain_data.mkdir(parents=True)

    monkeypatch.setenv("BRAIN_ROOT", str(brain_root))
    monkeypatch.setenv("BRAIN_DATA", str(brain_data))
    # 不跑 cron / 不连 LLM
    monkeypatch.setenv("BRAINHUB_NO_CRON", "1")

    # 重置 brainhub.storage.db 单例（避免跨测试复用旧 conn）
    import brainhub.storage.db as dbmod
    dbmod._hub_store = None
    dbmod._hub_searcher = None
    dbhub_memorize = getattr(dbmod, "_hub_memorize", None)
    dbmod._hub_memorize = None
    dbmod._hub_indexer = None
    dbmod._hub_conn = None

    yield tmp_path

    # 清理
    dbmod._hub_conn = None
    dbmod._hub_store = None
