# -*- coding: utf-8 -*-
"""管道客户端测试 — envelope 序列化 + writer 不崩 + listener 起停。

不连真 BrainBridge（它 Windows listener 未起）。验证：
- envelope JSON 形状对齐协议宪法 §3（task_id/spec_ref omitempty）
- writer 对端没起时返回 False 不抛（fire-and-forget 契约）
- listener 能 CreateNamedPipe + 起后台线程 + 干净 stop
- 回环：起 listener，writer 连入写一行，on_frame 收到解析后的 dict
"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from brainhub.pipe.protocol import (
    Envelope, build_envelope,
    PIPE_MATRIX_OUT, PIPE_MATRIX_IN, TYPE_NOTIFY, TYPE_TASK_ASSIGN,
    pipe_path,
)


# ─── envelope 序列化 ───

class TestEnvelope:
    def test_notify_omits_optional_fields(self):
        """notify 不带 task_id/spec_ref 时 JSON 不含这两个字段（对齐 Go omitempty）。"""
        env = build_envelope(TYPE_NOTIFY, to="room1", text="hello")
        d = json.loads(env.to_json())
        assert d["type"] == "notify"
        assert d["from"] == "brainhub"
        assert d["to"] == "room1"
        assert d["text"] == "hello"
        assert "ts" in d
        # omitempty：空字段不应出现
        assert "task_id" not in d
        assert "spec_ref" not in d

    def test_task_assign_includes_task_id(self):
        env = build_envelope(
            TYPE_TASK_ASSIGN, to="agent1", text="干活",
            task_id="t_001", spec_ref="minio://spec/t001",
        )
        d = json.loads(env.to_json())
        assert d["task_id"] == "t_001"
        assert d["spec_ref"] == "minio://spec/t001"

    def test_to_json_single_line(self):
        """帧是单行 JSON（writer 加 \\n，本身不含换行）。"""
        env = build_envelope(TYPE_NOTIFY, to="r", text="x")
        s = env.to_json()
        assert "\n" not in s

    def test_pipe_path_windows(self):
        if sys.platform == "win32":
            assert pipe_path("brain-matrix-out") == r"\\.\pipe\brain-matrix-out"
        else:
            assert pipe_path("brain-matrix-out") == "/tmp/brain-matrix-out.sock"


# ─── writer（不崩契约）───

class TestWriter:
    def test_send_no_listener_returns_false(self):
        """对端 listener 没起，send 返回 False 不抛（fire-and-forget）。"""
        from brainhub.pipe import writer as writer_mod
        # 重置单例
        writer_mod._writer = None
        w = writer_mod.PipeWriter(from_="brainhub")
        env = build_envelope(TYPE_NOTIFY, to="room1", text="ping")
        # brain-matrix-out 没人 listen，应 False
        ok = w.send(env)
        assert ok is False

    def test_send_matrix_helper(self):
        from brainhub.pipe import writer as writer_mod
        writer_mod._writer = None
        out = writer_mod.send_matrix(
            TYPE_NOTIFY, to="r", text="hi", from_="brainhub",
        )
        assert out["ok"] is False
        assert out["type"] == "notify"
        assert out["to"] == "r"
        assert "error" in out


# ─── listener 起停 + 回环 ───

@pytest.mark.skipif(sys.platform != "win32", reason="命名管道 listener 仅 Windows")
class TestListener:
    def test_start_stop(self):
        """listener 能起后台线程 + 干净 stop。"""
        from brainhub.pipe import listener as listener_mod
        listener_mod._listeners = []
        listener_mod._main_loop = None
        frames: list = []
        lst = listener_mod._PipeListener(
            PIPE_MATRIX_IN, "matrix_in",
            lambda topic, f: frames.append((topic, f)),
        )
        ok = lst.start()
        assert ok is True
        assert lst._thread is not None
        assert lst._thread.is_alive()
        lst.stop()
        # daemon 线程，不强行 join（卡在 ConnectNamedPipe）
        assert lst._stop.is_set()

    def test_loopback_one_frame(self):
        """回环：起 listener，writer 连入写一行，on_frame 收到解析 dict。

        用独立管道路径（brain-matrix-out 同时当 listener + writer 的回环），
        避免和默认 brain-matrix-in（无对端写）耦合。直接用 _PipeListener + writer
        连同一个管道名，验证「起 server → client 连入写 → 按行收」整条链路。
        """
        from brainhub.pipe import listener as listener_mod
        from brainhub.pipe import writer as writer_mod

        listener_mod._listeners = []
        listener_mod._main_loop = None

        received: list[tuple[str, dict]] = []
        evt = threading.Event()
        lock = threading.Lock()

        def on_frame(topic, frame):
            with lock:
                received.append((topic, frame))
            evt.set()

        # 用 brain-matrix-out 做回环（同一进程既 listen 又 connect）
        lst = listener_mod._PipeListener(
            PIPE_MATRIX_OUT, "matrix_out", on_frame,
        )
        ok = lst.start()
        if not ok:
            pytest.skip("pywin32 缺失，listener 起不了")

        try:
            # 给 listener 一点时间 CreateNamedPipe + 进 ConnectNamedPipe
            time.sleep(0.3)
            # writer 连入写一行
            w = writer_mod.PipeWriter(from_="brainhub")
            env = build_envelope(TYPE_NOTIFY, to="room1", text="ping")
            sent = w.send(env)
            # 等读到
            assert evt.wait(timeout=3.0), "listener 未在超时内收到帧"
            assert len(received) >= 1
            topic, frame = received[0]
            assert topic == "matrix_out"
            assert frame["type"] == "notify"
            assert frame["to"] == "room1"
            assert frame["text"] == "ping"
            # 成功写入
            assert sent is True
        finally:
            lst.stop()

    def test_handle_line_bad_json(self):
        """坏 JSON 行不抛，记日志丢弃。"""
        from brainhub.pipe import listener as listener_mod
        lst = listener_mod._PipeListener(
            PIPE_MATRIX_IN, "matrix_in", lambda t, f: None,
        )
        # 不应抛
        lst._handle_line(b"not json")
        lst._handle_line(b'{"type":"notify","to":"r","text":"x","ts":"t"}')
