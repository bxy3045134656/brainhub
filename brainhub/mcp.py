# -*- coding: utf-8 -*-
"""BrainHub MCP 网关 — fastmcp，暴露 BrainHub 工具 + 转发 BrainMem 工具。

协议宪法 §1 的 11 个工具：
- BrainHub 自有：write_note / read_file / list_files / list_projects /
  query_project / update_project / health_check
- 转发 brainmem（BrainHub 网关代理，经自己的单例调底层 API）：
  search_knowledge / query_memory / write_memory / reindex

策略：不 import brainmem.mcp 的装饰函数 re-decorate；直调 brainmem Python API
（Searcher/Memorize/Indexer）经 brainhub.storage.db 的懒加载单例。输出 shape 镜像
brainmem.mcp 的格式（对外 JSON 一致）。

注册（协议宪法 §MCP 注册）：Claude Code → ~/.claude/settings.json mcpServers；
Codex → ~/.codex/config.toml；OpenClaw → SSE :7789。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from brainhub.config import brain_root, brain_data, db_path
from brainhub.storage.db import (
    get_store, get_searcher, get_memorize, get_indexer, get_hub_conn,
)
from brainhub.storage.archive import write_note as _write_note
from brainhub.storage.files import FileRepo, _safe_resolve, _sha256, _iso_mtime
from brainhub.projects.models import ProjectRepo, TaskRepo, VALID_STATUSES

logger = logging.getLogger(__name__)

mcp = FastMCP("brainhub")

# 复用 brainmem.mcp 的拒绝清单（read_file/list_files 安全）
_READ_BLOCKED_PATTERNS = [".trash/", "secrets.json", ".git/", ".venv/", ".uv/", "__pycache__/"]


def _is_blocked(rel_str: str) -> bool:
    rel_str = rel_str.replace("\\", "/")
    return any(p in rel_str for p in _READ_BLOCKED_PATTERNS)


# ---------------------------------------------------------------------------
# BrainHub 自有工具
# ---------------------------------------------------------------------------

@mcp.tool
def write_note(title: str, content: str, category: str | None = None) -> dict[str, Any]:
    """写一篇笔记到 3-Knowledge/{分类}/。

    Args:
        title: 笔记标题。
        content: 笔记内容（markdown）。
        category: 可选，指定分类目录；None 时按内容关键词自动分类。
    Returns:
        {path, archived_to}（协议宪法 write_note 输出契约）。
    """
    return _write_note(title=title, content=content, category=category)


@mcp.tool
def read_file(path: str, range: str | None = None) -> dict[str, Any]:
    """只读读取文件（协议宪法 §1，BrainHub 实现）。

    拒绝 .trash/、secrets.json、.git/、.venv/、.uv/ + BRAIN_ROOT 越界。
    Args:
        path: 相对 BRAIN_ROOT 或绝对。
        range: 可选行范围 "start-end"（1-based 闭区间）。
    Returns:
        {path, content, mtime, sha256, lines} 或 {error}。
    """
    root = brain_root()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        rel = p.resolve().relative_to(root.resolve())
    except ValueError:
        return {"error": f"路径越出 BRAIN_ROOT：{path}"}
    if _is_blocked(str(rel)):
        return {"error": f"路径被拒绝（.trash/secrets/越界）：{path}"}
    if not p.is_file():
        return {"error": f"文件不存在：{path}"}

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="gbk", errors="replace")
    except Exception as e:
        return {"error": f"读取失败：{e}"}

    lines = content.split("\n")
    if range:
        try:
            start, end = range.split("-")
            start, end = int(start), int(end)
            content = "\n".join(lines[start - 1:end])
        except (ValueError, IndexError):
            return {"error": f"range 格式错误，应为 start-end：{range}"}

    return {
        "path": str(p),
        "content": content,
        "mtime": _iso_mtime(p),
        "sha256": _sha256(p),
        "lines": len(lines),
    }


@mcp.tool
def list_files(dir: str = ".", pattern: str | None = None,
               recursive: bool = True) -> dict[str, Any]:
    """列出目录文件（限 BRAIN_ROOT 下）。

    Args:
        dir: 目录（相对 BRAIN_ROOT 或绝对），默认根。
        pattern: glob 过滤，如 "*.md"。
        recursive: 是否递归，默认 True。
    Returns:
        {files:[{path, size, mtime}], count} 或 {error}。
    """
    import fnmatch
    root = brain_root()
    d = Path(dir)
    if not d.is_absolute():
        d = root / d
    try:
        rel = d.resolve().relative_to(root.resolve())
    except ValueError:
        return {"error": f"目录越出 BRAIN_ROOT：{dir}"}
    if _is_blocked(str(rel)):
        return {"error": f"目录被拒绝：{dir}"}
    if not d.is_dir():
        return {"error": f"目录不存在：{dir}"}

    files = []
    it = d.rglob("*") if recursive else d.glob("*")
    for f in it:
        if not f.is_file():
            continue
        try:
            r = f.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if _is_blocked(str(r)):
            continue
        if pattern and not fnmatch.fnmatch(f.name, pattern):
            continue
        files.append({
            "path": str(r).replace("\\", "/"),
            "size": f.stat().st_size,
            "mtime": _iso_mtime(f),
        })
    return {"files": files, "count": len(files)}


# ---- 项目/任务（协议宪法 §1 list_projects/query_project/update_project）----

@mcp.tool
def list_projects() -> dict[str, Any]:
    """列出所有项目。"""
    conn = get_hub_conn()
    return {"projects": ProjectRepo(conn).list_all()}


@mcp.tool
def query_project(project_id: str) -> dict[str, Any]:
    """查单个项目 + 其任务。"""
    conn = get_hub_conn()
    p = ProjectRepo(conn).get(project_id)
    if not p:
        return {"error": f"项目不存在：{project_id}"}
    tasks = TaskRepo(conn).list_by_project(project_id)
    return {"project": p, "tasks": tasks}


@mcp.tool
def update_project(project_id: str, status: str | None = None,
                   name: str | None = None) -> dict[str, Any]:
    """更新项目状态/名字。"""
    conn = get_hub_conn()
    repo = ProjectRepo(conn)
    if status is not None:
        try:
            return repo.update_status(project_id, status)
        except ValueError as e:
            return {"error": str(e)}
    if name is not None:
        import sqlite3 as _s
        from brainmem.time_sense import now_shanghai
        conn.execute(
            "UPDATE projects SET name=?, updated_at=? WHERE id=?",
            (name, now_shanghai().isoformat(), project_id),
        )
        return repo.get(project_id) or {"error": "更新后未找到"}
    return {"error": "需提供 status 或 name"}


# ---- 健康检查 ----

@mcp.tool
def health_check() -> dict[str, Any]:
    """健康检查：memory.db 可达 + 索引统计 + 磁盘空间。

    OpenClaw/HiClaw 探针留 Phase 3（避免硬编码端口未确认的依赖）。
    """
    checks: list[dict[str, Any]] = []

    # memory.db 可达
    try:
        conn = get_hub_conn()
        n = conn.execute("SELECT count(*) FROM ops_log").fetchone()[0]
        checks.append({"name": "memory.db", "ok": True,
                       "detail": f"ops_log rows={n}"})
    except Exception as e:
        checks.append({"name": "memory.db", "ok": False, "detail": str(e)})

    # 索引统计（懒加载 Store，可能触发首次加载——这里只读 manifest 不碰 embedder）
    try:
        conn = get_store().conn
        docs = conn.execute("SELECT count(*) FROM index_manifest").fetchone()[0]
        chunks = conn.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
        checks.append({"name": "index", "ok": True,
                       "detail": f"docs={docs} chunks={chunks}"})
    except Exception as e:
        checks.append({"name": "index", "ok": False, "detail": str(e)})

    # 磁盘空间（braindata 所在盘）
    try:
        import shutil
        usage = shutil.disk_usage(str(brain_data()))
        free_gb = usage.free / (1 << 30)
        checks.append({"name": "disk", "ok": free_gb > 1,
                       "detail": f"free={free_gb:.1f}GB"})
    except Exception as e:
        checks.append({"name": "disk", "ok": False, "detail": str(e)})

    return {"checks": checks,
            "ok": all(c.get("ok") for c in checks)}


# ---------------------------------------------------------------------------
# 转发 brainmem 工具（输出 shape 镜像 brainmem.mcp）
# ---------------------------------------------------------------------------

@mcp.tool
def search_knowledge(query: str, k: int = 5, layer: str = "all",
                     time_range: str | None = None) -> dict[str, Any]:
    """搜索知识库（dense+sparse 混合检索 + 时间衰减）。转发 brainmem Searcher。"""
    sr = get_searcher()
    results = sr.search(query, k=k, time_range=time_range)
    out = []
    for r in results:
        out.append({
            "doc_path": r.get("doc_path", ""),
            "chunk_text": (r.get("chunk_text") or "")[:1000],
            "score": round(r.get("final_score", r.get("score", 0)), 4),
            "source": r.get("source", "hybrid"),
            "chunk_idx": r.get("chunk_idx", 0),
            "mtime": r.get("mtime", ""),
        })
    return {"results": out, "count": len(out)}


@mcp.tool
def query_memory(query: str, k: int = 5, layers: list[str] | None = None,
                 time_range: str | None = None) -> dict[str, Any]:
    """检索记忆（dense+sparse+graph 三路混合）。转发 brainmem Searcher。"""
    sr = get_searcher()
    results = sr.query_memory(query, k=k, layers=layers, time_range=time_range)
    out = []
    for r in results:
        out.append({
            "id": r.get("id", ""),
            "layer": r.get("layer", ""),
            "content": (r.get("content") or "")[:1000],
            "score": round(r.get("final_score", r.get("hybrid_score", 0)), 4),
            "source": r.get("source", "hybrid"),
            "entities": [e.get("name", "") for e in r.get("entities", [])],
            "tags": r.get("tags", []),
            "importance": r.get("importance", 0.5),
            "created_at": r.get("created_at", ""),
        })
    return {"memories": out, "count": len(out)}


@mcp.tool
def write_memory(layer: str, content: str, entities: list[str] | None = None,
                 importance: float | None = None,
                 tags: list[str] | None = None) -> dict[str, Any]:
    """写入一条记忆（六层之一）。转发 brainmem Memorize。"""
    from brainmem.layers import VALID_LAYERS
    if layer not in VALID_LAYERS:
        return {"error": f"layer 非法：{layer}，有效值：{list(VALID_LAYERS)}"}
    if not content or not content.strip():
        return {"error": "content 不能为空"}
    mem = get_memorize()
    return mem.write_memory(content=content, layer=layer, entities=entities,
                            importance=importance, tags=tags, source="mcp")


@mcp.tool
def reindex(path: str | None = None, full: bool = False) -> dict[str, Any]:
    """增量/全量重新索引知识库。转发 brainmem Indexer。"""
    ix = get_indexer()
    if path:
        stats = ix.index_root(Path(path), full=full)
    else:
        root = brain_root()
        stats = ix.index_roots([root / "3-Knowledge", root / "4-Personal"], full=full)
    return {
        "indexed": stats.indexed,
        "scanned": stats.scanned,
        "skipped": stats.skipped_unchanged,
        "deleted": stats.deleted,
        "failed": stats.failed,
        "duration_ms": round(stats.duration_ms, 1),
        "errors": stats.errors[:10],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    """MCP server 入口（stdio）。python -m brainhub.mcp 调这里。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [brainhub] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info(f"brainhub MCP starting (BRAIN_ROOT={brain_root()}, BRAIN_DATA={brain_data()})")
    mcp.run()


if __name__ == "__main__":
    main()
