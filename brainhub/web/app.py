# -*- coding: utf-8 -*-
"""BrainHub FastAPI 主应用。

create_app() 在 lifespan 里把 store/searcher/memorize/indexer/hub_conn/config/ws_broker
挂到 app.state，路由经 request.app.state 拿共享单例。模块级 app 供 uvicorn
`brainhub.web.app:app`。

里程碑 A：knowledge + search + ws 路由。里程碑 B/C 的路由（files/board/memory/agents/ops）
逐步 include，先留占位返回。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from brainhub.config import load_hub_config, ensure_config, static_dir, templates_dir
from brainhub.storage.db import (
    get_store, get_searcher, get_memorize, get_indexer,
    get_hub_conn, close_all,
)
from brainhub.web.ws import WSBroker, set_broker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建单例 + 建模板 + 起 broker（+ 起 cron，里程碑 C 补）。

    Store 延迟建（首次访问 get_store 才真正加载 bge）——这里不主动 get_store，避免
    启动卡在模型加载。searcher/memorize/indexer 同理，按需建。
    """
    ensure_config()
    app.state.hub_config = load_hub_config()
    app.state.hub_conn = get_hub_conn()  # 自有表 conn，建表，轻量
    app.state.templates = Jinja2Templates(directory=str(templates_dir()))
    app.state.ws_broker = WSBroker()
    set_broker(app.state.ws_broker)

    # brainmem 单例标到 state（懒加载，真访问才建）
    app.state.get_store = get_store
    app.state.get_searcher = get_searcher
    app.state.get_memorize = get_memorize
    app.state.get_indexer = get_indexer

    # 里程碑 C：start_scheduler（--no-cron 时 BRAINHUB_NO_CRON=1，跳过）
    from brainhub.ops.cron import start_scheduler
    app.state.scheduler = start_scheduler(app)

    # 管道读 listeners（Bridge→BrainHub：brain-matrix-in + brain-agent-status）
    # 非 Windows / 缺 pywin32 时返回 ok=False，不阻塞 web。
    from brainhub.pipe.listener import start_listeners, stop_listeners
    try:
        app.state.pipe_listeners = start_listeners()
    except Exception:
        logger.exception("管道 listener 起失败（不影响 web）")
        app.state.pipe_listeners = None

    yield

    # 停管道 listeners
    if app.state.pipe_listeners is not None:
        try:
            stop_listeners()
        except Exception:
            pass
    # 关闭调度器 + 关闭连接
    if app.state.scheduler is not None:
        try:
            await app.state.scheduler.shutdown(wait=False)
        except Exception:
            pass
    close_all()


def create_app() -> FastAPI:
    app = FastAPI(title="BrainHub", lifespan=lifespan)

    # 静态资源（如有）
    if Path(static_dir()).is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")

    # 里程碑 A/B/C 路由
    from brainhub.web.routes import (
        knowledge, search, ws, files, board, memory, agents, ops,
    )
    app.include_router(knowledge.router)
    app.include_router(search.router)
    app.include_router(ws.router)
    app.include_router(files.router)
    app.include_router(board.router)
    app.include_router(memory.router)
    app.include_router(agents.router)
    app.include_router(ops.router)

    return app


app = create_app()
