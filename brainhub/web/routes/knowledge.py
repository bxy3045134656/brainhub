# -*- coding: utf-8 -*-
"""知识库面板路由 — 目录树浏览 + 文件预览。

复用 brainmem.mcp 的 _is_blocked 路径拒绝逻辑（.trash/secrets/.git/.venv/.uv + BRAIN_ROOT
越界）。目录树 HTMX 按需 hx-get 加载；.md 预览用 Jinja2 渲染（或 markdown 库），PDF 预览
走 files.ensure_thumbnail 首页图（里程碑 B 补，Phase 2 此版先返回占位）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from brainhub.config import brain_root

router = APIRouter()

# 复用 brainmem.mcp 的拒绝清单（read_file/list_files 也用同一套）
_READ_BLOCKED_PATTERNS = [".trash/", "secrets.json", ".git/", ".venv/", ".uv/", "__pycache__/"]


def _is_blocked(rel_str: str) -> bool:
    rel_str = rel_str.replace("\\", "/")
    return any(p in rel_str for p in _READ_BLOCKED_PATTERNS)


def _safe_resolve(rel: str) -> Path:
    """把相对路径解析到 BRAIN_ROOT 下，越界/被拒则 raise 403。"""
    root = brain_root()
    p = (root / rel).resolve() if rel else root
    try:
        rel_to_root = p.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="路径越出 BRAIN_ROOT")
    if _is_blocked(str(rel_to_root)):
        raise HTTPException(status_code=403, detail="路径被拒绝（.trash/secrets/越界）")
    return p


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """知识库主面板：左侧目录树 + 右侧预览区。"""
    return request.app.state.templates.TemplateResponse(
        request, "base.html",
        {"request": request, "active_panel": "knowledge"},
    )


@router.get("/api/tree", response_class=HTMLResponse)
async def tree(request: Request, dir: str = ""):
    """目录树子节点（HTMX hx-get 按需加载）。

    返回 _tree.html partial：当前目录的子目录（可展开）+ 文件（点预览）。
    """
    base = _safe_resolve(dir)
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")

    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(base.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith(".") and child.name in {".git", ".venv", ".uv"}:
                continue
            rel = str(child.relative_to(brain_root().resolve())).replace("\\", "/")
            if _is_blocked(rel):
                continue
            entries.append({
                "name": child.name,
                "rel": rel,
                "is_dir": child.is_dir(),
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权限访问")

    return request.app.state.templates.TemplateResponse(
        request, "_tree.html",
        {"request": request, "entries": entries, "dir": dir},
    )


@router.get("/api/preview", response_class=HTMLResponse)
async def preview(request: Request, path: str):
    """文件预览：.md 渲染、其他文本直出、PDF/图片走缩略图（里程碑 B 补）。"""
    p = _safe_resolve(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = p.suffix.lower()
    content = ""
    if suffix == ".md":
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="gbk", errors="replace")
    elif suffix in {".txt", ".py", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".log", ".md"}:
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="gbk", errors="replace")
    else:
        # PDF/图片等：里程碑 B 补缩略图，此版占位提示
        return request.app.state.templates.TemplateResponse(
        request, "_preview.html",
            {"request": request, "path": path, "content": None,
             "note": f"预览 {suffix} 类型待里程碑 B（缩略图）实现。"},
        )

    return request.app.state.templates.TemplateResponse(
        request, "_preview.html",
        {"request": request, "path": path, "content": content, "note": None},
    )


@router.get("/api/health")
async def health():
    """轻量健康端点（cli status 用）。"""
    return {"status": "ok"}
