# -*- coding: utf-8 -*-
"""archive 归档规则单测 — 8 类样例 + 顺序陷阱（UART 命中 Embedded）+ 兜底。"""
# -*- coding: utf-8 -*-

from brainhub.storage.archive import classify, write_note, FALLBACK_DIR


def test_classify_fpga():
    assert classify("CORDIC 笔记", "Zynq FPGA 相位差测量") == "FPGA"


def test_classify_embedded():
    assert classify("STM32 油介损", "GD32 DMA ADC 采集") == "Embedded"


def test_classify_agent():
    assert classify("OpenClaw 架构", "QwenPaw Agent 记忆系统") == "Agent"


def test_classify_order_uart_hits_embedded_not_protocol():
    """顺序陷阱：UART/SPI 在 Embedded 行和 Protocol 行都有，命中 Embedded 先。"""
    assert classify("UART 笔记", "SPI 通信") == "Embedded"


def test_classify_fallback_toolchain():
    """无法匹配 → Toolchain 兜底。"""
    assert classify("杂项", "一些无关内容") == FALLBACK_DIR


def test_classify_priority_specific_over_generic():
    """最具体领域优先：STM32 + Python 同时出现 → Embedded（行序在前），
    不会被 Toolchain 的 Python 抢走。"""
    assert classify("STM32 开发", "Python 脚本辅助") == "Embedded"


def test_write_note_creates_file():
    result = write_note("CORDIC 算法", "Zynq FPGA 实现相位差测量")
    assert "path" in result and result["archived_to"] == "FPGA"
    # 文件真存在
    from brainhub.config import brain_root
    p = brain_root() / result["path"]
    assert p.is_file()
    # 内容有标题头
    content = p.read_text(encoding="utf-8")
    assert content.startswith("# CORDIC 算法")


def test_write_note_dedupe_v2():
    """重名加 _v2。"""
    from brainhub.config import brain_root
    r1 = write_note("CORDIC 算法", "Zynq FPGA")  # 同标题同日
    r2 = write_note("CORDIC 算法", "Zynq FPGA")
    assert r1["path"] != r2["path"]
    assert "_v2" in r2["path"]
