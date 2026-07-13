# -*- coding: utf-8 -*-
"""记忆提取 — 从 OpenClaw 当天对话日志提取记忆写 memory.db。

数据源（探查确认）：d:\\openclaw\\data\\.openclaw\\agents\\{main,ace,sentinel}\\sessions\\
下的 *.trajectory.jsonl。feishu-claude-bridge/sessions.json 带 lastActiveAt ISO8601，按日期过滤。

Phase 2 范围：普通函数直调 AsyncAnthropic（非 OpsAgent ReAct，降低风险）。
cron 23:00 触发，或 `brainhub ops extract-memories --date YYYY-MM-DD` 手动跑。

LLM prompt 必须显式抽人/病情实体（保证"心脏问题"实体存活，验收 #4 依赖）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from brainhub.config import openclaw_data, openclaw_log_dir, brain_root
from brainhub.storage.db import get_memorize, log_ops
from brainhub.web.ws import get_broker

logger = logging.getLogger(__name__)

# OpenClaw agent 名（sessions 子目录）
_AGENT_DIRS = ["main", "ace", "sentinel"]

# 单次 LLM 抽取的对话块大小（token 估算，中文 1 字≈1.5 token，~4k token 安全）
_CHUNK_CHARS = 6000

# 抽取 prompt：强制输出 JSON，强制实体抽取（含人/病情）。
_EXTRACT_PROMPT = """你是一个记忆抽取助手。下面是用户与 AI 助手的一段对话日志。
请从中抽取值得长期记住的事实性记忆，输出 JSON 数组，每条结构：
{
  "layer": "episodic" | "semantic" | "preference" | "procedural" | "core",
  "content": "记忆内容，中文，陈述事实，不要复述对话",
  "entities": [{"name": "实体名", "type": "person|condition|tool|project|place|other"}],
  "importance": 0.0-1.0
}

规则：
- 只抽事实性记忆（已发生的事、确定的知识、明确偏好），不要抽闲聊/客套。
- 层选择：个人经历/事件 → episodic；知识概念/事实 → semantic；偏好/习惯 → preference；
  操作流程/方法 → procedural；身份/长期事实 → core。
- 实体必须显式抽：涉及的人（如"鑫宇"）、病情/身体状况（如"心脏问题"、"体检"）、
  工具、项目名都要抽成 entities，type 尽量准确。病情一律 type=condition。
- importance：影响大的事实（健康、重大决策）≥0.7，普通知识 0.4-0.6。
- 没有可抽的记忆就返回空数组 []。
- 只输出 JSON 数组，不要任何解释文字。

