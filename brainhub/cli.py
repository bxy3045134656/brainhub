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
        old_main = old.get("main") if old else None
        if old_main and _is_alive(old_main):
            typer.echo(f"BrainHub 已在运行（PID {old_main}）。如需重启先 `brainhub stop`。")
            raise typer.Exit(code=1)

    import uvicorn

    # 环境变量透传给 uvicorn 进程（cron 起 / 不起由 lifespan 读）
    if no_cron:
        os.environ["BRAINHUB_NO_CRON"] = "1"

    typer.echo(f"BrainHub 启动：http://{bind_host}:{bind_port}")
    # 先拉 brain-bridge serve 子进程（失败只警告，不挡 web 起）。
    child_pid = _spawn_bridge()
    # 写 PID 文件（主 + 子，JSON）。uvicorn.run 会阻塞，stop/status 读这个。
    _write_pid(os.getpid(), child_pid)
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
        # uvicorn.run 退出（正常停 / Ctrl+C / 崩溃）后：
        # 1) 连子进程 brain-bridge 一起杀（防 Ctrl+C 退留孤儿 → 下次 start 撞端口）
        # 2) 清 PID 文件
        if child_pid and _is_alive(child_pid):
            _kill_pid(child_pid)
            typer.echo(f"已停止 brain-bridge 子进程（PID {child_pid}）。")
        _pid_path().unlink(missing_ok=True)


@app.command()
def stop():
    """停止 BrainHub + brain-bridge 子进程（读 PID 文件，terminate）。"""
    pids = _read_pid()
    if not pids:
        typer.echo("BrainHub 未运行（无 PID 文件）。")
        raise typer.Exit(code=1)

    main_pid = pids.get("main")
    child_pid = pids.get("child")

    # 主进程
    if main_pid and _is_alive(main_pid):
        _kill_pid(main_pid)
        typer.echo(f"已发送停止信号给 BrainHub PID {main_pid}。")
    elif main_pid:
        typer.echo(f"主 PID {main_pid} 已不在。")

    # brain-bridge 子进程（连子 PID 一起杀）
    if child_pid and _is_alive(child_pid):
        _kill_pid(child_pid)
        typer.echo(f"已发送停止信号给 brain-bridge PID {child_pid}。")
    elif child_pid:
        typer.echo(f"子 PID {child_pid}（brain-bridge）已不在。")

    _pid_path().unlink(missing_ok=True)


@app.command()
def status():
    """查看 BrainHub 运行状态 + 索引统计。"""
    pids = _read_pid()
    main_pid = pids.get("main") if pids else None
    child_pid = pids.get("child") if pids else None
    running = main_pid and _is_alive(main_pid)
    typer.echo(f"BrainHub：{'运行中 (PID ' + str(main_pid) + ')' if running else '未运行'}")
    if child_pid:
        child_alive = _is_alive(child_pid)
        typer.echo(f"brain-bridge：{'运行中 (PID ' + str(child_pid) + ')' if child_alive else f'已退 (PID {child_pid})'}")

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
# PID 文件助手（JSON，记 main + child 两个 PID）
# ---------------------------------------------------------------------------

def _write_pid(main: int, child: int | None) -> None:
    _pid_path().write_text(
        json.dumps({"main": main, "child": child}, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_pid() -> dict | None:
    """读 PID 文件，返回 {"main":int, "child":int|None}。

    兼容旧格式（纯文本单 PID）：纯数字则当 main，child=None。
    """
    p = _pid_path()
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    # 旧格式：纯文本单 PID
    try:
        return {"main": int(raw), "child": None}
    except ValueError:
        pass
    # 新格式：JSON {"main","child"}
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and "main" in d:
            return {"main": int(d["main"]),
                    "child": int(d["child"]) if d.get("child") else None}
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
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


def _kill_pid(pid: int) -> None:
    """杀进程（Windows taskkill /T /F 杀整树，POSIX SIGTERM）。"""
    import subprocess
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


# ---------------------------------------------------------------------------
# brain-bridge 子进程拉起
# ---------------------------------------------------------------------------

def _find_bridge_exe() -> str | None:
    """找 brain-bridge 可执行文件路径。

    优先 PATH（brain-bridge / brain-bridge.exe），再查 d:\\braincode\\brainbridge\\bin\\。
    找不到返回 None。
    """
    import shutil
    # 1. PATH
    in_path = shutil.which("brain-bridge") or shutil.which("brain-bridge.exe")
    if in_path:
        return in_path
    # 2. 构建产物目录（d:\braincode\brainbridge\bin\）
    exe_name = "brain-bridge.exe" if sys.platform == "win32" else "brain-bridge"
    for cand in (Path(r"d:\braincode\brainbridge\bin") / exe_name,
                 Path(r"D:\braincode\brainbridge\bin") / exe_name):
        if cand.is_file():
            return str(cand)
    return None


def _spawn_bridge() -> int | None:
    """拉起 brain-bridge serve 子进程，返回子 PID（失败返回 None，不抛）。

    brain-bridge 起不来只 log 警告，不影响 BrainHub web 起来
    （管道客户端的 listener/writer 自带退化，bridge 不在也能降级）。
    """
    exe = _find_bridge_exe()
    if not exe:
        logger.warning("brain-bridge 未找到（PATH 无 + d:\\braincode\\brainbridge\\bin\\ 无构建产物），跳过子进程拉起。管道待联调。")
        typer.echo("[warn] brain-bridge 未找到，跳过子进程拉起（web 照常起）。")
        return None
    try:
        import subprocess
        # 重定向到 BRAIN_DATA/logs/brain-bridge.{out,err}.log（append），
        # bridge 静默挂时有线索可查（跟 brainhub.pid 同目录）。
        log_dir = logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        out_log = log_dir / "brain-bridge.out.log"
        err_log = log_dir / "brain-bridge.err.log"
        out_fp = open(out_log, "ab", buffering=0)
        err_fp = open(err_log, "ab", buffering=0)
        typer.echo(f"brain-bridge 日志：{out_log} / {err_log}")
        proc = subprocess.Popen(
            [exe, "serve", "--matrix-send-only"],
            stdout=out_fp,
            stderr=err_fp,
            # Windows：不挂控制台，独立进程组；父 Ctrl+C 退不拽死子，
            # 但 uvicorn.run finally 会显式杀子（见 start），防孤儿。
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                            if sys.platform == "win32" else 0),
        )
        typer.echo(f"brain-bridge serve 已拉起（PID {proc.pid}）。")
        logger.info(f"brain-bridge serve 拉起，子 PID={proc.pid}")
        return proc.pid
    except Exception as e:
        logger.warning(f"brain-bridge serve 拉起失败：{e}（web 照常起）")
        typer.echo(f"[warn] brain-bridge serve 拉起失败：{e}（web 照常起）。")
        return None


def main():
    app()


if __name__ == "__main__":
    main()
