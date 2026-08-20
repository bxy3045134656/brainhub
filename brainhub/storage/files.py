# -*- coding: utf-8 -*-
"""网盘文件 CRUD + 元数据 + 缩略图。

files 表存元数据（path/size/mtime/sha256/sync_state/thumb_path）。
缩略图懒生成（按预览请求），按 sha256 缓存到 BRAIN_DATA/cache/thumbs/：
- 图片：Pillow open + thumbnail(256)
- PDF：pdf2image.convert_from_path（首页，>50 页跳过）
- poppler 缺失降级 broken-image（不阻塞预览）
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from brainhub.config import brain_root, thumbs_dir, is_blocked_path

logger = logging.getLogger(__name__)

# 图片后缀（Pillow 能处理的常见类型）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _safe_resolve(rel: str) -> Path:
    """相对路径 → BRAIN_ROOT 下绝对路径，越界/被拒 raise ValueError。"""
    root = brain_root()
    p = (root / rel).resolve() if rel else root
    try:
        rel_to_root = p.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"路径越出 BRAIN_ROOT：{rel}")
    if is_blocked_path(str(rel_to_root)):
        raise ValueError(f"路径被拒绝：{rel}")
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _iso_mtime(path: Path) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat()


class FileRepo:
    """files 表 CRUD + 缩略图。"""

    def __init__(self, conn):
        self.conn = conn

    # ---- 元数据 ----

    def upsert(self, rel_path: str, size: int, mtime: str,
               sha256: str, sync_state: str = "ok",
               thumb_path: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO files(path, size, mtime, sha256, "
            "sync_state, thumb_path) VALUES (?,?,?,?,?,?)",
            (rel_path, size, mtime, sha256, sync_state, thumb_path),
        )

    def get(self, rel_path: str) -> dict[str, Any] | None:
        r = self.conn.execute(
            "SELECT * FROM files WHERE path=?", (rel_path,)
        ).fetchone()
        return dict(r) if r else None

    def list_dir(self, dir_rel: str = "") -> list[dict[str, Any]]:
        """列目录（文件系统实时扫，非纯 DB 查——保持与磁盘一致）。"""
        base = _safe_resolve(dir_rel)
        if not base.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for child in sorted(base.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            rel = str(child.relative_to(brain_root().resolve())).replace("\\", "/")
            if is_blocked_path(rel):
                continue
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
            out.append({
                "name": child.name,
                "rel": rel,
                "is_dir": is_dir,
                "size": size,
                "mtime": _iso_mtime(child),
            })
        return out

    # ---- 缩略图 ----

    def ensure_thumbnail(self, rel_path: str) -> str | None:
        """懒生成缩略图，返回相对 thumbs 目录的路径（或 None=无法生成）。

        按 sha256 缓存（同内容只生成一次）。
        """
        try:
            p = _safe_resolve(rel_path)
        except ValueError:
            return None
        if not p.is_file():
            return None

        suffix = p.suffix.lower()
        cache = thumbs_dir()
        sha = _sha256(p)
        thumb = cache / f"{sha}.png"
        if thumb.exists():
            return str(thumb.relative_to(brain_root().resolve().parent)).replace("\\", "/")

        try:
            if suffix in _IMAGE_EXTS:
                from PIL import Image
                img = Image.open(p)
                img.thumbnail((256, 256))
                img.save(thumb, "PNG")
            elif suffix == ".pdf":
                try:
                    from pdf2image import convert_from_path
                    pages = convert_from_path(
                        str(p), dpi=80, first_page=1, last_page=1,
                        size=(256, None),
                    )
                    if pages:
                        pages[0].save(thumb, "PNG")
                except Exception as e:
                    # poppler 缺失或 PDF 异常：不生成，返回 None（不阻塞）
                    logger.info(f"PDF 缩略图跳过 {rel_path}: {e}")
                    return None
            else:
                return None  # 非图片/PDF，无缩略图
        except Exception as e:
            logger.warning(f"缩略图生成失败 {rel_path}: {e}")
            return None

        # 回写 files 表
        self.upsert(rel_path, p.stat().st_size, _iso_mtime(p), sha,
                    thumb_path=str(thumb).replace("\\", "/"))
        return str(thumb).replace("\\", "/")

    # ---- 删除（网盘 CRUD）----

    def delete(self, rel_path: str) -> bool:
        """物理删除文件 + 清 files 元数据。"""
        try:
            p = _safe_resolve(rel_path)
        except ValueError:
            return False
        if not p.is_file():
            return False
        p.unlink()
        self.conn.execute("DELETE FROM files WHERE path=?", (rel_path,))
        return True
