# -*- coding: utf-8 -*-
"""管道写客户端 — BrainHub→BrainBridge（brain-matrix-out）。

往 \\\\.\\pipe\\brain-matrix-out 写一行 JSON envelope + \\n + flush。
对端是 BrainBridge 的 listener（go-winio Accept，集成联调时补）。

实现：pywin32 win32file.CreateFile（语义准确、可设超时/pipe mode）；
退化：pywin32 缺失时用 open(path, 'r+b')（Windows 命名管道在 stdlib 也能 open，
但每次写完须 seek+truncate 避免残留，且打开阻塞行为略不同）。

fire-and-forget：写失败（对端没起 / 管道满）仅 log + 返回 False，不抛。
send_matrix_msg 工具 / ops agent 调 send()。
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from brainhub.pipe.protocol import (
    Envelope, PIPE_MATRIX_OUT, build_envelope, pipe_path,
)

logger = logging.getLogger(__name__)

# 写超时（毫秒）；pywin32 传给 CreateFile 的不在 CreateFile 本身设，
# 而是在 WaitNamedPipe / 写时用。这里先不用超时（fire-and-forget）。

# 尝试 import pywin32；缺失走 stdlib 退化
try:
    import win32file  # type: ignore
    import win32pipe  # type: ignore
    import pywintypes  # type: ignore
    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False


class PipeWriter:
    """brain-matrix-out 写客户端。

    每次 send 独立开/关连接（命名管道 fire-and-forget，对端按行读）。
    不长连：对端 listener 重启 / 单次写语义更稳，且避免连接状态难维护。
    若将来要长连，加 reconnect 逻辑即可。

    Args:
        from_: envelope 的 from 字段（默认 brainhub）。
    """

    def __init__(self, from_: str = "brainhub") -> None:
        self.from_ = from_

    def send(self, env: Envelope) -> bool:
        """写一个 envelope 到 brain-matrix-out。

        Returns:
            True 写成功；False 失败（对端没起 / 写异常，已 log）。
        """
        line = env.to_json() + "\n"
        data = line.encode("utf-8")
        if _HAS_PYWIN32 and sys.platform == "win32":
            return self._send_win32(data)
        return self._send_stdlib(data)

    def send_text(
        self, type: str, to: str, text: str,
        *, task_id: str = "", spec_ref: str = "",
    ) -> bool:
        """便捷发：构造 envelope 并写。"""
        env = build_envelope(
            type=type, to=to, text=text,
            from_=self.from_, task_id=task_id, spec_ref=spec_ref,
        )
        return self.send(env)

    # ------------------------------------------------------------------
    # pywin32 实现
    # ------------------------------------------------------------------

    def _send_win32(self, data: bytes) -> bool:
        path = pipe_path(PIPE_MATRIX_OUT)
        # 重试：对端 listener 正在 CreateNamedPipe→ConnectNamedPipe 过渡期，
        # CreateFile 会短暂 ERROR_FILE_NOT_FOUND。WaitNamedPipe + 小退避重试。
        for attempt in range(5):
            try:
                handle = win32file.CreateFile(
                    path,
                    win32file.GENERIC_WRITE,
                    0,  # 不共享
                    None,  # 默认 security
                    win32file.OPEN_EXISTING,
                    0,  # 默认属性
                    None,  # 默认 template
                )
                break
            except pywintypes.error as e:
                winerr = getattr(e, "winerror", None)
                # 2 = ERROR_FILE_NOT_FOUND（管道不存在，对端 listener 没起 / 过渡期）
                # 231 = ERROR_PIPE_BUSY（对端全忙，等一下重试）
                if winerr in (2, 231) and attempt < 4:
                    try:
                        # WaitNamedPipe 等对端 listener 起实例（最多等 2s）
                        win32pipe.WaitNamedPipe(path, 2000)
                    except pywintypes.error:
                        time.sleep(0.1)
                    continue
                logger.debug("管道写：连 %s 失败（对端 listener 未起?）: %s", path, e)
                return False
        else:
            return False
        try:
            win32file.WriteFile(handle, data)
            win32file.FlushFileBuffers(handle)
            return True
        except pywintypes.error as e:
            logger.warning("管道写失败（%s）: %s", path, e)
            return False
        finally:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # stdlib 退化（pywin32 缺失 / 非 Windows）
    # ------------------------------------------------------------------

    def _send_stdlib(self, data: bytes) -> bool:
        path = pipe_path(PIPE_MATRIX_OUT)
        try:
            # Windows 命名管道也能 open；Linux Unix socket 不能用 open（需 socket）
            with open(path, "r+b", buffering=0) as f:
                f.write(data)
                f.flush()
            return True
        except OSError as e:
            # FileNotFoundError = 管道不存在；对端没起
            logger.debug("管道写（stdlib）连 %s 失败: %s", path, e)
            return False


# ─── 进程级单例（mcp.py / ops 调 get_writer 拿同一实例）───
_writer: PipeWriter | None = None


def get_writer(from_: str = "brainhub") -> PipeWriter:
    global _writer
    if _writer is None:
        _writer = PipeWriter(from_=from_)
    return _writer


def send_matrix(
    type: str, to: str, text: str,
    *, task_id: str = "", spec_ref: str = "", from_: str = "brainhub",
) -> dict[str, Any]:
    """发一条 Matrix 消息到 brain-matrix-out（供 mcp 工具调）。

    Returns:
        {ok, pipe, type, to, error?}
    """
    w = get_writer(from_=from_)
    env = build_envelope(
        type=type, to=to, text=text,
        from_=from_, task_id=task_id, spec_ref=spec_ref,
    )
    ok = w.send(env)
    out: dict[str, Any] = {
        "ok": ok,
        "pipe": pipe_path(PIPE_MATRIX_OUT),
        "type": type,
        "to": to,
    }
    if not ok:
        out["error"] = "写管道失败（对端 BrainBridge listener 未起？）"
    return out
