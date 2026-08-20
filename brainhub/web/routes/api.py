# -*- coding: utf-8 -*-
"""JSON API v1 路由 — 给桌面端 React 前端用。

与现有 HTML 路由（knowledge/search/files/board/memory/ops）平行，复用同一批
service 层（store/searcher/repo），只把返回从 TemplateResponse 换成 JSONResponse。
HTML 路由不删（阶段0 Tauri 还套着现页）。

增强 /health：带 model_loaded，供 Tauri 判断 bge 是否预热完（风险 E）。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

from brainhub.config import brain_root, is_blocked_path
from brainhub.projects.models import ProjectRepo, TaskRepo
from brainhub.storage.db import get_hub_conn, get_searcher
from brainhub.storage.files import FileRepo, _safe_resolve, _iso_mtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


# ─── 知识库 ───────────────────────────────────────────────────────────────

def _safe_resolve_api(rel: str):
    """与 knowledge.py 的 _safe_resolve 同逻辑，越界/被拒 raise 403。"""
    root = brain_root()
    p = (root / rel).resolve() if rel else root
    try:
        rel_to_root = p.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="路径越出 BRAIN_ROOT")
    if is_blocked_path(str(rel_to_root)):
        raise HTTPException(status_code=403, detail="路径被拒绝")
    return p


@router.get("/tree")
async def tree(dir: str = "") -> JSONResponse:
    base = _safe_resolve_api(dir)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(base.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith(".") and child.name in {".git", ".venv", ".uv"}:
                continue
            rel = str(child.relative_to(brain_root().resolve())).replace("\\", "/")
            if is_blocked_path(rel):
                continue
            entries.append({"name": child.name, "rel": rel, "is_dir": child.is_dir()})
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权限访问")
    return JSONResponse({"entries": entries, "dir": dir})


@router.get("/preview")
async def preview(path: str) -> JSONResponse:
    p = _safe_resolve_api(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    suffix = p.suffix.lower()
    content: str | None = None
    note: str | None = None
    if suffix in {".md", ".txt", ".py", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".log"}:
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="gbk", errors="replace")
    else:
        note = f"预览 {suffix} 类型待缩略图实现（阶段2/里程碑B）。"
    return JSONResponse({"path": path, "content": content, "suffix": suffix, "note": note})


# ─── 搜索 / 记忆（复用 search.py / memory.py 的 _format 逻辑）──────────────

def _format_search(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in results:
        out.append({
            "doc_path": r.get("doc_path", ""),
            "chunk_text": (r.get("chunk_text") or "")[:1000],
            "score": round(r.get("final_score", r.get("score", 0)), 4),
            "source": r.get("source", "hybrid"),
            "mtime": r.get("mtime", ""),
        })
    return out


def _format_memory(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in results:
        out.append({
            "id": r.get("id", ""),
            "layer": r.get("layer", ""),
            "content": (r.get("content") or "")[:200],
            "score": round(r.get("final_score", r.get("hybrid_score", 0)), 4),
            "source": r.get("source", "hybrid"),
            "entities": [e.get("name", "") for e in r.get("entities", [])],
            "importance": r.get("importance", 0.5),
        })
    return out


@router.get("/search")
async def search(q: str = "", k: int = 5) -> JSONResponse:
    if not q.strip():
        return JSONResponse({"results": [], "q": q})
    try:
        sr = get_searcher()
        raw = await anyio.to_thread.run_sync(sr.search, q, k)
        return JSONResponse({"results": _format_search(raw), "q": q})
    except Exception as e:
        logger.exception("search 失败")
        return JSONResponse({"results": [], "q": q, "error": str(e)}, status_code=500)


@router.get("/memory")
async def memory(q: str = "", k: int = 5, layers: str | None = None) -> JSONResponse:
    if not q.strip():
        return JSONResponse({"results": [], "q": q})
    layer_list = [s.strip() for s in layers.split(",")] if layers else None
    try:
        sr = get_searcher()
        raw = await anyio.to_thread.run_sync(
            lambda: sr.query_memory(q, k=k, layers=layer_list)
        )
        return JSONResponse({"results": _format_memory(raw), "q": q})
    except Exception as e:
        logger.exception("memory 查询失败")
        return JSONResponse({"results": [], "q": q, "error": str(e)}, status_code=500)


# ─── 网盘 ──────────────────────────────────────────────────────────────────

@router.get("/files")
async def files_list(dir: str = "") -> JSONResponse:
    conn = get_hub_conn()
    repo = FileRepo(conn)
    try:
        entries = repo.list_dir(dir)
        return JSONResponse({"entries": entries, "dir": dir})
    except ValueError as e:
        return JSONResponse({"entries": [], "dir": dir, "error": str(e)}, status_code=403)


@router.post("/files/delete")
async def files_delete(path: str = Form(...)) -> JSONResponse:
    try:
        src = _safe_resolve(path)
        if src.is_file():
            trash = brain_root() / "1-trash"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / src.name
            if dest.exists():
                dest = trash / f"{src.stem}_v{int(_iso_mtime(src).replace('-', '')[-6:])}{src.suffix}"
            src.replace(dest)
        return JSONResponse({"ok": True, "path": path})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)


# ─── 看板 ──────────────────────────────────────────────────────────────────

@router.get("/board")
async def board() -> JSONResponse:
    conn = get_hub_conn()
    projects = ProjectRepo(conn).list_all()
    tasks = TaskRepo(conn).list_all_grouped()
    return JSONResponse({"projects": projects, "tasks": tasks,
                         "statuses": ["todo", "doing", "blocked", "done"]})


@router.post("/projects")
async def create_project(name: str = Form(...)) -> JSONResponse:
    conn = get_hub_conn()
    try:
        created = ProjectRepo(conn).create(name)
        return JSONResponse({"ok": True, "project": created})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/projects/{pid}/status")
async def update_project_status(pid: str, status: str = Form(...)) -> JSONResponse:
    conn = get_hub_conn()
    try:
        updated = ProjectRepo(conn).update_status(pid, status)
        return JSONResponse({"ok": True, "project": updated})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/tasks")
async def create_task(
    project_id: str = Form(...),
    title: str = Form(...),
    status: str = Form("todo"),
    assignee: str = Form(None),
) -> JSONResponse:
    conn = get_hub_conn()
    try:
        created = TaskRepo(conn).create(project_id, title, status=status, assignee=assignee)
        return JSONResponse({"ok": True, "task": created})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/tasks/{tid}/move")
async def move_task(
    tid: str,
    status: str | None = Form(None),
    ord: int | None = Form(None),
) -> JSONResponse:
    conn = get_hub_conn()
    try:
        moved = TaskRepo(conn).move(tid, status=status, ord=ord)
        return JSONResponse({"ok": True, "task": moved})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.delete("/tasks/{tid}")
async def delete_task(tid: str) -> JSONResponse:
    conn = get_hub_conn()
    n = TaskRepo(conn).delete(tid)
    return JSONResponse({"ok": True, "deleted": tid, "rows": n})


# ─── 运维日志 ───────────────────────────────────────────────────────────────

@router.get("/ops")
async def ops_list(limit: int = 100) -> JSONResponse:
    from brainhub.storage.db import recent_ops_log
    logs = recent_ops_log(limit=limit)
    return JSONResponse({"logs": logs, "limit": limit})


# ─── agent 状态（占位，Phase 3 OpsAgent 跑时填实）─────────────────────────

@router.get("/agents")
async def agents_list() -> JSONResponse:
    """占位：真实 agent 状态经 WS agent_status topic 推，无 HTTP 轮询源。
    阶段2 前端先拿空列表，状态灯靠 WS。"""
    return JSONResponse({"agents": [], "note": "agent 状态经 WS agent_status topic 实时推"})


# ─── 增强 health（Tauri 轮询判断 bge 预热，风险 E）────────────────────────

@router.get("/health")
async def health() -> JSONResponse:
    """增强健康端点：status + store_initialized（DB/后端是否就绪）。

    Tauri 侧轮询此端点判断后端可连。model_loaded（bge 是否预热）留作占位——
    lifespan 不主动加载 bge（首次 store/searcher 访问才载，~1-2min CPU），
    此端点不主动触发加载。阶段2 前端若需预热判断，单独加 /api/v1/store/status。
    """
    store_initialized = False
    try:
        get_hub_conn()
        store_initialized = True
    except Exception:
        store_initialized = False
    return JSONResponse({"status": "ok", "store_initialized": store_initialized,
                          "model_loaded": False, "note": "bge 懒加载，首次查询才载"})

