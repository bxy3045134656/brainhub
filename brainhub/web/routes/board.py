# -*- coding: utf-8 -*-
"""看板面板路由 — 项目/任务 CRUD + 拖拽改状态。

Alpine x-data 处理拖拽（draggable + @drop），hx-post /api/tasks/{id}/move 持久化。
状态机非法转移返回 400。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from brainhub.projects.models import ProjectRepo, TaskRepo, VALID_STATUSES
from brainhub.storage.db import get_hub_conn

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/board", response_class=HTMLResponse)
async def board(request: Request):
    """看板主视图：列（todo/doing/blocked/done）+ 任务卡。"""
    conn = get_hub_conn()
    projects = ProjectRepo(conn).list_all()
    tasks = TaskRepo(conn).list_all_grouped()
    return request.app.state.templates.TemplateResponse(
        request, "_board.html",
        {"request": request, "projects": projects, "tasks": tasks,
         "statuses": ["todo", "doing", "blocked", "done"]},
    )


@router.post("/api/projects", response_class=HTMLResponse)
async def create_project(request: Request, name: str = Form(...)):
    conn = get_hub_conn()
    try:
        ProjectRepo(conn).create(name)
    except Exception as e:
        logger.exception("创建项目失败")
    # 重渲染看板
    return await board(request)


@router.post("/api/projects/{pid}/status", response_class=HTMLResponse)
async def update_project_status(request: Request, pid: str, status: str = Form(...)):
    conn = get_hub_conn()
    try:
        ProjectRepo(conn).update_status(pid, status)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    return await board(request)


@router.post("/api/tasks", response_class=HTMLResponse)
async def create_task(
    request: Request,
    project_id: str = Form(...),
    title: str = Form(...),
    status: str = Form("todo"),
    assignee: str = Form(None),
):
    conn = get_hub_conn()
    try:
        TaskRepo(conn).create(project_id, title, status=status, assignee=assignee)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    return await board(request)


@router.post("/api/tasks/{tid}/move", response_class=HTMLResponse)
async def move_task(
    request: Request,
    tid: str,
    status: str | None = Form(None),
    ord: int | None = Form(None),
):
    """拖拽落点（Alpine @drop → hx-post）。"""
    conn = get_hub_conn()
    try:
        TaskRepo(conn).move(tid, status=status, ord=ord)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    return await board(request)


@router.delete("/api/tasks/{tid}")
async def delete_task(tid: str):
    conn = get_hub_conn()
    TaskRepo(conn).delete(tid)
    return {"deleted": tid}


@router.get("/api/tasks")
async def list_tasks():
    """纯 JSON（给 Alpine 初始化用，可选）。"""
    conn = get_hub_conn()
    return TaskRepo(conn).list_all_grouped()
