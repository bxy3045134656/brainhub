# -*- coding: utf-8 -*-
"""管道协议 — 对齐 BrainBridge `matrix/envelope.go` + `matrix/pipe.go`。

envelope = 协议宪法 §3，字段与 BrainBridge 的 Go struct 一一对应（含 json tag）：
  type     task_assign / task_result / heartbeat / notify
  from     发送方 agent_id（如 nuonuo / hermes / xinyu / brainhub）
  to       接收方 agent_id 或 room_id
  task_id  任务标识（派发/结果时必填，heartbeat/notify 可空，omitempty）
  spec_ref 任务规格引用（minio://path，可空，omitempty）
  text     消息正文
  ts       ISO8601 时间戳

agent_status 帧 = {agents:[...], ts}（不走 envelope，单独结构，见 listener.py）。

管道名（裸名，不含平台前缀）：
  brain-matrix-out    BrainHub→Bridge
  brain-matrix-in     Bridge→BrainHub（Matrix 收到的消息）
  brain-agent-status  Bridge→BrainHub（agent 状态）
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from brainmem.time_sense import now_shanghai

# 管道裸名（与 brainbridge/matrix/pipe.go 的常量一致）
PIPE_MATRIX_OUT = "brain-matrix-out"
PIPE_MATRIX_IN = "brain-matrix-in"
PIPE_AGENT_STATUS = "brain-agent-status"

# envelope type 常量（与 brainbridge/matrix/envelope.go 一致）
TYPE_TASK_ASSIGN = "task_assign"
TYPE_TASK_RESULT = "task_result"
TYPE_HEARTBEAT = "heartbeat"
TYPE_NOTIFY = "notify"

# BrainHub 默认 sender id（写客户端发出消息的 from 字段）
DEFAULT_FROM = "brainhub"


def pipe_path(name: str) -> str:
    """返回平台管道路径。

    Windows: \\\\\\\\.\\\\pipe\\\\<name>  （即 \\\\.\\pipe\\brain-matrix-out）
    Linux:   /tmp/<name>.sock（Unix socket，对齐 BrainBridge）
    """
    if sys.platform == "win32":
        # Windows 命名管道标准前缀 \\.\pipe\ （反斜杠分隔，注意 raw string 不能
        # 以单反斜杠结尾，用普通字符串显式构造）
        return "\\\\.\\pipe\\" + name
    return "/tmp/" + name + ".sock"


@dataclass
class Envelope:
    """Matrix 任务消息信封（协议宪法 §3，对齐 BrainBridge Envelope struct）。

    序列化时 task_id / spec_ref 为空则 omit（与 Go 的 omitempty 一致），
    避免对端收到空字符串字段。
    """
    type: str
    from_: str = DEFAULT_FROM
    to: str = ""
    text: str = ""
    task_id: str = ""
    spec_ref: str = ""
    ts: str = field(default_factory=lambda: now_shanghai().isoformat())

    def to_json(self) -> str:
        """序列化成单行 JSON（不含换行；writer 加 \\n）。"""
        import json
        d: dict[str, Any] = {
            "type": self.type,
            "from": self.from_,
            "to": self.to,
            "text": self.text,
            "ts": self.ts,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        if self.spec_ref:
            d["spec_ref"] = self.spec_ref
        return json.dumps(d, ensure_ascii=False)


def build_envelope(
    type: str,
    to: str,
    text: str,
    *,
    from_: str = DEFAULT_FROM,
    task_id: str = "",
    spec_ref: str = "",
    ts: str | None = None,
) -> Envelope:
    """便捷构造 envelope。"""
    return Envelope(
        type=type,
        from_=from_,
        to=to,
        text=text,
        task_id=task_id,
        spec_ref=spec_ref,
        ts=ts or now_shanghai().isoformat(),
    )
