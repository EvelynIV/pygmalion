"""FastAPI 业务接口；保留参考项目字段，管理工作区无鉴权。"""

from __future__ import annotations

import re
import os
import functools
import math
import socket
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..core import db, printer
from ..core.config import CONFIG, ROOT, PAPER_PRESETS
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def device_guard(function):
    @functools.wraps(function)
    def guarded(request: Request, *args, **kwargs):
        service = request.app.state.printer
        if service.current or not service.device_lock.acquire(blocking=False):
            return JSONResponse(
                {"ok": False, "error": "机器正在书写或处理另一项操作"}, status_code=409
            )
        try:
            return function(request, *args, **kwargs)
        finally:
            service.device_lock.release()

    return guarded


def local_ips() -> list[str]:
    """
    本机所有可用的局域网 IP，用来告诉成员该访问哪个地址。

    热点场景下会有多个网卡（有线、无线、热点），逐个列出来比只显示一个
    更实用 —— 用户能一眼找到自己手机连的那个网段。
    """
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted((ip for ip in ips if not ip.startswith("127.")))


def overrides_from_request(values: Any | None = None) -> dict[str, Any]:
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


@router.get("/api/setup", response_model=None)
def api_setup(request: Request):
    """
    操作相关的只读信息：笔尖该放哪个角、整张纸占机器的哪段范围。

    公开接口 —— 管理页要用它显示对位提示。不含任何机器控制能力，
    也不暴露路径。改原点角之后这里的文字会自动跟着变，不用改前端。
    """
    from ..core import gcode

    cfg = printer.build_configs()
    tf = gcode.PageToMachine(cfg["machine"], cfg["page"])
    x0, y0, x1, y1 = tf.bounds()
    page = cfg["page"]
    return {
        "ok": True,
        "origin_corner": "左上"
        if cfg["machine"].fixed_grid_origin
        else CONFIG["page"].get("origin_corner", "左上"),
        "origin_target": "刻度区域的左上角"
        if cfg["machine"].fixed_grid_origin
        else f"纸张的{CONFIG['page'].get('origin_corner', '左上')}角",
        "fixed_grid_origin": cfg["machine"].fixed_grid_origin,
        "hint": tf.page_origin_hint(),
        "page_mm": [page.width_mm, page.height_mm],
        "machine_bounds": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
        "negative_travel": {
            "x": round(abs(min(x0, 0.0)), 1),
            "y": round(abs(min(y0, 0.0)), 1),
        },
    }


@router.get("/api/admin/grid-calibration", response_model=None)
def api_get_grid_calibration(request: Request):
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


@router.post("/api/admin/grid-calibration", response_model=None)
def api_set_grid_calibration(
    request: Request, data: dict[str, Any] = Body(default_factory=dict)
):
    """保存纸张相对固定刻度原点的位置；只写本地配置，不发串口。"""
    incoming = (data or {}).get("values") or {}
    if not isinstance(incoming, dict):
        return JSONResponse(
            content={"ok": False, "error": "校准数据格式不正确"}, status_code=400
        )
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
            return JSONResponse(
                content={
                    "ok": False,
                    "error": f"{supplied} 必须在 {lo:g}–{hi:g} mm 内",
                },
                status_code=400,
            )
        values[stored] = round(value, 2)
        cleaned[stored] = round(value, 2)
    if rejected:
        return JSONResponse(
            content={"ok": False, "error": "不是数字：" + "、".join(rejected)},
            status_code=400,
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
        return JSONResponse(
            content={
                "ok": False,
                "error": "纸张必须落在写字盘范围内：最多左移 100mm、上移 50mm，右边和下边不可越过刻度区边界。",
            },
            status_code=400,
        )
    from ..core import config as config_mod

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


@router.get("/api/admin/config", response_model=None)
def api_get_config(request: Request):
    """读取当前的机器书写参数。"""
    return {
        "ok": True,
        "fields": _field_spec(),
        "note": "这些是软件参数（写在 config.json），不是机器的 $ 参数。改动不会写 EEPROM、不动机器任何设置，立即生效，无需备份。",
    }


@router.post("/api/admin/config", response_model=None)
def api_set_config(request: Request, data: dict[str, Any] = Body(default_factory=dict)):
    """
    保存机器书写参数。**只写 config.json，不碰机器。**

    每个字段按定义里的 min/max 钳制。越界的值会被夹到边界并如实告知，
    而不是默默接受 —— 一个手滑输的 500mm 落笔深度不该变成撞机。
    """
    data = data or {}
    incoming = data.get("values") or {}
    if not isinstance(incoming, dict) or not incoming:
        return JSONResponse(
            content={"ok": False, "error": "没有要保存的参数"}, status_code=400
        )
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
        return JSONResponse(
            content={
                "ok": False,
                "error": f"没有可保存的字段（不认识的键：{rejected}）",
            },
            status_code=400,
        )
    from ..core import config as config_mod

    config_mod.save_section("machine", cleaned)
    return {
        "ok": True,
        "saved": cleaned,
        "clamped": clamped,
        "rejected": rejected,
        "fields": _field_spec(),
    }


@router.post("/api/admin/estop", response_model=None)
def api_estop(request: Request, data: dict[str, Any] = Body(default_factory=dict)):
    """
    急停。立即停下并**确保机器不会卡在 Hold**。

    和「中止」的区别：
      中止   停止发送新指令，让已经排进缓冲区的运动走完（几秒钟），
             位置不丢、文件可续写。日常用这个。
      急停   立刻停，丢弃未完成的运动，笔可能停在半路。出事时用这个。

    两种情况都不会把机器留在 Hold 状态 —— 那是之前的 bug，会让机器
    "按了没反应"。
    """
    from ..core import grbl

    m = CONFIG["machine"]
    if request.app.state.printer.emergency_stop():
        return {"ok": True, "state": "急停指令已发送，请重新读取设备状态并对位"}
    try:
        with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
            state = link.emergency_stop(pen_up_z=m["z_off"])
        return {"ok": True, "state": state}
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=503,
        )


