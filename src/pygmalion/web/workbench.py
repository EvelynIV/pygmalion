"""V1 单页工作台入口；复用已有任务执行器，不提供队列操作界面。"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import CONFIG
from ..submissions import repository as submissions
from ..writing.document import DocumentRequest, prepare_document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workbench")


@router.post("/preview")
def preview_document(data: DocumentRequest, fast: bool = True):
    try:
        p, overflow = prepare_document(data, fast=fast)
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
        }
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("正文预览失败")
        return JSONResponse({"error": "预览生成失败，请重试"}, status_code=500)


@router.post("/write")
def write_document(request: Request, data: DocumentRequest):
    service = request.app.state.printer
    if not service.workbench_lock.acquire(blocking=False):
        return JSONResponse({"error": "另一项书写请求正在处理"}, status_code=409)
    try:
        if (
            any(j.status in ("queued", "printing") for j in service.all_jobs())
            or service.manual_control_lock.locked()
        ):
            return JSONResponse(
                {"error": "请等待当前任务或点动操作结束"}, status_code=409
            )
        if not service.device_lock.acquire(blocking=False):
            return JSONResponse({"error": "设备正在处理另一项操作"}, status_code=409)
        try:
            p, overflow = prepare_document(data)
            if overflow:
                raise ValueError("正文超出单页范围，请调整排版后再书写")
            if p.missing:
                raise ValueError("缺字：" + "、".join(p.missing))
            if not p.can_print:
                raise ValueError("字形尚未加载完成，或没有可绘制的正文")
            if not service.dry_run and CONFIG["page"].get("fixed_grid_origin", False):
                page = data.page_config()
                config = CONFIG["page"]
                x, y = (
                    float(config.get("paper_offset_x_mm", 0)),
                    float(config.get("paper_offset_y_mm", 0)),
                )
                if (
                    x < config.get("paper_min_x_mm", -100)
                    or y < config.get("paper_min_y_mm", -50)
                    or x + page.width_mm > config.get("grid_width_mm", 190)
                    or y + page.height_mm > config.get("grid_height_mm", 280)
                ):
                    raise ValueError(
                        "纸张尺寸超出当前机器范围，请检查纸张规格与设备配置"
                    )
            # 兼容旧任务存储，内部记录仅用于任务关联；名称不交给排版器。
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
            service.device_lock.release()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        service.workbench_lock.release()
