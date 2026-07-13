# -*- coding: utf-8 -*-
"""运维 agent — Phase 2 只留接口壳，ReAct 循环主体留 Phase 3。

Phase 2 用 extract.py 的直调路径验证 LLM 网关 + anyio.to_thread 包 sync brainmem
跑通后，再在此迭代自主 ReAct。

预留规格（Phase 3 落地）：
- anthropic.AsyncAnthropic()（自动读 ANTHROPIC_AUTH_TOKEN/BASE_URL/MODEL=xopglm52）
- 工具集 = 11 个 MCP 工具同名 schema，但分发直调内部 Python（不开子进程走 stdio）
- max_steps=10、fail_switch_threshold=2
- 每步 await ws_broker.publish("ops_log", {step, thought, action, observation})
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class OpsAgent:
    """运维 agent 接口壳（Phase 3 补 ReAct 主体）。

    Phase 2：__init__ 建 AsyncAnthropic client + 工具 schema 定义，run() 抛
    NotImplementedError。extract-memories 不经此，走 extract.py 直调。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.max_steps = self.config.get("ops", {}).get("max_steps", 10)
        self.fail_switch = self.config.get("ops", {}).get("fail_switch_threshold", 2)
        self.model = self.config.get("ops", {}).get("model", "xopglm52")
        self._client = None  # 懒建：Phase 3 run() 首次访问才建 AsyncAnthropic
        # 工具 schema 占位（Phase 3 填 11 个，与 mcp.py 对齐）
        self.tools: list[dict[str, Any]] = []

    @property
    def client(self):
        """AsyncAnthropic（懒建，Phase 3 才用）。"""
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def run(self, task: str) -> dict[str, Any]:
        """ReAct 循环（Phase 3 实现）。

        Phase 2：占位，提示走 extract.py 直调路径。
        """
        raise NotImplementedError(
            "OpsAgent.run 留 Phase 3。Phase 2 的 LLM 路径走 brainhub.ops.extract "
            "（直调 AsyncAnthropic，非 ReAct 循环）。"
        )
