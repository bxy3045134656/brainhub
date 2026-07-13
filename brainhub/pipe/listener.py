# -*- coding: utf-8 -*-
"""管道读客户端 listener — Bridge→BrainHub（brain-matrix-in + brain-agent-status）。

BrainHub 当 listener：CreateNamedPipe + ConnectNamedPipe 等连接，BrainBridge 主动连入
写。按行读 UTF-8 JSON，envelope（brain-matrix-in）推 WSBroker topic=matrix_in，
agent_status 帧（brain-agent-status）推 WSBroker topic=agent_status。

实现：pywin32（Windows 命名管道当 server 必须用，stdlib open 做不了 listener）。
退化：pywin32 缺失 / 非 Windows 时 start_listeners 返回空，记日志（不阻塞 web）。

并发模型：每个管道一个后台线程做 Accept→ConnectNamedPipe→读循环（阻塞 IO），
读到一行就 asyncio.run_coroutine_threadsafe 把推 WSBroker 的协程调度进主事件循环。
（pywin32 命名管道 IO 是阻塞的，丢线程跑，不阻塞 asyncio。）
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

from brainhub.pipe.protocol import PIPE_AGENT_STATUS, PIPE_MATRIX_IN, pipe_path

logger = logging.getLogger(__name__)

try:
    import win32file  # type: ignore
    import win32pipe  # type: ignore
    import pywintypes  # type: ignore
    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False

# 管道缓冲（字节）
_PIPE_BUF = 64 * 1024
# 单行最大长度（防恶意/异常大行占内存）
_MAX_LINE = 1024 * 1024

# 推 WSBroker 的 topic
TOPIC_MATRIX_IN = "matrix_in"
TOPIC_AGENT_STATUS = "agent_status"


class _PipeListener:
    """单个命名管道的 listener（后台线程跑 Accept→Read 循环）。"""

    def __init__(
        self,
        pipe_name: str,
        topic: str,
        on_frame: Callable[[dict[str, Any]], None],
    ) -> None:
        self.pipe_name = pipe_name
        self.path = pipe_path(pipe_name)
        self.topic = topic
        self.on_frame = on_frame
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """起后台线程。返回 False 表示环境不支持（pywin32 缺失）。"""
        if not _HAS_PYWIN32 or not _is_windows():
            logger.warning(
                "管道 listener %s 未起：pywin32=%s platform=%s（非 Windows 或缺依赖）",
                self.pipe_name, _HAS_PYWIN32, _is_windows(),
            )
            return False
        self._thread = threading.Thread(
            target=self._run, name=f"pipe-listen-{self.pipe_name}",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        # 线程卡在 ConnectNamedPipe 时无法直接唤醒；daemon=True 会在进程退出时随主退。
        # stop 主要给测试用（配合 disconnect）。

    # ------------------------------------------------------------------
    # 后台线程主体
    # ------------------------------------------------------------------

    def _run(self) -> None:
        logger.info("管道 listener 起：%s（topic=%s）", self.path, self.topic)
        while not self._stop.is_set():
            handle = None
            try:
                # 创建命名管道实例（server 端）。可多实例，这里单实例够用。
                handle = win32pipe.CreateNamedPipe(
                    self.path,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE
                    | win32pipe.PIPE_WAIT,
                    1,  # nMaxInstances
                    _PIPE_BUF, _PIPE_BUF, 0, None,
                )
            except pywintypes.error as e:
                logger.error("CreateNamedPipe(%s) 失败: %s", self.path, e)
                # 退避避免狂转
                self._stop.wait(1.0)
                continue

            try:
                # 阻塞等对端连入（BrainBridge 写端 connect）
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as e:
                # 对端连上后立即断开会触发 ERROR_PIPE_CONNECTED (535)，正常
                if getattr(e, "winerror", None) == 535:
                    pass
                else:
                    logger.debug("ConnectNamedPipe(%s): %s", self.path, e)
                    try:
                        win32file.CloseHandle(handle)
                    except Exception:
                        pass
                    continue

            if self._stop.is_set():
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                break

            # 连接已建立，读循环
            try:
                self._read_loop(handle)
            except Exception:
                logger.exception("读循环异常 %s", self.path)
            finally:
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
        logger.info("管道 listener 退：%s", self.path)

    def _read_loop(self, handle) -> None:
        """按行读 JSON 帧直到对端断开。"""
        buf = b""
        while not self._stop.is_set():
            try:
                # pywin32 ReadFile 返回 (hr, data)：hr=Win32 error code(0=成功),
                # data=读到的字节。注意不是 (data, size)。
                hr, data = win32file.ReadFile(handle, _PIPE_BUF)
            except pywintypes.error as e:
                # ERROR_BROKEN_PIPE (109) = 对端关闭，正常退出读循环
                if getattr(e, "winerror", None) == 109:
                    break
                logger.debug("ReadFile(%s): %s", self.path, e)
                break
            # hr 非 0 但非 109 也当异常（如 ERROR_MORE_DATA 234 是正常的，数据在 data 里继续）
            if not data and hr == 0:
                # 空读 + 无错误，继续（罕见）
                continue
            if data:
                buf += data
                # 按行切
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_line(line)
            if hr == 109:  # 对端断
                break

        # 处理末尾无换行的残余
        buf = buf.strip()
        if buf:
            self._handle_line(buf)

    def _handle_line(self, line: bytes) -> None:
        import json
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("管道 %s 解析帧失败: %s (line=%r)",
                           self.pipe_name, e, line[:200])
            return
        if not isinstance(obj, dict):
            logger.warning("管道 %s 帧非对象: %r", self.pipe_name, line[:200])
            return
        try:
            self.on_frame(self.topic, obj)
        except Exception:
            logger.exception("on_frame 回调异常（topic=%s）", self.topic)


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"


# ─── 进程级管理：start_listeners / stop_listeners ───
_listeners: list[_PipeListener] = []
# 主事件循环引用（线程回调用 run_coroutine_threadsafe 调度推 WSBroker）
_main_loop: asyncio.AbstractEventLoop | None = None


def _on_frame_threadsafe(topic: str, frame: dict[str, Any]) -> None:
    """读线程调：把推 WSBroker 的协程调度进主事件循环。"""
    if _main_loop is None or not _main_loop.is_running():
        # web 没起 / loop 没跑：丢弃（fire-and-forget，无订阅者本就 no-op）
        return
    asyncio.run_coroutine_threadsafe(
        _publish_to_broker(topic, frame), _main_loop,
    )


async def _publish_to_broker(topic: str, frame: dict[str, Any]) -> None:
    """主循环里把帧推 WSBroker（订阅者无则 no-op）。"""
    try:
        from brainhub.web.ws import get_broker
        broker = get_broker()
        await broker.publish(topic, frame)
    except Exception:
        logger.exception("推 WSBroker 失败（topic=%s）", topic)


def start_listeners() -> dict[str, bool]:
    """起 brain-matrix-in + brain-agent-status 两个 listener。

    在 web lifespan 启动时调（需已 set_broker + 主 loop 在跑）。
    Returns:
        {matrix_in: bool, agent_status: bool} 各 listener 是否起成功。
    """
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    specs = [
        (PIPE_MATRIX_IN, TOPIC_MATRIX_IN),
        (PIPE_AGENT_STATUS, TOPIC_AGENT_STATUS),
    ]
    out: dict[str, bool] = {}
    for name, topic in specs:
        lst = _PipeListener(name, topic, _on_frame_threadsafe)
        ok = lst.start()
        _listeners.append(lst)
        out[name] = ok
    logger.info("管道 listeners 起：%s", out)
    return out


def stop_listeners() -> None:
    """停所有 listener（web lifespan 关闭时调）。"""
    for lst in _listeners:
        lst.stop()
    _listeners.clear()
