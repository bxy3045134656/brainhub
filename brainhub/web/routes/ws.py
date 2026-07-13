# -*- coding: utf-8 -*-
"""WS 路由 — 单 /ws，订阅时收 topics，broker 分发。

面板连 /ws 后发 {"topics":["ops_log","agent_status"]} 订阅；ops/cron 经
WSBroker.publish(topic, msg) 推送。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from brainhub.web.ws import get_broker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # 第一条消息约定为订阅请求
        req = await ws.receive_json()
        topics = req.get("topics", [])
        if not isinstance(topics, list) or not topics:
            topics = ["ops_log", "agent_status"]
        broker = get_broker()
        await broker.subscribe(ws, topics)
        # 保持连接，等待服务端推送；receive 阻塞直到断开
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS 异常")
    finally:
        broker = get_broker()
        await broker.unsubscribe(ws)
