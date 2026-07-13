# -*- coding: utf-8 -*-
"""agent 状态面板路由 — Phase 2 占位（灯接 WS agent_status topic）。

Phase 3 OpsAgent 跑时推 agent_status（online/current_task）；Phase 2 cron 任务
起止推占位状态。此版渲染一个占位面板 + WS 订阅。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/agents", response_class=HTMLResponse)
async def agents_panel(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "_agents.html",
        {"request": request},
    )
