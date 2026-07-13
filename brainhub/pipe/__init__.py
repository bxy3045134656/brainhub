# -*- coding: utf-8 -*-
"""命名管道子包 — BrainHub ↔ BrainBridge 通信。

对齐 BrainBridge 的 `matrix/pipe.go` 顶部注释协议规范：
  - 管道：
      Windows: \\\\.\\pipe\\brain-matrix-out   (BrainHub→Bridge 发消息请求)
               \\\\.\\pipe\\brain-matrix-in    (Bridge→BrainHub 推收到的 Matrix 消息)
               \\\\.\\pipe\\brain-agent-status (Bridge→BrainHub 推 agent 状态)
      Linux:   /tmp/brain-matrix-out.sock 等（Unix socket，对齐用）
  - 帧格式：每行一个 UTF-8 JSON，\\n 分隔
  - envelope = 协议宪法 §3（{type,from,to,task_id,spec_ref,text,ts}）
  - agent_status 帧 = {agents:[...], ts}
  - 无 ack / fire-and-forget；写失败仅 log

方向（BrainHub 端）：
  - writer  当 client 连 brain-matrix-out（Bridge 那边当 listener，go-winio Accept）
  - listener 当 server listen brain-matrix-in + brain-agent-status（Bridge 主动连入写）

⚠ BrainBridge 那边 Windows 命名管道 listener 尚未接 go-winio（pipe.go 返回
errWindowsPipeNotImpl），集成联调前 BrainHub 端先就绪：writer 能连上就发、连不上
记日志不崩；listener 起来等 Bridge 连入。真接 Bridge 时再端到端联调。

模块：
  protocol  envelope 构造 + 管道名常量
  writer    写客户端（BrainHub→Bridge）
  listener  读客户端 listener（Bridge→BrainHub，推 WSBroker）
"""

from brainhub.pipe.protocol import (
    Envelope,
    PIPE_MATRIX_OUT,
    PIPE_MATRIX_IN,
    PIPE_AGENT_STATUS,
    TYPE_TASK_ASSIGN,
    TYPE_TASK_RESULT,
    TYPE_HEARTBEAT,
    TYPE_NOTIFY,
    build_envelope,
)

__all__ = [
    "Envelope",
    "PIPE_MATRIX_OUT",
    "PIPE_MATRIX_IN",
    "PIPE_AGENT_STATUS",
    "TYPE_TASK_ASSIGN",
    "TYPE_TASK_RESULT",
    "TYPE_HEARTBEAT",
    "TYPE_NOTIFY",
    "build_envelope",
]