@router.post("/api/admin/recover", response_model=None)
@device_guard
def api_recover(request: Request, data: dict[str, Any] = Body(default_factory=dict)):
    """
    把机器从 Hold / Alarm 里救出来。

    防的就是"机器卡住了按什么都没反应" —— 这个接口只做恢复，
    不做任何运动（除了抬笔），随时可以安全地点。
    """
    from ..core import grbl

    m = CONFIG["machine"]
    try:
        with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
            before = link.state()
            after = link.recover_if_stuck(pen_up_z=m["z_off"])
        return {
            "ok": True,
            "before": before,
            "after": after,
            "recovered": before != after,
        }
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=503,
        )


@router.get("/api/fonts", response_model=None)
def api_fonts(request: Request):
    """
    可用字库列表。公开接口 —— 提交页也要用它给作者选字体。
    只暴露名字、字数这类展示信息，不含任何路径。
    """
    from ..core import fonts as font_registry

    available = font_registry.discover(ROOT)
    return {
        "ok": True,
        "default": font_registry.default_id(ROOT),
        "fonts": [f.to_dict() for f in available],
    }


@router.post("/api/submit", response_model=None)
def api_submit(request: Request, data: dict[str, Any] = Body(default_factory=dict)):
    """接收一篇投稿。"""
    data = data
    for key in (
        "author",
        "title",
        "body",
        "direction",
        "colophon",
        "signature",
        "font",
    ):
        if key in data and not isinstance(data[key], str):
            return JSONResponse(
                {"ok": False, "error": f"{key} 必须为文本"}, status_code=400
            )
    author = (data.get("author") or "").strip()
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    direction = (data.get("direction") or "vertical").strip()
    colophon = (data.get("colophon") or "none").strip()
    signature = (data.get("signature") or "").strip()
    font = (data.get("font") or "").strip()
    if not author:
        return JSONResponse(
            content={"ok": False, "error": "请填写你的名字"}, status_code=400
        )
    if len(author) > 80 or len(title) > 200 or len(signature) > 200:
        return JSONResponse(
            {"ok": False, "error": "名字、诗题或署名过长"}, status_code=400
        )
    if not body:
        return JSONResponse(
            content={"ok": False, "error": "正文不能为空"}, status_code=400
        )
    if len(body) > 2000:
        return JSONResponse(
            content={"ok": False, "error": "正文太长（上限 2000 字）"}, status_code=400
        )
    if direction not in ("vertical", "horizontal"):
        direction = "vertical"
    if colophon not in ("none", "author", "full", "seal"):
        colophon = "none"
    sub_id = db.add_submission(
        author=author,
        title=title,
        body=body,
        direction=direction,
        colophon=colophon,
        signature=signature,
        client_ip=(request.client.host if request.client else "") or "",
        font=font,
    )
    return {"ok": True, "id": sub_id}


