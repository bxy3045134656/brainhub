# -*- coding: utf-8 -*-
"""记忆面板路由 — query_memory（dense+sparse+graph 三路混合）。

调 brainmem Searcher.query_memory()，sync 调用用 anyio.to_thread.run_sync 包。
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


async def _query_memory_async(query: str, k: int = 5,
                              layers: list[str] | None = None) -> list[dict[str, Any]]:
    sr = get_searcher()
    return await anyio.to_thread.run_sync(
        lambda: sr.query_memory(query, k=k, layers=layers)
    )


def _format(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


@router.get("/memory", response_class=HTMLResponse)
async def memory_panel(request: Request, q: str = "", k: int = 5):
    """记忆面板：搜索框 + 结果（带 layer/entities）。"""
    results: list[dict[str, Any]] = []
    error = None
    if q.strip():
        try:
            raw = await _query_memory_async(q, k=k)
            results = _format(raw)
        except Exception as e:
            logger.exception("query_memory 失败")
            error = str(e)
    return request.app.state.templates.TemplateResponse(
        request, "_memory.html",
        {"request": request, "q": q, "results": results, "error": error},
    )


@router.post("/api/memory", response_class=HTMLResponse)
async def memory_search(request: Request, q: str = "", k: int = 5):
    return await memory_panel(request, q=q, k=k)
