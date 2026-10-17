from __future__ import annotations

import logging
import math
from typing import Any

from ..config import CONFIG, PAPER_PRESETS
from .errors import ServiceError

MACHINE_FIELDS: dict[str, dict[str, Any]] = {
    "z_off": {
        "label": "抬笔高度",
        "unit": "mm",
        "min": 0.0,
        "max": 30.0,
        "step": 0.1,
        "help": "笔离开纸面多高。太小会蹭纸、笔尖拖出毛边；太大浪费时间。只要不蹭纸，越小越好。",
    },
    "z_on": {
        "label": "落笔高度",
        "unit": "mm",
        "min": 0.0,
        "max": 30.0,
        "step": 0.1,
        "help": "落笔时 Z 走到多低。值越大压得越深。太浅写不上，太深戳纸、笔尖容易劈。建议从 2.0 开始，以「线条清晰且不戳穿纸」为准。",
    },
    "table_segment_mm": {
        "label": "表格框线分段长度",
        "unit": "mm",
        "min": 0.5,
        "max": 20.0,
        "step": 0.5,
        "help": "每段画完抬笔，从同一端点继续，保持实线。默认每段最多 5 mm；只作用于表格框线。",
    },
    "stroke_delay": {
        "label": "每笔停顿",
        "unit": "秒",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "help": "每写完一笔停多久再写下一笔。笔画密的地方连续快速落笔，墨水来不及跟上会出现起笔断线。停一下让墨水润开。0 = 不停，速度最快。",
    },
    "z_speed": {
        "label": "抬落笔速度",
        "unit": "mm/min",
        "min": 100.0,
        "max": 5000.0,
        "step": 100.0,
        "help": "Z 轴升降快慢。太快落笔冲击大、可能丢步；太慢每笔都拖时间。",
    },
    "feed_rate": {
        "label": "书写速度",
        "unit": "mm/min",
        "min": 100.0,
        "max": 4000.0,
        "step": 100.0,
        "help": "落笔状态下画线的速度。机器上限是 $110/$111 = 4000。写太快笔画会飘、转折处发虚。",
    },
    "jog_speed": {
        "label": "空走速度",
        "unit": "mm/min",
        "min": 100.0,
        "max": 4000.0,
        "step": 100.0,
        "help": "抬笔状态下笔画之间移动的速度。不影响笔迹，只影响总耗时，可以设得比书写速度快。",
    },
}


logger = logging.getLogger(__name__)


def get_grid_calibration():
    """读取固定刻度原点与纸张摆放；只读，不碰机器。"""
    p = CONFIG["page"]
    grid_w = float(p.get("grid_width_mm", 190.0))
    grid_h = float(p.get("grid_height_mm", 280.0))
    paper_w = float(p["width_mm"])
    paper_h = float(p["height_mm"])
    paper_x = float(p.get("paper_offset_x_mm", 0.0))
    paper_y = float(p.get("paper_offset_y_mm", 0.0))
    min_x = float(p.get("paper_min_x_mm", -100.0))
    min_y = float(p.get("paper_min_y_mm", -50.0))
    fits = (
        min_x <= paper_x
        and min_y <= paper_y
        and (paper_x + paper_w <= grid_w + 1e-06)
        and (paper_y + paper_h <= grid_h + 1e-06)
    )
    return {
        "ok": True,
        "fixed_grid_origin": True,
        "origin_target": "刻度区域左上角（固定）",
        "grid_mm": [grid_w, grid_h],
        "paper_mm": [paper_w, paper_h],
        "paper_preset": p.get("paper_preset", "custom"),
        "paper_presets": PAPER_PRESETS,
        "paper_offset_mm": [paper_x, paper_y],
        "paper_bounds_mm": [min_x, min_y, grid_w, grid_h],
        "fits": fits,
    }