@router.get("/api/my/{sub_id}", response_model=None)
def api_my_preview(request: Request, sub_id: int):
    """
    成员查看自己那篇的排版效果。

    只回传预览，不回传别人的内容，也不暴露任何管理能力 ——
    知道 id 就能看排版，但看不到机器状态、也改不了任何东西。
    """
    sub = db.get_submission(sub_id)
    if not sub:
        return JSONResponse(
            content={"ok": False, "error": "投稿不存在"}, status_code=404
        )
    try:
        fast_preview = request.query_params.get("fast") in ("1", "true", "True")
        p = printer.prepare(sub, save_gcode=False, fast_preview=fast_preview)
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": f"排版失败: {exc}"}, status_code=500
        )
    return {
        "ok": True,
        "svg": p.svg,
        "font_size_mm": round(p.font_size_mm, 2),
        "cells": p.cells,
        "missing": p.missing,
        "warnings": p.warnings,
        "pending_gfont": p.pending_gfont,
        "can_print": p.can_print,
        "requested_size": p.requested_size,
        "shrunk": p.shrunk,
        "strokes": p.strokes,
    }


@router.get("/api/admin/net", response_model=None)
def api_net(request: Request):
    """热点地址，显示在管理页上方便告诉社团成员。"""
    port = int(os.getenv("PYGMALION_PORT", CONFIG["server"]["port"]))
    return {
        "ok": True,
        "port": port,
        "urls": [f"http://{ip}:{port}" for ip in local_ips()],
    }


@router.get("/api/admin/serial-ports", response_model=None)
def api_serial_ports(request: Request):
    """列出系统串口；不打开设备、不发送串口数据。"""
    from ..core import grbl

    return {
        "ok": True,
        "configured_port": CONFIG["machine"].get("port", "auto"),
        "baud": CONFIG["machine"].get("baud", 115200),
        "ports": grbl.serial_ports(),
    }


@router.post("/api/admin/serial-port", response_model=None)
@device_guard
def api_set_serial_port(
    request: Request, data: dict[str, Any] = Body(default_factory=dict)
):
    """保存手动选择的串口，或保存 `auto` 让书写前自动识别 GRBL。"""
    data = data or {}
    port = str(data.get("port") or "").strip()
    if not port:
        return JSONResponse(
            content={"ok": False, "error": "请选择串口"}, status_code=400
        )
    if port.casefold() == "auto":
        port = "auto"
    elif not re.fullmatch("COM[1-9][0-9]*", port, flags=re.IGNORECASE):
        return JSONResponse(
            content={"ok": False, "error": "串口格式应为 COM 加数字，例如 COM8"},
            status_code=400,
        )
    else:
        port = port.upper()
    from ..core import config as config_mod

    config_mod.save_section("machine", {"port": port})
    return {"ok": True, "port": port}


@router.post("/api/admin/serial-ports/detect", response_model=None)
@device_guard
def api_detect_serial_port(
    request: Request, data: dict[str, Any] = Body(default_factory=dict)
):
    """显式扫描并以 `$I` 只读识别 GRBL；书写中绝不探测。"""
    if request.app.state.printer.current:
        return JSONResponse(
            content={
                "ok": False,
                "error": "机器正在书写，不能扫描串口；请等待任务结束",
            },
            status_code=409,
        )
    from ..core import grbl

    baud = int(CONFIG["machine"].get("baud", 115200))
    checked = grbl.probe_grbl_ports(baud)
    matches = [item for item in checked if item.get("grbl")]
    if len(matches) != 1:
        if not matches:
            return JSONResponse(
                content={
                    "ok": False,
                    "error": "未检测到可响应的 GRBL 设备",
                    "ports": checked,
                },
                status_code=404,
            )
        return JSONResponse(
            content={
                "ok": False,
                "error": "检测到多个 GRBL 设备，请手动选择",
                "ports": checked,
            },
            status_code=409,
        )
    port = str(matches[0]["device"])
    from ..core import config as config_mod

    config_mod.save_section("machine", {"port": port})
    return {
        "ok": True,
        "port": port,
        "ports": checked,
        "firmware": matches[0].get("firmware", ""),
    }


@router.get("/api/admin/submissions", response_model=None)
def api_submissions(request: Request):
    status = request.query_params.get("status") or None
    if status == "all":
        status = None
    rows = db.list_submissions(status=status)
    return {"ok": True, "items": rows, "stats": db.stats()}


