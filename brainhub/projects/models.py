# -*- coding: utf-8 -*-
"""项目看板 — projects + tasks 表 + 状态机。

状态机：todo → doing → blocked → done（done 终态）。
非法转移 raise ValueError（路由层返回 400）。
drag-drop 落点经 TaskRepo.move(id, status?, ord?) 持久化。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from brainmem.time_sense import now_shanghai

# 合法状态
VALID_STATUSES = {"todo", "doing", "blocked", "done"}

# 状态机：todo → doing → blocked → done（严格流：todo 只进 doing；done 可重开回 todo）。
TRANSITIONS: dict[str, set[str]] = {
    "todo": {"doing"},
    "doing": {"blocked", "done"},
    "blocked": {"doing", "done"},
    "done": {"todo"},  # done 可重开（回 todo）
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    if from_status == to_status:
        return True
    return to_status in TRANSITIONS.get(from_status, set())


def _new_id(prefix: str) -> str:
    # 加微秒精度 + 随机量防同微秒碰撞（同秒内连建多个 ID 不撞）。
    raw = f"{now_shanghai().isoformat(timespec='microseconds')}{os.urandom(4).hex()}"
    return f"{prefix}_{hashlib.sha1(raw.encode()).hexdigest()[:12]}"


class ProjectRepo:
    """projects 表 CRUD。"""

    def __init__(self, conn):
        self.conn = conn

    def create(self, name: str, status: str = "todo") -> dict[str, Any]:
        now = now_shanghai().isoformat()
        pid = _new_id("proj")
        self.conn.execute(
            "INSERT INTO projects(id, name, status, created_at, updated_at) VALUES (?,?,?,?,?)",
            (pid, name, status, now, now),
        )
        return {"id": pid, "name": name, "status": status,
                "created_at": now, "updated_at": now}

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, pid: str) -> dict[str, Any] | None:
        r = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (pid,)
        ).fetchone()
        return dict(r) if r else None

    def update_status(self, pid: str, status: str) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"非法状态：{status}")
        cur = self.get(pid)
        if not cur:
            raise ValueError(f"项目不存在：{pid}")
        if not is_valid_transition(cur["status"], status):
            raise ValueError(f"非法转移：{cur['status']} → {status}")
        now = now_shanghai().isoformat()
        self.conn.execute(
            "UPDATE projects SET status=?, updated_at=? WHERE id=?",
            (status, now, pid),
        )
        cur["status"] = status
        cur["updated_at"] = now
        return cur

    def delete(self, pid: str) -> int:
        self.conn.execute("DELETE FROM tasks WHERE project_id=?", (pid,))
        cur = self.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        return cur.rowcount or 0


class TaskRepo:
    """tasks 表 CRUD + 拖拽落点。"""

    def __init__(self, conn):
        self.conn = conn

    def create(self, project_id: str, title: str,
               status: str = "todo", assignee: str | None = None) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"非法状态：{status}")
        now = now_shanghai().isoformat()
        tid = _new_id("task")
        # ord = 当前项目该列任务数（追加到末尾）
        max_ord = self.conn.execute(
            "SELECT COALESCE(MAX(ord), -1) FROM tasks WHERE project_id=? AND status=?",
            (project_id, status),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO tasks(id, project_id, title, status, assignee, ord, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, project_id, title, status, assignee, max_ord + 1, now, now),
        )
        return {"id": tid, "project_id": project_id, "title": title,
                "status": status, "assignee": assignee, "ord": max_ord + 1,
                "created_at": now, "updated_at": now}

    def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY status, ord",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_grouped(self) -> dict[str, list[dict[str, Any]]]:
        """所有任务按状态分桶（看板列）。"""
        out = {s: [] for s in VALID_STATUSES}
        rows = self.conn.execute(
            "SELECT t.*, p.name AS project_name FROM tasks t "
            "LEFT JOIN projects p ON t.project_id = p.id "
            "ORDER BY t.status, t.ord"
        ).fetchall()
        for r in rows:
            d = dict(r)
            out.setdefault(d["status"], []).append(d)
        return out

    def move(self, tid: str, status: str | None = None,
             ord: int | None = None) -> dict[str, Any]:
        """拖拽落点：改 status 和/或 ord。状态转移校验。

        ord 调整：同列内重排（drag-reorder）；跨列时 ord 追加到目标列末尾。
        """
        r = self.conn.execute(
            "SELECT * FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        if not r:
            raise ValueError(f"任务不存在：{tid}")
        cur = dict(r)
        now = now_shanghai().isoformat()

        new_status = status if status is not None else cur["status"]
        if new_status not in VALID_STATUSES:
            raise ValueError(f"非法状态：{new_status}")
        if new_status != cur["status"] and \
                not is_valid_transition(cur["status"], new_status):
            raise ValueError(f"非法转移：{cur['status']} → {new_status}")

        if ord is not None:
            new_ord = int(ord)
        elif status is not None and status != cur["status"]:
            # 跨列：追加到目标列末尾
            max_ord = self.conn.execute(
                "SELECT COALESCE(MAX(ord), -1) FROM tasks WHERE project_id=? AND status=?",
                (cur["project_id"], new_status),
            ).fetchone()[0]
            new_ord = max_ord + 1
        else:
            new_ord = cur["ord"]

        self.conn.execute(
            "UPDATE tasks SET status=?, ord=?, updated_at=? WHERE id=?",
            (new_status, new_ord, now, tid),
        )
        cur["status"] = new_status
        cur["ord"] = new_ord
        cur["updated_at"] = now
        return cur

    def delete(self, tid: str) -> int:
        cur = self.conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
        return cur.rowcount or 0
