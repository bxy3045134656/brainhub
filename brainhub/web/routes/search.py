# -*- coding: utf-8 -*-
"""搜索面板路由 — 顶栏全局语义搜索。

调 brainmem Searcher.search()（dense+sparse 混合 + 时间衰减）。Searcher.search 是 sync
且首次会加载 bge 模型（~1-2min CPU / ~3-5s GPU），用 anyio.to_thread.run_sync 包，不阻塞
事件循环。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from brainhub.storage.db import get_searcher

logger = logging.getLogger(__name__)

router = APIRouter()


async def _search_async(query: str, k: int = 5) -> list[dict[str, Any]]:
    """把 sync 的 Searcher.search 丢线程池跑，不阻塞事件循环。"""
    sr = get_searcher()
    return await anyio.to_thread.run_sync(sr.search, query, k)


def _format_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对齐 brainmem.mcp.search_knowledge 的输出 shape。"""
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


@router.get("/search", response_class=HTMLResponse)
async def search_get(request: Request, q: str = "", k: int = 5):
    """HTMX 搜索：返回 _search.html partial（结果列表）。"""
    results: list[dict[str, Any]] = []
    if q.strip():
        try:
            raw = await _search_async(q, k=k)
            results = _format_results(raw)
        except Exception as e:
            logger.exception("搜索失败")
            return request.app.state.templates.TemplateResponse(
        request, "_search.html",
                {"request": request, "q": q, "results": [], "error": str(e)},
            )
    return request.app.state.templates.TemplateResponse(
        request, "_search.html",
        {"request": request, "q": q, "results": results, "error": None},
    )


@router.post("/api/search", response_class=HTMLResponse)
async def search_post(request: Request, q: str = "", k: int = 5):
    """HTMX hx-post 入口（与 GET /search 同逻辑）。"""
    return await search_get(request, q=q, k=k)