@router.get("/api/admin/preview/{sub_id}", response_model=None)
def api_admin_preview(request: Request, sub_id: int):
    sub = db.get_submission(sub_id)
    if not sub:
        return JSONResponse(
            content={"ok": False, "error": "投稿不存在"}, status_code=404
        )
    try:
        fast_preview = request.query_params.get("fast") in ("1", "true", "True")
        p = printer.prepare(
            sub,
            overrides_from_request(request.query_params),
            save_gcode=False,
            fast_preview=fast_preview,
        )
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": f"排版失败: {exc}"}, status_code=500
        )
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
        "pending_gfont": p.pending_gfont,
        "can_print": p.can_print,
        "font": p.font or "默认",
        "colophon": p.poem.colophon,
    }


@router.get("/api/admin/submission/{sub_id}")
def api_submission(sub_id: int):
    item = db.get_submission(sub_id)
    if not item:
        return JSONResponse({"ok": False, "error": "投稿不存在"}, status_code=404)
    return {"ok": True, "item": item, "layout": CONFIG["layout"]}


@router.post("/api/admin/print/{sub_id}", response_model=None)
def api_print(
    request: Request, sub_id: int, data: dict[str, Any] = Body(default_factory=dict)
):
    """把一篇投稿按当前预览参数冻结后排进书写队列。"""
    data = data or {}
    overrides = overrides_from_request(data)
    try:
        job_id = request.app.state.printer.submit(sub_id, overrides or None)
    except ValueError as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=500)
    return {"ok": True, "job_id": job_id}


@router.get("/api/admin/job/{job_id}", response_model=None)
def api_job(request: Request, job_id: int):
    prog = request.app.state.printer.get(job_id)
    if not prog:
        archived = next(
            (job for job in db.all_job_records() if job["job_id"] == job_id), None
        )
        if archived:
            return {"ok": True, "job": archived}
        return JSONResponse(
            content={"ok": False, "error": "任务不存在"}, status_code=404
        )
    return {"ok": True, "job": prog.to_dict()}


@router.get("/api/admin/jobs", response_model=None)
def api_jobs(request: Request):
    cur = request.app.state.printer.current
    return {
        "ok": True,
        "current": cur.to_dict() if cur else None,
        "jobs": merge_jobs(request),
        "dry_run": request.app.state.printer.dry_run,
    }


def merge_jobs(request: Request) -> list[dict]:
    records = db.all_job_records()
    live = {job.job_id: job.to_dict() for job in request.app.state.printer.all_jobs()}
    for record in records:
        if record["job_id"] in live:
            record.update(live[record["job_id"]])
    return records


@router.post("/api/admin/cancel/{job_id}", response_model=None)
def api_cancel(
    request: Request, job_id: int, data: dict[str, Any] = Body(default_factory=dict)
):
    try:
        request.app.state.printer.cancel(job_id)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True}


@router.post("/api/admin/delete/{sub_id}", response_model=None)
def api_delete(
    request: Request, sub_id: int, data: dict[str, Any] = Body(default_factory=dict)
):
    if not db.get_submission(sub_id):
        return JSONResponse({"ok": False, "error": "投稿不存在"}, status_code=404)
    db.delete_submission(sub_id)
    return {"ok": True}


@router.get("/api/admin/machine", response_model=None)
@device_guard
def api_machine(request: Request):
    """
    查询机器状态。只读 —— 发 $$ 和 ? 不会让机器动。

    这是唯一主动碰串口的只读接口。查不到就如实报错，不假装正常。
    """
    from ..core import grbl

    m = CONFIG["machine"]
    try:
        with grbl.GrblLink(m["port"], m["baud"], timeout=1.5) as link:
            state = link.status()
            info = link.query("$I", max_lines=20)
            backup = grbl.latest_backup()
            return {
                "ok": True,
                "port": link.port,
                "configured_port": m["port"],
                "baud": m["baud"],
                "state": state,
                "firmware": info[:3],
                "backup": str(backup) if backup else None,
            }
    except Exception as exc:
        return JSONResponse(
            content={
                "ok": False,
                "port": m["port"],
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "检查：kdraw 是否已完全退出（串口独占）、USB 是否插好、COM 口号是否变了",
            },
            status_code=503,
        )


