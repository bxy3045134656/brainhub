# -*- coding: utf-8 -*-
"""projects 状态机 + TaskRepo 拖拽落点单测。"""

import pytest

from brainhub.projects.models import (
    ProjectRepo, TaskRepo, VALID_STATUSES, is_valid_transition,
)
from brainhub.storage.db import get_hub_conn


def test_valid_transitions():
    assert is_valid_transition("todo", "doing")
    assert is_valid_transition("doing", "done")
    assert is_valid_transition("done", "todo")  # 可重开
    assert not is_valid_transition("done", "blocked")  # done 不能直接 blocked


def test_project_create_and_status():
    conn = get_hub_conn()
    repo = ProjectRepo(conn)
    p = repo.create("测试项目")
    assert p["status"] == "todo"
    updated = repo.update_status(p["id"], "doing")
    assert updated["status"] == "doing"


def test_project_illegal_transition_raises():
    conn = get_hub_conn()
    repo = ProjectRepo(conn)
    p = repo.create("非法转移测试")
    with pytest.raises(ValueError):
        repo.update_status(p["id"], "blocked")  # todo 不能直接 blocked


def test_task_create_and_move():
    conn = get_hub_conn()
    proj = ProjectRepo(conn).create("任务测试项目")
    trepo = TaskRepo(conn)
    t = trepo.create(proj["id"], "写文档", status="todo")
    assert t["status"] == "todo"
    moved = trepo.move(t["id"], status="doing")
    assert moved["status"] == "doing"


def test_task_move_illegal_raises():
    conn = get_hub_conn()
    proj = ProjectRepo(conn).create("非法拖拽项目")
    t = TaskRepo(conn).create(proj["id"], "任务", status="done")
    with pytest.raises(ValueError):
        trepo = TaskRepo(conn)
        trepo.move(t["id"], status="blocked")  # done 不能直接 blocked


def test_list_all_grouped():
    conn = get_hub_conn()
    proj = ProjectRepo(conn).create("分组测试")
    trepo = TaskRepo(conn)
    trepo.create(proj["id"], "t1", status="todo")
    trepo.create(proj["id"], "t2", status="doing")
    grouped = trepo.list_all_grouped()
    assert "todo" in grouped and "doing" in grouped
    assert len(grouped["todo"]) >= 1
    assert len(grouped["doing"]) >= 1
