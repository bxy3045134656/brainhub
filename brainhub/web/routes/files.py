# -*- coding: utf-8 -*-
"""网盘面板路由 — 文件列表 + 预览 + 删除 + 上传。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from brainhub.config import brain_root
from brainhub.storage.files import FileRepo, _safe_resolve, _iso_mtime
from brainhub.storage.db import get_hub_conn

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/files", response_class=HTMLResponse)
async def files_panel(request: Request, dir: str = ""):
    """网盘面板：当前目录文件列表 + 上传/删除。"""
    conn = get_hub_conn()
    repo = FileRepo(conn)
    try:
        entries = repo.list_dir(dir)
        error = None
    except ValueError as e:
        entries = []
        error = str(e)
    return request.app.state.templates.TemplateResponse(
        request, "_files.html",
        {"request": request, "entries": entries, "dir": dir, "error": error},
    )


@router.post("/api/files/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    dir: str = Form(""),
    file: UploadFile = File(...),
):
    """上传文件到指定目录（BRAIN_ROOT 下）。"""
    try:
        base = _safe_resolve(dir)
        if not base.is_dir():
            base.mkdir(parents=True, exist_ok=True)
        dest = base / (file.filename or "unnamed")
        # 重名加 _v2
        if dest.exists():
            i = 2
            while True:
                cand = dest.with_name(f"{dest.stem}_v{i}{dest.suffix}")
                if not cand.exists():
                    dest = cand
                    break
                i += 1
        with open(dest, "wb") as f:
            while chunk := await file.read(1 << 16):
                f.write(chunk)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(e))
    return await files_panel(request, dir=dir)


@router.post("/api/files/delete", response_class=HTMLResponse)
async def delete_file(request: Request, path: str = Form(...)):
    """删除文件（移到 1-trash 而非物理删，安全）。"""
    try:
        from pathlib import Path
        src = _safe_resolve(path)
        if src.is_file():
            trash = brain_root() / "1-trash"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / src.name
            if dest.exists():
                dest = trash / f"{src.stem}_v{int(_iso_mtime(src).replace('-','')[-6:])}{src.suffix}"
            src.replace(dest)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(e))
    # 返回到所在目录
    parent = str(path.rsplit("/", 1)[0]) if "/" in path else ""
    return await files_panel(request, dir=parent)
