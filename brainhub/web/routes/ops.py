# -*- coding: utf-8 -*-
"""运维日志面板路由 — 历史 ops_log + WS 实时推送。

WS broker.publish("ops_log", step) 在 cron/extract-memories 跑时推；面板订阅后
Alpine 接收并 prepend 到列表。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from brainhub.storage.db import recent_ops_log

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ops", response_class=HTMLResponse)
async def ops_panel(request: Request, limit: int = 100):
    """运维日志面板：历史 + WS 实时。"""
    logs = recent_ops_log(limit=limit)
    return request.app.state.templates.TemplateResponse(
        request, "_ops.html",
        {"request": request, "logs": logs},
    )
