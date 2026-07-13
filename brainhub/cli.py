# -*- coding: utf-8 -*-
"""BrainHub CLI — `brainhub start/stop/status` + `brainhub ops <task>`。

复用 brainmem.cli 的 typer 模式（app = typer.Typer + @app.command）。
- start/stop/status：管 uvicorn 进程（PID 文件在 BRAIN_DATA/logs/brainhub.pid）。
- ops 子命令组（里程碑 B/C 补全）：archive / extract-memories / reindex / health。

环境变量：BRAIN_ROOT、BRAIN_DATA（与 brainmem 同一套）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import typer

from brainhub.config import load_hub_config, ensure_config, brain_data, logs_dir, DEFAULT_PORT

app = typer.Typer(
    name="brainhub",
    help="BrainHub — 统一 Web 前端 + 网盘 + MCP 网关 + 运维 agent",
    no_args_is_help=True,
)

# ops 子命令组（里程碑 B/C 补全命令体）
ops_app = typer.Typer(name="ops", help="运维操作（归档/索引/记忆提取/健康检查）")
app.add_typer(ops_app)

logger = logging.getLogger(__name__)

_PID_FILE = "brainhub.pid"


def _pid_path() -> Path:
    return logs_dir() / _PID_FILE


# ---------------------------------------------------------------------------
# start / stop / status
# ---------------------------------------------------------------------------

@app.command()
def start(
    port: int = typer.Option(None, "--port", "-p", help="端口（默认读 config.toml 或 7788）"),
    host: str = typer.Option(None, "--host", help="绑定地址（默认 127.0.0.1）"),
    no_cron: bool = typer.Option(False, "--no-cron", help="不启动 APScheduler（调试用）"),
    reload: bool = typer.Option(False, "--reload", help="热重载（开发用）"),
):
    """启动 BrainHub Web 服务（uvicorn，单进程，workers=1）。"""
    cfg = load_hub_config()
    bh = cfg.get("brainhub", {})
    bind_port = port or bh.get("port", DEFAULT_PORT)
    bind_host = host or bh.get("host", "127.0.0.1")

    # 已在跑？
    if _pid_path().exists():
        old = _read_pid()
        if old and _is_alive(old):
            typer.echo(f"BrainHub 已在运行（PID {old}）。如需重启先 `brainhub stop`。")
            raise typer.Exit(code=1)

    import uvicorn

    # 环境变量透传给 uvicorn 进程（cron 起 / 不起由 lifespan 读）
    if no_cron:
        os.environ["BRAINHUB_NO_CRON"] = "1"

    typer.echo(f"BrainHub 启动：http://{bind_host}:{bind_port}")
    # 先写 PID 文件（本进程 PID；uvicorn.run 会阻塞，stop/status 读这个 PID）。
    _pid_path().write_text(str(os.getpid()), encoding="utf-8")
    # workers=1：APScheduler 去重前提（多 worker 各跑一份 cron 会重复归档/索引）。
    try:
        uvicorn.run(
            "brainhub.web.app:app",
            host=bind_host,
            port=bind_port,
            workers=1,
            reload=reload,
            log_level="info",
        )
    finally:
        # uvicorn.run 退出后清理 PID
        _pid_path().unlink(missing_ok=True)


@app.command()
def stop():
    """停止 BrainHub（读 PID 文件，terminate）。"""
    pid = _read_pid()
    if not pid:
        typer.echo("BrainHub 未运行（无 PID 文件）。")
        raise typer.Exit(code=1)
    if not _is_alive(pid):
        typer.echo(f"PID {pid} 已不在，清理 PID 文件。")
        _pid_path().unlink(missing_ok=True)
        raise typer.Exit()
    try:
        import subprocess
        if sys.platform == "win32":
            # Windows：taskkill 杀进程树（比 CTRL_BREAK 可靠，无需进程组）
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        typer.echo(f"已发送停止信号给 PID {pid}。")
    except Exception as e:
        typer.echo(f"停止失败：{e}", err=True)
        raise typer.Exit(code=1)
    _pid_path().unlink(missing_ok=True)


@app.command()
def status():
    """查看 BrainHub 运行状态 + 索引统计。"""
    pid = _read_pid()
    running = pid and _is_alive(pid)
    typer.echo(f"进程：{'运行中 (PID ' + str(pid) + ')' if running else '未运行'}")

    # 轻量健康探针
    cfg = load_hub_config()
    port = cfg.get("brainhub", {}).get("port", DEFAULT_PORT)
    if running:
        try:
            import httpx
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2.0)
            typer.echo(f"HTTP /api/health: {r.status_code} {r.text}")
        except Exception as e:
            typer.echo(f"HTTP 探针失败：{e}")

    # 索引统计（调 brainmem Store，懒加载）
    try:
        from brainhub.storage.db import get_store
        s = get_store().get_stats()
        typer.echo(f"索引：chunks={s['total_chunks']} docs={s['total_docs']} backend={s['backend']}")
        typer.echo(f"DB: {s['db_path']}")
    except Exception as e:
        typer.echo(f"Store 统计失败（可能首次未加载模型）：{e}")


# ---------------------------------------------------------------------------
# ops 子命令（里程碑 B/C 补全命令体，先留壳）
# ---------------------------------------------------------------------------

@ops_app.command("archive")
def ops_archive():
    """扫 2-Inbox 按归档规则分类移动到 3-Knowledge/{分类}/（里程碑 B 补全）。"""
    ensure_config()
    from brainhub.storage.archive import archive_inbox
    result = archive_inbox()
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@ops_app.command("extract-memories")
def ops_extract_memories(
    date: str = typer.Option(..., "--date", "-d", help="日期 YYYY-MM-DD"),
    log_path: str = typer.Option(None, "--log-path", help="覆盖 OpenClaw 日志目录"),
):
    """从 OpenClaw 当天对话日志提取记忆写 memory.db。"""
    if log_path:
        os.environ["OPENCLAW_LOG_DIR"] = log_path
    from brainhub.ops.extract import run_extract
    result = run_extract(date, log_path=log_path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@ops_app.command("reindex")
def ops_reindex(
    full: bool = typer.Option(False, "--full", help="强制全量重写"),
):
    """增量/全量重建索引（转发 brainmem Indexer，里程碑 C 补全）。"""
    from brainhub.storage.db import get_indexer
    from brainhub.config import brain_root
    ix = get_indexer()
    root = brain_root()
    stats = ix.index_roots([root / "3-Knowledge", root / "4-Personal"], full=full)
    typer.echo(
        f"reindex: indexed={stats.indexed} skipped={stats.skipped_unchanged} "
        f"deleted={stats.deleted} ({stats.duration_ms:.0f}ms)"
    )


@ops_app.command("health")
def ops_health():
    """健康检查：memory.db + 索引统计 + 磁盘空间。"""
    from brainhub.mcp import health_check
    result = health_check()
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
# PID 文件助手
# ---------------------------------------------------------------------------

def _read_pid() -> int | None:
    p = _pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def _is_alive(pid: int) -> bool:
    """进程是否在跑（跨平台）。"""
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            kernel32.CloseHandle(h)
            return True
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def main():
    app()


if __name__ == "__main__":
    main()
