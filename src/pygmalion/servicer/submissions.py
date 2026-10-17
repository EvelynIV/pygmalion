from __future__ import annotations

import logging
import math
from typing import Any

from ..config import CONFIG
from ..submissions import repository as submissions
from ..writing import prepare as preparation
from .errors import ServiceError

logger = logging.getLogger(__name__)


def overrides_from_request(values) -> dict[str, Any]:
    """
    从 querystring 里取排版覆盖参数。

    预览和打印共用同一套解析，保证"看到的"和"写出来的"完全一致 ——
    如果两边各解析一套，迟早会出现预览是一种排法、实际写出来是另一种。

    所有参数都做范围钳制。这些值会直接变成机器运动指令，界面上一个
    手滑的输入不该变成撞机。
    """
    ov: dict[str, Any] = {}
    a = values or {}
    direction = a.get("direction")
    if direction in ("vertical", "horizontal"):
        ov["direction"] = direction
    colophon = a.get("colophon")
    if colophon in ("none", "author", "full", "seal"):
        ov["colophon"] = colophon
    font = a.get("font")
    if font:
        ov["font"] = font

    def num(key: str, lo: float, hi: float) -> None:
        raw = a.get(key)
        if raw in (None, ""):
            return
        try:
            v = float(raw)
        except (ValueError, TypeError):
            return
        if not math.isfinite(v):
            return
        ov[key] = max(lo, min(hi, v))

    num("font_size_mm", 4.0, 40.0)
    num("char_gap_mm", 0.0, 20.0)
    num("line_gap_mm", 0.0, 30.0)
    num("title_scale", 1.0, 2.5)
    num("handwriting_strength", 0.0, 3.0)
    hw = a.get("handwriting")
    if isinstance(hw, bool):
        ov["handwriting"] = hw
    elif hw not in (None, ""):
        ov["handwriting"] = hw not in ("0", "false", "False")
    return ov


def submit(data, client_ip):
    """接收一篇投稿。"""
    for key in (
        "author",
        "title",
        "body",
        "direction",
        "colophon",
        "signature",
        "font",
    ):
        if key in data and (not isinstance(data[key], str)):
            raise ServiceError("invalid", {"ok": False, "error": f"{key} 必须为文本"})
    author = (data.get("author") or "").strip()
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    direction = (data.get("direction") or "vertical").strip()
    colophon = (data.get("colophon") or "none").strip()
    signature = (data.get("signature") or "").strip()
    font = (data.get("font") or "").strip()
    if not author:
        raise ServiceError("invalid", {"ok": False, "error": "请填写你的名字"})
    if len(author) > 80 or len(title) > 200 or len(signature) > 200:
        raise ServiceError("invalid", {"ok": False, "error": "名字、诗题或署名过长"})
    if not body:
        raise ServiceError("invalid", {"ok": False, "error": "正文不能为空"})
    if len(body) > 2000:
        raise ServiceError(
            "invalid", {"ok": False, "error": "正文太长（上限 2000 字）"}
        )
    if direction not in ("vertical", "horizontal"):
        direction = "vertical"
    if colophon not in ("none", "author", "full", "seal"):
        colophon = "none"
    sub_id = submissions.add_submission(
        author=author,
        title=title,
        body=body,
        direction=direction,
        colophon=colophon,
        signature=signature,
        client_ip=client_ip,
        font=font,
    )
    return {"ok": True, "id": sub_id}


def my_preview(sub_id, query):
    """
    成员查看自己那篇的排版效果。

    只回传预览，不回传别人的内容，也不暴露任何管理能力 ——
    知道 id 就能看排版，但看不到机器状态、也改不了任何东西。
    """
    sub = submissions.get_submission(sub_id)
    if not sub:
        raise ServiceError("not_found", {"ok": False, "error": "投稿不存在"})
    try:
        fast_preview = query.get("fast") in ("1", "true", "True")
        p = preparation.prepare(sub, save_gcode=False, fast_preview=fast_preview)
    except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
        raise ServiceError("internal", {"ok": False, "error": f"排版失败: {exc}"})
    return {
        "ok": True,
        "svg": p.svg,
        "font_size_mm": round(p.font_size_mm, 2),
        "cells": p.cells,
        "missing": p.missing,
        "warnings": p.warnings,
        "can_print": p.can_print,
        "requested_size": p.requested_size,
        "shrunk": p.shrunk,
        "strokes": p.strokes,
    }


def list_submissions(query):
    status = query.get("status") or None
    if status == "all":
        status = None
    rows = submissions.list_submissions(status=status)
    return {"ok": True, "items": rows, "stats": submissions.stats()}


def admin_preview(sub_id, query):
    sub = submissions.get_submission(sub_id)
    if not sub:
        raise ServiceError("not_found", {"ok": False, "error": "投稿不存在"})
    try:
        fast_preview = query.get("fast") in ("1", "true", "True")
        p = preparation.prepare(
            sub,
            overrides_from_request(query),
            save_gcode=False,
            fast_preview=fast_preview,
        )
    except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
        raise ServiceError("internal", {"ok": False, "error": f"排版失败: {exc}"})
    return {
        "ok": True,
        "svg": p.svg,
        "font_size_mm": round(p.font_size_mm, 2),
        "requested_size": round(p.requested_size, 2),
        "shrunk": p.shrunk,
        "cells": p.cells,
        "strokes": p.strokes,
        "missing": p.missing,
        "warnings": p.warnings,
        "can_print": p.can_print,
        "font": p.font or "默认",
        "colophon": p.poem.colophon,
    }


def get_submission(sub_id):
    item = submissions.get_submission(sub_id)
    if not item:
        raise ServiceError("not_found", {"ok": False, "error": "投稿不存在"})
    return {"ok": True, "item": item, "layout": CONFIG["layout"]}


def delete_submission(sub_id, data):
    if not submissions.get_submission(sub_id):
        raise ServiceError("not_found", {"ok": False, "error": "投稿不存在"})
    submissions.delete_submission(sub_id)
    return {"ok": True}