对话日志：
"""


async def extract_memories(date_str: str, k_limit: int = 50) -> dict[str, Any]:
    """从 OpenClaw 指定日期对话日志提取记忆写 memory.db。

    Args:
        date_str: YYYY-MM-DD。
        k_limit: 最多写多少条（防 LLM 爆量）。
    Returns:
        {date, sessions_read, chunks, memories_written, layers, errors:[...]}
    """
    broker = get_broker()
    await broker.publish("ops_log", {
        "ts": _now_iso(), "task": "extract-memories",
        "action": f"start date={date_str}", "result": "ok",
    })

    # 1. 收集该日期的 trajectory 文件
    sessions = _collect_sessions(date_str)
    if not sessions:
        msg = f"未找到 {date_str} 的 OpenClaw 对话日志（检查 OPENCLAW_LOG_DIR）"
        await broker.publish("ops_log", {
            "ts": _now_iso(), "task": "extract-memories",
            "action": "no sessions", "result": "warn", "detail": msg,
        })
        log_ops("extract-memories", "no sessions", result="warn", detail=msg)
        return {"date": date_str, "sessions_read": 0, "chunks": 0,
                "memories_written": 0, "layers": [], "errors": [msg]}

    # 2. 读 + 拼对话文本
    dialog_text, sessions_read = _read_sessions_text(sessions)
    if not dialog_text.strip():
        return {"date": date_str, "sessions_read": sessions_read, "chunks": 0,
                "memories_written": 0, "layers": [], "errors": ["对话文本为空"]}

    # 3. 分块
    chunks = _chunk(dialog_text, _CHUNK_CHARS)

    # 4. 逐块调 LLM 抽取（AsyncAnthropic，xopglm52）
    try:
        import anthropic
    except ImportError as e:
        log_ops("extract-memories", "import anthropic", result="fail", detail=str(e))
        return {"date": date_str, "sessions_read": sessions_read, "chunks": len(chunks),
                "memories_written": 0, "layers": [], "errors": [f"anthropic 未装: {e}"]}

    client = anthropic.AsyncAnthropic()  # 自动读 ANTHROPIC_AUTH_TOKEN/BASE_URL/MODEL
    model = _resolve_model()

    mem = get_memorize()
    written = 0
    layers_seen: set[str] = set()
    errors: list[str] = []

    for i, chunk in enumerate(chunks):
        await broker.publish("ops_log", {
            "ts": _now_iso(), "task": "extract-memories",
            "action": f"LLM chunk {i + 1}/{len(chunks)}", "result": "ok",
        })
        try:
            memories = await _llm_extract(client, model, chunk)
        except Exception as e:
            err = f"chunk {i + 1} LLM 失败: {e}"
            errors.append(err)
            log_ops("extract-memories", f"LLM chunk {i + 1}", result="fail", detail=err)
            continue

        for m in memories[:k_limit]:
            try:
                # 实体归一成 Memorize.write_memory 接受的 [{name,type}]
                ents = m.get("entities") or []
                result = mem.write_memory(
                    content=m.get("content", ""),
                    layer=m.get("layer", "semantic"),
                    entities=ents,
                    importance=m.get("importance"),
                    source=f"extract:{date_str}",
                )
                written += 1
                layers_seen.add(result.get("layer", ""))
                if written >= k_limit:
                    break
            except Exception as e:
                errors.append(f"write_memory 失败: {e}")
        if written >= k_limit:
            break

    result = {
        "date": date_str, "sessions_read": sessions_read, "chunks": len(chunks),
        "memories_written": written, "layers": sorted(layers_seen), "errors": errors,
    }
    log_ops("extract-memories", "done", result="ok",
            detail=f"written={written} layers={sorted(layers_seen)} errors={len(errors)}")
    await broker.publish("ops_log", {
        "ts": _now_iso(), "task": "extract-memories",
        "action": f"done written={written}", "result": "ok",
        "detail": f"layers={sorted(layers_seen)}",
    })
    return result


# ---------------------------------------------------------------------------
# OpenClaw 日志定位 + 读取
# ---------------------------------------------------------------------------

def _collect_sessions(date_str: str) -> list[Path]:
    """收集指定日期活跃的 trajectory.jsonl 文件。

    优先用 sessions.json 的 lastActiveAt 过滤；无 sessions.json 则按文件 mtime 兜底。
    """
    log_root = openclaw_log_dir()
    sessions: list[Path] = []
    if not log_root.is_dir():
        return sessions

    # 各 agent 子目录
    for agent in _AGENT_DIRS:
        d = log_root / agent / "sessions"
        if not d.is_dir():
            continue
        # sessions.json（feishu bridge 那种带 lastActiveAt 的）
        idx = log_root / "feishu-claude-bridge" / "sessions.json"
        date_filter = _parse_date(date_str)
        if idx.is_file() and date_filter:
            sessions += _filter_by_sessions_index(d, idx, date_filter)
        else:
            # 兜底：按文件 mtime 日期
            for f in d.glob("*.trajectory.jsonl"):
                try:
                    mt = datetime.fromtimestamp(f.stat().st_mtime).date()
                    if mt == date_filter:
                        sessions.append(f)
                except Exception:
                    pass
    return sessions


def _filter_by_sessions_index(sessions_dir: Path, index_path: Path,
                              target: date) -> list[Path]:
    """用 sessions.json 的 lastActiveAt 过滤该日期的 trajectory.jsonl。"""
    try:
        idx = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[Path] = []
    for _k, v in idx.items():
        last = v.get("lastActiveAt", "") if isinstance(v, dict) else ""
        try:
            d = datetime.fromisoformat(last.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if d != target:
            continue
        sid = v.get("sessionId", "") if isinstance(v, dict) else ""
        if not sid:
            continue
        f = sessions_dir / f"{sid}.trajectory.jsonl"
        if f.is_file():
            out.append(f)
    return out


def _read_sessions_text(sessions: list[Path]) -> tuple[str, int]:
    """读 trajectory.jsonl，拼成对话文本（user/assistant 轮次）。

    OpenClaw trajectory 格式：每行一个事件 {type, data, ts, ...}。
    对话文本在 type=prompt.submitted / context.compiled 的 data.messages
    （[{role, content}]），以及 type=model.completed 的 data 里（assistant 回复）。
    去重：prompt.submitted 和 context.compiled 的 messages 大量重复，只取
    prompt.submitted 的 messages（每轮用户输入）+ model.completed 的 assistant 输出。
    """
    parts: list[str] = []
    read = 0
    seen_user_msgs: set[str] = set()  # 去重（同一 user 消息在多个事件里重复）
    for f in sessions:
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = obj.get("type", "")
                data = obj.get("data", {})
                if not isinstance(data, dict):
                    continue

                if etype == "prompt.submitted":
                    msgs = data.get("messages", [])
                    if isinstance(msgs, list):
                        for m in msgs:
                            role = m.get("role", "") if isinstance(m, dict) else ""
                            content = m.get("content", "") if isinstance(m, dict) else ""
                            content = _flatten_content(content)
                            if content and role == "user":
                                # 去重：同一 user 消息在多个事件里重复，按前 200 字签名去重。
                                sig = content[:200]
                                if sig in seen_user_msgs:
                                    continue
                                seen_user_msgs.add(sig)
                                parts.append(f"[user] {content[:2000]}")
                elif etype == "model.completed":
                    # assistant 输出：data.output 或 data.response 或 data.content
                    content = (data.get("output") or data.get("response")
                               or data.get("content") or "")
                    content = _flatten_content(content)
                    if content:
                        parts.append(f"[assistant] {content[:2000]}")
            read += 1
        except Exception as e:
            logger.warning(f"读 {f} 失败: {e}")
    return "\n\n".join(parts), read


def _flatten_content(content) -> str:
    """content 可能是 str / list[{type,text}] / list[str]。展平成 str。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for c in content:
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, dict):
                out.append(str(c.get("text", c.get("content", ""))))
        return " ".join(out)
    return str(content)


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

