# -*- coding: utf-8 -*-
"""WebSocket broker — 单 /ws 连接，topic 分发。

ops agent / cron 任务通过 broker.publish(topic, msg) 推送进度；Web 面板订阅 topic
（ops_log / agent_status）实时接收。同进程同事件循环，publish 无订阅者时 no-op。

topic 约定：
- ops_log       ops 任务步骤（archive/reindex/extract-memories 的 thought/action/observation）
- agent_status  agent 在线状态（Phase 3 OpsAgent 跑时推；Phase 2 cron 任务起止推占位状态）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSBroker:
    """内存 topic 广播。订阅者带各自 topics 集合。"""

    def __init__(self) -> None:
        # (websocket, topics) 列表；用 list 而非 dict 便于多个订阅同 ws
        self._subs: list[tuple[WebSocket, set[str]]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket, topics: list[str]) -> None:
        async with self._lock:
            self._subs.append((ws, set(topics)))

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subs = [(w, t) for (w, t) in self._subs if w is not ws]

    async def publish(self, topic: str, msg: dict[str, Any]) -> None:
        """广播给订阅了 topic 的所有 ws。发送失败的 ws 静默丢弃（已断开）。"""
        if not self._subs:
            return
        payload = {"topic": topic, **msg}
        dead: list[WebSocket] = []
        for ws, topics in list(self._subs):
            if topic not in topics:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                # ws 已断开，标记清理
                dead.append(ws)
        if dead:
            async with self._lock:
                self._subs = [(w, t) for (w, t) in self._subs if w not in dead]


# ─── 进程级单例（web lifespan 建，ops/cron 经 app.state 拿）───
_broker: WSBroker | None = None


def get_broker() -> WSBroker:
    global _broker
    if _broker is None:
        _broker = WSBroker()
    return _broker


def set_broker(broker: WSBroker) -> None:
    """lifespan 启动时把 broker 注入单例（ops 模块经 get_broker 拿同一实例）。"""
    global _broker
    _broker = broker