@router.post("/api/admin/jog", response_model=None)
@device_guard
def api_jog(request: Request, data: dict[str, Any] = Body(default_factory=dict)):
    """
    手动点动 —— 把笔尖对到当前工作原点。

    为什么必须有这个：机器没有限位开关也不归零，工作原点就是笔尖当前所在
    的位置。所以每次书写前都要手动把笔尖移到固定刻度区域左上角，再把那里设为
    零点。没有点动就只能靠手推小车，而手推会让坐标计数失准。

    所有动作都走"先备份再动"的闸门，且限制单次位移上限。
    """
    from ..core import grbl

    data = data or {}
    if not isinstance(data.get("action"), str):
        return JSONResponse({"ok": False, "error": "动作格式不正确"}, status_code=400)
    action = data["action"].strip()
    if action not in ("x", "y", "pen_down", "pen_up", "zero", "goto_zero"):
        return JSONResponse({"ok": False, "error": "不认识的动作"}, status_code=400)
    if action in ("x", "y"):
        try:
            distance = float(data.get("dist", 0))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "位移不是数字"}, status_code=400)
        if not math.isfinite(distance) or not 0.01 <= abs(distance) <= 60:
            return JSONResponse(
                {"ok": False, "error": "单次点动应在 0.01–60 mm 内"}, status_code=400
            )
    m = CONFIG["machine"]
    MAX_JOG = 60.0
    try:
        with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
            grbl.ensure_backup_today(link, reason="手动点动前自动备份")
            if action in ("x", "y"):
                try:
                    dist = float(data.get("dist", 0))
                except (TypeError, ValueError):
                    return JSONResponse(
                        content={"ok": False, "error": "位移不是数字"}, status_code=400
                    )
                if abs(dist) > MAX_JOG:
                    return JSONResponse(
                        content={
                            "ok": False,
                            "error": f"单次点动上限 {MAX_JOG:g}mm，收到 {abs(dist):g}mm",
                        },
                        status_code=400,
                    )
                if abs(dist) < 0.01:
                    return JSONResponse(
                        content={"ok": False, "error": "位移为 0"}, status_code=400
                    )
                link.run_program(["G91", f"G1 {action.upper()}{dist:g} F500", "G90"])
            elif action == "pen_down":
                link.run_program([f"G1 Z{m['z_on']} F{m['z_speed']}"])
            elif action == "pen_up":
                link.run_program([f"G0 Z{m['z_off']}"])
            elif action == "zero":
                link.send("G92 X0 Y0")
            elif action == "goto_zero":
                link.run_program(["G90", "G0 X0 Y0"])
            else:
                return JSONResponse(
                    content={"ok": False, "error": f"不认识的动作: {action}"},
                    status_code=400,
                )
            st = link.wait_idle()
            return {"ok": True, "action": action, "state": st}
    except Exception as exc:
        return JSONResponse(
            content={
                "ok": False,
                "port": m["port"],
                "error": f"{type(exc).__name__}: {exc}",
            },
            status_code=503,
        )


@router.get("/api/admin/backup", response_model=None)
@device_guard
def api_backup(request: Request):
    """
    备份机器设置。只读操作（发 $$ / $I / $G / $#），不会动机器。

    这也是"改前必须先备份"那条硬约束的入口 —— 任何时候都可以先点这个，
    拿到一份可以逐行发回去的完整快照。
    """
    from ..core import grbl

    m = CONFIG["machine"]
    try:
        with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
            path = grbl.backup_settings(link, reason="管理页手动备份")
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=503,
        )


@router.post("/api/admin/settings", response_model=None)
@device_guard
def api_write_settings(
    request: Request, data: dict[str, Any] = Body(default_factory=dict)
):
    """默认离线预览；明确确认后备份并写入真实控制板。"""
    from ..core import grbl

    changes = data.get("changes")
    if not isinstance(changes, dict) or not changes:
        return JSONResponse({"ok": False, "error": "没有要修改的参数"}, status_code=400)
    if "confirm" in data and not isinstance(data["confirm"], bool):
        return JSONResponse(
            {"ok": False, "error": "确认字段必须为布尔值"}, status_code=400
        )
    try:
        commands = grbl.settings_commands(changes)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if data.get("confirm") is not True:
        return {
            "ok": True,
            "dry_run": True,
            "will_send": commands,
            "note": "预览未写入控制板",
        }
    machine = CONFIG["machine"]
    try:
        with grbl.GrblLink(machine["port"], machine["baud"], timeout=2) as link:
            path = link.backup(reason=f"修改参数 {list(changes)}")
            result = link.write_settings(changes, dry_run=False)
        return {"ok": True, "dry_run": False, "backup": str(path), "result": result}
    except Exception as exc:
        logger.exception("控制板参数写入失败")
        return JSONResponse(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=503
        )
