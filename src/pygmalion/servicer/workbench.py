from __future__ import annotations

import logging

from ..config import CONFIG
from ..schemas.canvas import CanvasDocument, FitRequest, Table
from ..schemas.document import DocumentRequest
from ..submissions import repository as submissions
from ..writing.canvas import fit_table, prepare_canvas
from ..writing.document import prepare_document
from ..writing.page import page_config
from .errors import ServiceError

logger = logging.getLogger(__name__)


def preview_document(data: CanvasDocument | DocumentRequest, fast: bool = True):
    try:
        p, overflow = (
            prepare_canvas(data)
            if isinstance(data, CanvasDocument)
            else prepare_document(data, fast=fast)
        )
        return {
            "svg": p.svg,
            "font_size_mm": p.font_size_mm,
            "requested_size": p.requested_size,
            "shrunk": False,
            "can_print": p.can_print,
            "overflow": overflow,
            "cells": p.cells,
            "strokes": p.strokes,
            "missing": p.missing,
            "warnings": p.warnings,
            "font": p.font,
            "layout": p.layout,
            "diagnostics": p.diagnostics,
        }
    except ValueError as exc:
        raise ServiceError("invalid", {"error": str(exc)})
    except Exception:
        logger.exception("纸面预览失败")
        raise ServiceError("internal", {"error": "预览生成失败，请重试"})


def write_document(context, data: CanvasDocument | DocumentRequest):
    service = context.printer
    if not service.workbench_lock.acquire(blocking=False):
        raise ServiceError("busy", {"error": "另一项书写请求正在处理"})
    try:
        if (
            any(j.status in ("queued", "printing") for j in service.all_jobs())
            or context.machine.manual_control_lock.locked()
        ):
            raise ServiceError("busy", {"error": "请等待当前任务或点动操作结束"})
        if not context.machine.device_lock.acquire(blocking=False):
            raise ServiceError("busy", {"error": "设备正在处理另一项操作"})
        try:
            p, overflow = (
                prepare_canvas(data)
                if isinstance(data, CanvasDocument)
                else prepare_document(data)
            )
            if overflow:
                raise ValueError("内容或元素超出可写范围，请调整排版后再书写")
            if p.missing:
                raise ValueError("缺字：" + "、".join(p.missing))
            if not p.can_print:
                raise ValueError("没有可绘制的内容")
            if not service.dry_run and CONFIG["page"].get("fixed_grid_origin", False):
                page = page_config(data)
                config = CONFIG["page"]
                x, y = (
                    float(config.get("paper_offset_x_mm", 0)),
                    float(config.get("paper_offset_y_mm", 0)),
                )
                if (
                    x < config.get("paper_min_x_mm", -100)
                    or y < config.get("paper_min_y_mm", -50)
                    or x + page.width_mm > config.get("grid_width_mm", 190)
                    or (y + page.height_mm > config.get("grid_height_mm", 280))
                ):
                    raise ValueError(
                        "纸张尺寸超出当前机器范围，请检查纸张规格与设备配置"
                    )
            sub_id = submissions.add_submission(
                author="",
                title=data.name,
                body=data.body,
                direction=data.direction,
                font=data.font,
            )
            sub = submissions.get_submission(sub_id)
            job_id = service.enqueue_prepared(sub, data.model_dump(), p)
            return {"job_id": job_id, "dry_run": service.dry_run}
        finally:
            context.machine.device_lock.release()
    except ValueError as exc:
        raise ServiceError("invalid", {"error": str(exc)})
    finally:
        service.workbench_lock.release()


def fit_document_table(data: FitRequest):
    table = next(
        (
            e
            for e in data.document.elements
            if e.id == data.element_id and isinstance(e, Table)
        ),
        None,
    )
    if table is None:
        raise ServiceError("invalid", {"error": "未找到表格"})
    try:
        fitted, diagnostics = fit_table(table)
        if not diagnostics:
            document = data.document.model_copy(update={"elements": [fitted]})
            prepared, _ = prepare_canvas(document)
            diagnostics = [d for d in prepared.diagnostics if d["code"] == "overflow"]
        return {
            "table": (table if diagnostics else fitted).model_dump(),
            "diagnostics": diagnostics,
            "success": not diagnostics,
        }
    except ValueError as exc:
        raise ServiceError("invalid", {"error": str(exc)})


def import_vector(data):
    from ..drawing.svg import import_svg

    try:
        return {"name": data.name, **import_svg(data.source)}
    except ValueError as exc:
        raise ServiceError("invalid", {"error": str(exc)})
