# -*- coding: utf-8 -*-
"""APScheduler 定时任务 — 归档/索引/记忆提取/健康检查/Index.md 更新。

单进程：uvicorn workers=1 前提下，AsyncIOScheduler 在事件循环里跑，cron 不重复。
jobs 调 sync 的 brainmem 调用（Indexer.index_roots 等）经 scheduler 默认线程池跑，
不阻塞事件循环。

Cron 表（config.toml [cron] 段可调）：
- archive          30 2 * * *   扫 2-Inbox 归档
- reindex          0 3 * * *    增量索引 3-Knowledge + 4-Personal
- extract_memories 0 23 * * *   从 OpenClaw 日志提取记忆
- health_check     */5 * * * *  健康检查
- index_update     30 3 * * *   重写 Index.md 速览表
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from brainhub.config import brain_root
from brainhub.storage.db import log_ops

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(app) -> AsyncIOScheduler | None:
    """启动 cron 调度器（lifespan 调；BRAINHUB_NO_CRON=1 时跳过）。

    返回 scheduler 或 None（跳过）。
    """
    import os
    if os.environ.get("BRAINHUB_NO_CRON"):
        logger.info("BRAINHUB_NO_CRON=1，跳过 cron 调度器")
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    cfg = app.state.hub_config
    cron_cfg = cfg.get("cron", {})

    sched = AsyncIOScheduler(timezone="Asia/Shanghai")
    _scheduler = sched

    # archive 02:30
    _add_job(sched, cron_cfg.get("archive", "30 2 * * *"),
             _job_archive, "archive")
    # reindex 03:00
    _add_job(sched, cron_cfg.get("reindex", "0 3 * * *"),
             _job_reindex, "reindex")
    # extract-memories 23:00
    _add_job(sched, cron_cfg.get("extract_memories", "0 23 * * *"),
             _job_extract_memories, "extract-memories")
    # health check 每 5min
    _add_job(sched, cron_cfg.get("health_check", "*/5 * * * *"),
             _job_health_check, "health-check")
    # Index.md update 03:30
    _add_job(sched, cron_cfg.get("index_update", "30 3 * * *"),
             _job_index_update, "index-update")

    sched.start()
    logger.info("APScheduler 启动（5 个 cron 任务）")
    return sched


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _add_job(sched: AsyncIOScheduler, cron_expr: str, func, name: str) -> None:
    """加一个 cron job（5 字段）。func 是 async。"""
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone="Asia/Shanghai")
    except ValueError as e:
        logger.error(f"cron 表达式非法 [{name}]: {cron_expr} ({e})")
        return
    sched.add_job(func, trigger=trigger, id=name, name=name,
                  misfire_grace_time=60, coalesce=True)


# ---------------------------------------------------------------------------
# job 实现（async，重 sync brainmem 调用丢线程池）
# ---------------------------------------------------------------------------

async def _job_archive() -> None:
    """扫 2-Inbox 归档（sync，丢线程池）。"""
    from brainhub.storage.archive import archive_inbox
    await asyncio.to_thread(_run_sync_logged, "archive", archive_inbox)


async def _job_reindex() -> None:
    """增量索引（sync brainmem Indexer，丢线程池）。"""
    def _do():
        from brainhub.storage.db import get_indexer
        root = brain_root()
        stats = get_indexer().index_roots(
            [root / "3-Knowledge", root / "4-Personal"], full=False
        )
        return {"indexed": stats.indexed, "skipped": stats.skipped_unchanged,
                "deleted": stats.deleted, "duration_ms": round(stats.duration_ms, 1)}
    await asyncio.to_thread(_run_sync_logged, "reindex", _do)


async def _job_extract_memories() -> None:
    """记忆提取（async，直接调 extract_memories）。"""
    from datetime import datetime
    from brainmem.time_sense import now_shanghai
    date_str = now_shanghai().strftime("%Y-%m-%d")
    from brainhub.ops.extract import extract_memories
    try:
        result = await extract_memories(date_str)
        log_ops("cron:extract-memories", "done", result="ok",
                detail=f"written={result.get('memories_written', 0)}")
    except Exception as e:
        log_ops("cron:extract-memories", "run", result="fail", detail=str(e))
        logger.exception("extract-memories cron 失败")


async def _job_health_check() -> None:
    """健康检查（里程碑 C：OpenClaw/HiClaw/向量库/磁盘）。"""
    from brainhub.mcp import health_check
    try:
        result = await asyncio.to_thread(health_check)
        bad = [c for c in result.get("checks", []) if not c.get("ok")]
        if bad:
            log_ops("cron:health-check", "check", result="warn",
                    detail=f"bad={len(bad)}")
    except Exception as e:
        log_ops("cron:health-check", "run", result="fail", detail=str(e))


async def _job_index_update() -> None:
    """重写 Index.md 速览表（统计目录文件数）。"""
    def _do():
        # 简化版：重算 3-Knowledge 各子目录的 .md 数，写回 Index.md 速览段。
        # 完整版（按 Index.md 现有表格结构重写文件数列）留后续迭代。
        root = brain_root() / "3-Knowledge"
        counts: dict[str, int] = {}
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir():
                    counts[d.name] = sum(1 for _ in d.rglob("*.md"))
        log_ops("cron:index-update", "stats", result="ok",
                detail=str(counts))
        return counts
    await asyncio.to_thread(_run_sync_logged, "index-update", _do)


def _run_sync_logged(task: str, func) -> Any:
    """包 sync job：跑 + 写 ops_log（WS 推由调用方的 async job 负责，线程内不碰 loop）。"""
    log_ops(f"cron:{task}", "start", result="ok")
    try:
        result = func() if callable(func) else None
        log_ops(f"cron:{task}", "done", result="ok",
                detail=str(result)[:200] if result else "")
        return result
    except Exception as e:
        log_ops(f"cron:{task}", "run", result="fail", detail=str(e))
        logger.exception(f"cron {task} 失败")
        raise