async def _llm_extract(client, model: str, chunk: str) -> list[dict[str, Any]]:
    """调 AsyncAnthropic 抽取记忆 JSON 数组。"""
    prompt = _EXTRACT_PROMPT + chunk[:8000]
    resp = await client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = _extract_text(resp)
    return _parse_json_array(text)


def _extract_text(resp) -> str:
    """从 anthropic Message 响应抽 text（兼容 content 为 list of blocks）。"""
    content = getattr(resp, "content", resp.get("content") if isinstance(resp, dict) else [])
    out: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                out.append(str(block.get("text", "")))
            else:
                out.append(getattr(block, "text", str(block)))
    elif isinstance(content, str):
        out.append(content)
    return "".join(out)


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """从 LLM 输出抠 JSON 数组（容忍前后解释文字 + ```json 围栏）。"""
    import re
    text = text.strip()
    # 去 ```json 围栏
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 找第一个 [ 到最后一个 ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start:end + 1])
        return [a for a in arr if isinstance(a, dict)] if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _chunk(text: str, size: int) -> list[str]:
    """按 size 字符切（不智能，对话日志够用）。"""
    return [text[i:i + size] for i in range(0, len(text), size) if text[i:i + size].strip()]


def _now_iso() -> str:
    from brainmem.time_sense import now_shanghai
    return now_shanghai().isoformat()


def _resolve_model() -> str:
    import os
    return os.environ.get("ANTHROPIC_MODEL", "xopglm52")


# ---------------------------------------------------------------------------
# CLI 入口（brainhub ops extract-memories --date 调）
# ---------------------------------------------------------------------------

def run_extract(date_str: str, log_path: str | None = None) -> dict[str, Any]:
    """同步入口：包 asyncio.run。"""
    if log_path:
        import os
        os.environ["OPENCLAW_LOG_DIR"] = log_path
    return asyncio.run(extract_memories(date_str))
