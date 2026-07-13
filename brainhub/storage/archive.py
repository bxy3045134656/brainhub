# -*- coding: utf-8 -*-
"""笔记归档 — 硬编码 Index.md 关键词→目录表 + write_note + archive_inbox。

规则源：d:\\Brain\\Index.md 的分类规则表（已探明比 PLAN.md 的子集更全）。
策略：硬编码完整表（行为确定可解释）+ _parse_index_md_rules() 运行时解析兜底 +
单测断言硬编码表 == 当前 Index.md（漂移在测试期捕获）。

匹配原则（Index.md 原文）：
1. 按表顺序从上到下匹配，命中第一条即停止。
2. 最具体领域优先（STM32 优先于 OpenClaw）——由行序保证（Embedded 在 Toolchain 前）。
3. 无法匹配 → Toolchain/ 兜底。
4. 文件名 YYYY-MM-DD_简短关键词.md，重名加 _v2。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from brainhub.config import brain_root, brain_data
from brainhub.storage.db import log_ops
from brainmem.time_sense import now_shanghai

logger = logging.getLogger(__name__)

# 硬编码完整 Index.md 分类规则表（顺序敏感，首条命中即停）。
# 关键词 / 目标子目录名（3-Knowledge 下的子目录）。
ARCHIVE_RULES: list[tuple[list[str], str]] = [
    (["OpenClaw", "QwenPaw", "Agent", "记忆系统", "DreamWeave", "Heartbeat",
      "Cron", "技能", "安全红线", "harness"], "Agent"),
    (["LLM", "DeepSeek", "ViT", "国产生图", "AI市场", "EvoMap",
      "扩散模型", "生成模型"], "AI-LLM"),
    (["STM32", "GD32", "嵌入式", "RTOS", "UCOS", "DMA", "ADC", "UART",
      "SPI", "TideMemo", "油介损", "电网监测"], "Embedded"),
    (["FPGA", "Verilog", "CORDIC", "FFT", "FIR", "Zynq", "相位差", "PID",
      "pcm_audio", "DDIO", "FIFO", "PLL"], "FPGA"),
    (["芯片手册", "原理图", "数据手册", "AD9248", "AD7616", "AD8132", "ina818",
      "SP3485", "DM542", "TJA1050", "MCP2515"], "Hardware"),
    (["激光", "清障仪", "光路", "FFRC", "创鑫", "杰普特", "锐科",
      "大族光子", "波长光电", "维什", "红岸", "北创", "顺泰"], "Laser"),
    (["CAN", "CANopen", "USB转CAN", "BLE", "蓝牙", "I2C", "RS-485",
      "通信协议"], "Protocol"),
    (["OpenClaw运维", "FFmpeg", "Python", "SQLite", "pptx", "QQBot",
      "剪映", "CUDA", "Ubuntu", "踩坑", "代理", "梯子", "Bootstrap",
      "Harness"], "Toolchain"),
]

FALLBACK_DIR = "Toolchain"


def _parse_index_md_rules(index_path: Path | None = None) -> list[tuple[list[str], str]] | None:
    """运行时解析 Index.md 规则表（兜底，解析失败返回 None，调用方用硬编码表）。

    仅在需要时调用（如诊断硬编码表是否漂移）。生产路径用硬编码 ARCHIVE_RULES。
    """
    p = index_path or (brain_root() / "Index.md")
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None
    # 表行：| kw1/kw2/... | `Dir/` | 说明 | —— 提取第一列关键词 + 第二列目录名
    rules: list[tuple[list[str], str]] = []
    seen_dirs: set[str] = set()
    for line in text.splitlines():
        if "|" not in line or "`" not in line:
            continue
        m = re.match(r"\s*\|\s*(.*?)\s*\|\s*`([^`]+?)/`\s*\|", line)
        if not m:
            continue
        kw_cell = m.group(1).strip()
        target = m.group(2).strip()
        if not kw_cell or target in seen_dirs or target in {"", "分类"}:
            continue
        # 第一列形如 "OpenClaw/QwenPaw/Agent/…" 或 "规则关键词"
        if "/" not in kw_cell and "关键词" in kw_cell:
            continue  # 表头
        kws = [k.strip() for k in kw_cell.split("/") if k.strip()]
        if not kws:
            continue
        rules.append((kws, target))
        seen_dirs.add(target)
    return rules or None


def classify(title: str, content: str) -> str:
    """标题+内容 → 目标子目录名（首条命中即停，兜底 Toolchain）。

    顺序敏感：ARCHIVE_RULES 必须严格等于 Index.md 行序（Embedded 在 Protocol 前，
    "UART"/"SPI" 两行都有 → 命中 Embedded 先）。
    """
    text = f"{title}\n{content}"
    for keywords, target in ARCHIVE_RULES:
        for kw in keywords:
            if kw in text:
                return target
    return FALLBACK_DIR


# ---------------------------------------------------------------------------
# 文件名 slug + 去重
# ---------------------------------------------------------------------------

def _slug(s: str, max_len: int = 20) -> str:
    """标题 → 文件名安全 slug（中英数字保留，其余转 -，去首尾 -）。"""
    s = re.sub(r"[^\w一-鿿]+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or "note"


def _today_str() -> str:
    """今天日期（上海时区，与 brainmem now_shanghai 一致）。"""
    return now_shanghai().strftime("%Y-%m-%d")


def _dedupe_path(target_dir: Path, date_str: str, slug: str) -> Path:
    """文件名重名加 _v2 / _v3。"""
    base = target_dir / f"{date_str}_{slug}.md"
    if not base.exists():
        return base
    i = 2
    while True:
        cand = target_dir / f"{date_str}_{slug}_v{i}.md"
        if not cand.exists():
            return cand
        i += 1


# ---------------------------------------------------------------------------
# write_note（对外：MCP 工具 + archive_inbox 调）
# ---------------------------------------------------------------------------

def write_note(title: str, content: str, category: str | None = None) -> dict[str, Any]:
    """写一篇笔记到 3-Knowledge/{分类}/。

    - category=None 时用 classify(title, content) 自动分类。
    - 文件名 YYYY-MM-DD_slug.md，重名 _v2。
    - 自动加 # 标题头（content 无标题时补）。
    返回 {path, archived_to}（协议宪法 write_note 输出契约）。
    """
    cat = category or classify(title, content)
    target_dir = brain_root() / "3-Knowledge" / cat
    target_dir.mkdir(parents=True, exist_ok=True)

    date_str = _today_str()
    slug = _slug(title)
    path = _dedupe_path(target_dir, date_str, slug)

    # 内容加标题头（无 # 开头时补）
    body = content.strip()
    if not body.startswith("#"):
        body = f"# {title}\n\n{body}"
    path.write_text(body, encoding="utf-8")

    rel = str(path.relative_to(brain_root())).replace("\\", "/")
    log_ops("write_note", f"write {rel}", result="ok",
            detail=f"category={cat}")
    return {"path": rel, "archived_to": cat}


# ---------------------------------------------------------------------------
# archive_inbox（cron 02:30 调 + brainhub ops archive 调）
# ---------------------------------------------------------------------------

def archive_inbox() -> dict[str, Any]:
    """扫 2-Inbox/*.md，逐个分类移动到 3-Knowledge/{分类}/，写 ops_log。

    移动用 Path.replace（跨目录 rename，原子）。
    返回 {scanned, archived, moved:[...], fallback:[...], failed:[...]}。
    """
    inbox = brain_root() / "2-Inbox"
    if not inbox.is_dir():
        log_ops("archive", "inbox not found", result="warn",
                detail=str(inbox))
        return {"scanned": 0, "archived": 0, "moved": [], "fallback": [], "failed": []}

    md_files = sorted(inbox.glob("*.md"))
    moved: list[dict[str, str]] = []
    fallback: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    archived = 0

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = f.read_text(encoding="gbk", errors="replace")
        except Exception as e:
            failed.append({"path": f.name, "error": str(e)})
            continue

        title = _extract_title(f.name, content)
        cat = classify(title, content)
        target_dir = brain_root() / "3-Knowledge" / cat
        target_dir.mkdir(parents=True, exist_ok=True)

        # 保留原文件名（已是 YYYY-MM-DD_xxx.md 格式），重名加 _v2
        dest = target_dir / f.name
        if dest.exists():
            stem = f.stem
            suffix = f.suffix
            i = 2
            while True:
                cand = target_dir / f"{stem}_v{i}{suffix}"
                if not cand.exists():
                    dest = cand
                    break
                i += 1

        try:
            f.replace(dest)
            rel = str(dest.relative_to(brain_root())).replace("\\", "/")
            entry = {"path": rel, "category": cat, "from": f.name}
            if cat == FALLBACK_DIR:
                fallback.append(entry)
            else:
                moved.append(entry)
            archived += 1
            log_ops("archive", f"move {f.name} -> {rel}", result="ok",
                    detail=f"category={cat}")
        except Exception as e:
            failed.append({"path": f.name, "error": str(e)})
            log_ops("archive", f"move {f.name}", result="fail", detail=str(e))

    result = {
        "scanned": len(md_files),
        "archived": archived,
        "moved": moved,
        "fallback": fallback,
        "failed": failed,
    }
    log_ops("archive", "inbox sweep done", result="ok",
            detail=f"scanned={archived} failed={len(failed)}")
    return result


def _extract_title(filename: str, content: str) -> str:
    """从文件名或内容第一个 # 标题抽标题（分类用）。"""
    # 优先用内容第一个 # 标题
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    # 退而用文件名（YYYY-MM-DD_xxx.md → xxx）
    stem = Path(filename).stem
    if "_" in stem:
        parts = stem.split("_", 1)
        if len(parts) == 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
            return parts[1]
    return stem