def set_grid_calibration(data):
    """保存纸张相对固定刻度原点的位置；只写本地配置，不发串口。"""
    incoming = (data or {}).get("values") or {}
    if not isinstance(incoming, dict):
        raise ServiceError("invalid", {"ok": False, "error": "校准数据格式不正确"})
    current = CONFIG["page"]
    names = {
        "grid_width_mm": ("grid_width_mm", 50.0, 400.0),
        "grid_height_mm": ("grid_height_mm", 50.0, 400.0),
        "paper_width_mm": ("width_mm", 20.0, 1500.0),
        "paper_height_mm": ("height_mm", 20.0, 1500.0),
        "paper_offset_x_mm": ("paper_offset_x_mm", -100.0, 400.0),
        "paper_offset_y_mm": ("paper_offset_y_mm", -50.0, 400.0),
    }
    preset_id = str(
        incoming.get("paper_preset", current.get("paper_preset", "custom"))
    ).strip()
    known_preset_ids = {item["id"] for item in PAPER_PRESETS}
    if preset_id not in known_preset_ids:
        preset_id = "custom"
    cleaned: dict[str, float | bool | str] = {
        "fixed_grid_origin": True,
        "origin_corner": "左上",
        "paper_preset": preset_id,
    }
    values = dict(current)
    rejected: list[str] = []
    for supplied, (stored, lo, hi) in names.items():
        raw = incoming.get(supplied, current.get(stored, 0.0))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            rejected.append(supplied)
            continue
        if not lo <= value <= hi:
            raise ServiceError(
                "invalid",
                {"ok": False, "error": f"{supplied} 必须在 {lo:g}–{hi:g} mm 内"},
            )
        values[stored] = round(value, 2)
        cleaned[stored] = round(value, 2)
    if rejected:
        raise ServiceError(
            "invalid", {"ok": False, "error": "不是数字：" + "、".join(rejected)}
        )
    min_x = float(current.get("paper_min_x_mm", -100.0))
    min_y = float(current.get("paper_min_y_mm", -50.0))
    if (
        values["paper_offset_x_mm"] < min_x
        or values["paper_offset_y_mm"] < min_y
        or values["paper_offset_x_mm"] + values["width_mm"]
        > values["grid_width_mm"] + 1e-06
        or (
            values["paper_offset_y_mm"] + values["height_mm"]
            > values["grid_height_mm"] + 1e-06
        )
    ):
        raise ServiceError(
            "invalid",
            {
                "ok": False,
                "error": "纸张必须落在写字盘范围内：最多左移 100mm、上移 50mm，右边和下边不可越过刻度区边界。",
            },
        )
    from .. import config as config_mod

    saved = config_mod.save_section("page", cleaned)
    return {
        "ok": True,
        "saved": {
            "grid_mm": [saved["grid_width_mm"], saved["grid_height_mm"]],
            "paper_mm": [saved["width_mm"], saved["height_mm"]],
            "paper_offset_mm": [saved["paper_offset_x_mm"], saved["paper_offset_y_mm"]],
            "paper_preset": saved.get("paper_preset", "custom"),
        },
    }


def _field_spec() -> list[dict[str, Any]]:
    """字段定义 + 当前值，给前端渲染表单用。"""
    m = CONFIG["machine"]
    out = []
    for key, spec in MACHINE_FIELDS.items():
        item = dict(spec)
        item["key"] = key
        item["value"] = m.get(key, 0.0)
        out.append(item)
    return out


def get_config():
    """读取当前的机器书写参数。"""
    return {
        "ok": True,
        "fields": _field_spec(),
        "note": "这些是软件参数（写在 config.json），不是机器的 $ 参数。改动不会写 EEPROM、不动机器任何设置，立即生效，无需备份。",
    }


def set_config(data):
    """
    保存机器书写参数。**只写 config.json，不碰机器。**

    每个字段按定义里的 min/max 钳制。越界的值会被夹到边界并如实告知，
    而不是默默接受 —— 一个手滑输的 500mm 落笔深度不该变成撞机。
    """
    data = data or {}
    incoming = data.get("values") or {}
    if not isinstance(incoming, dict) or not incoming:
        raise ServiceError("invalid", {"ok": False, "error": "没有要保存的参数"})
    cleaned: dict[str, float] = {}
    clamped: list[str] = []
    rejected: list[str] = []
    for key, raw in incoming.items():
        spec = MACHINE_FIELDS.get(key)
        if not spec:
            rejected.append(key)
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            rejected.append(key)
            continue
        lo, hi = (spec["min"], spec["max"])
        if not math.isfinite(v):
            rejected.append(key)
            continue
        if v < lo or v > hi:
            clamped.append(f"{spec['label']} {v:g} → {max(lo, min(hi, v)):g}")
            v = max(lo, min(hi, v))
        cleaned[key] = round(v, 4)
    if not cleaned:
        raise ServiceError(
            "invalid",
            {"ok": False, "error": f"没有可保存的字段（不认识的键：{rejected}）"},
        )
    from .. import config as config_mod

    config_mod.save_section("machine", cleaned)
    return {
        "ok": True,
        "saved": cleaned,
        "clamped": clamped,
        "rejected": rejected,
        "fields": _field_spec(),
    }
