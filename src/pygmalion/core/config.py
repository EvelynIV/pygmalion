"""
配置加载。读取本机 config.json；首次部署可从 config.example.json 复制。
缺字段时用这里的默认值补齐。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import json
import os
import copy
import threading
from pathlib import Path
from typing import Any

ROOT = Path(
    os.environ.get("PYGMALION_HOME", Path(__file__).resolve().parents[3])
).resolve()
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
BACKUP_DIR = ROOT / "backups"

DEFAULTS: dict[str, Any] = {
    "machine": {
        "port": "auto",
        "baud": 115200,
        "pen_type": "stepper",
        "z_on": 2.0,
        "z_off": 0.0,
        "z_speed": 3000,
        "feed_rate": 3000,
        "jog_speed": 3000,
        "stroke_delay": 0.0,
    },
    "page": {
        "width_mm": 210.0,
        "height_mm": 297.0,
        "paper_preset": "A4",
        "origin": "top-left",
        "axes_swapped": True,
        "invert_x": False,
        "invert_y": False,
        "offset_x": 0.0,
        "offset_y": 0.0,
        # 固定刻度原点：笔尖对到机器刻度区左上角，不再要求碰到纸角。
        "fixed_grid_origin": True,
        "grid_width_mm": 190.0,
        "grid_height_mm": 280.0,
        # 整块盘面相对刻度区左上：左最多 100mm、上最多 50mm。
        "paper_min_x_mm": -100.0,
        "paper_min_y_mm": -50.0,
        # A4 右下与 190×280 参考区右下对齐。
        "paper_offset_x_mm": -20.0,
        "paper_offset_y_mm": -17.0,
        "margin_top_mm": 15.0,
        "margin_right_mm": 15.0,
        "margin_bottom_mm": 15.0,
        "margin_left_mm": 15.0,
    },
    "layout": {
        "direction": "vertical",
        "font_size_mm": 12.0,
        "char_gap_mm": 1.0,
        "line_gap_mm": 5.0,
        "title_scale": 1.25,
        "align": "center",
    },
    "handwriting": {
        "enabled": True,
        "rotate_deg": 2.0,
        "scale_jitter": 0.04,
        "pos_jitter_mm": 0.4,
        "baseline_jitter_mm": 0.5,
        "skeleton_strength": 0.15,
        "seed_random": True,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
    },
    "fonts": {
        "dir": "data/fonts",
        "latin": "hershey",
        "cjk": None,
        "gfont_dir": "data/gfonts",
        "default": "medians",
    },
    "stroke_quality": {
        "enabled": True,
        "corner_deg": 45.0,
        "samples": 4,
        "max_seg_mm": 0.4,
        "simplify_mm": 0.02,
    },
}

# 规格只负责填充纸张真实成品尺寸；能否放进当前写字盘仍由校准边界单独判断。
PAPER_PRESETS = [
    *[
        {
            "id": f"A{i}",
            "name": f"A{i}",
            "group": "A 系列（ISO 216）",
            "width_mm": w,
            "height_mm": h,
        }
        for i, (w, h) in enumerate(
            (
                (841, 1189),
                (594, 841),
                (420, 594),
                (297, 420),
                (210, 297),
                (148, 210),
                (105, 148),
                (74, 105),
                (52, 74),
                (37, 52),
                (26, 37),
            )
        )
    ],
    *[
        {
            "id": f"B{i}",
            "name": f"B{i}",
            "group": "B 系列（ISO 216）",
            "width_mm": w,
            "height_mm": h,
        }
        for i, (w, h) in enumerate(
            (
                (1000, 1414),
                (707, 1000),
                (500, 707),
                (353, 500),
                (250, 353),
                (176, 250),
                (125, 176),
                (88, 125),
                (62, 88),
                (44, 62),
                (31, 44),
            )
        )
    ],
    {
        "id": "folio-8",
        "name": "8 开",
        "group": "常用开本（成品规格）",
        "width_mm": 260,
        "height_mm": 370,
    },
    {
        "id": "folio-16",
        "name": "16 开",
        "group": "常用开本（成品规格）",
        "width_mm": 185,
        "height_mm": 260,
    },
    {
        "id": "folio-32",
        "name": "32 开",
        "group": "常用开本（成品规格）",
        "width_mm": 130,
        "height_mm": 185,
    },
    {
        "id": "folio-64",
        "name": "64 开",
        "group": "常用开本（成品规格）",
        "width_mm": 92,
        "height_mm": 130,
    },
    {
        "id": "folio-large-16",
        "name": "大 16 开",
        "group": "常用开本（成品规格）",
        "width_mm": 210,
        "height_mm": 285,
    },
    {
        "id": "folio-large-32",
        "name": "大 32 开",
        "group": "常用开本（成品规格）",
        "width_mm": 145,
        "height_mm": 210,
    },
]


def _merge(base: dict, override: dict) -> dict:
    """递归合并，override 覆盖 base。忽略 _ 开头的注释键。"""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    """读取配置。文件不存在或损坏时退回全默认值，不让程序起不来。"""
    if not CONFIG_PATH.exists():
        return _merge(DEFAULTS, {})
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _merge(DEFAULTS, {})
    return _merge(DEFAULTS, raw)


def ensure_dirs() -> None:
    """建好运行时要用的目录。"""
    for d in (DATA_DIR, OUTPUT_DIR, BACKUP_DIR, ROOT / "data" / "fonts"):
        d.mkdir(parents=True, exist_ok=True)


CONFIG = load()


# --------------------------------------------------------------------------
# 写回配置
# --------------------------------------------------------------------------


def _save_section(section: str, values: dict[str, Any]) -> dict[str, Any]:
    """
    把若干键值写回 config.json 的指定段，返回该段的完整新内容。

    为什么是"改文件里的键"而不是"dump 内存里的 CONFIG"：
    config.json 里那些 `_comment` / `_calibration` 注释块是给人看的文档，
    用程序内部的对象覆盖整个文件会把它们全冲掉。
    所以这里读原始 JSON、只动指定键、原样写回。

    ⚠️ 注意这些是**软件参数**（发什么 Z 坐标、走多快），不是机器 EEPROM 里的
    `$` 参数。改它们不会写机器、不需要备份闸门。真正改机器 `$` 参数的是
    /api/admin/settings，那条路有备份保护。
    """
    import json as _json

    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            raw = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, OSError):
            raw = {}

    sect = raw.setdefault(section, {})
    if not isinstance(sect, dict):
        sect = {}
        raw[section] = sect

    for k, v in values.items():
        sect[k] = v

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    pending_path = CONFIG_PATH.with_suffix(".json.tmp")
    pending_path.write_text(
        _json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    pending_path.replace(CONFIG_PATH)

    # 同步刷新内存里的配置，免得不重启就看不到新值
    CONFIG.clear()
    CONFIG.update(load())
    return CONFIG.get(section, {})


_SAVE_LOCK = threading.RLock()


def save_section(section: str, values: dict[str, Any]) -> dict[str, Any]:
    with _SAVE_LOCK:
        return _save_section(section, values)
