from __future__ import annotations

import logging

from ..jobs import repository as jobs
from .errors import ServiceError
from .submissions import overrides_from_request

logger = logging.getLogger(__name__)


def submit(context, sub_id, data):
    """把一篇投稿按当前预览参数冻结后排进书写队列。"""
    data = data or {}
    overrides = overrides_from_request(data)
    try:
        job_id = context.printer.submit(sub_id, overrides or None)
    except ValueError as exc:
        raise ServiceError("invalid", {"ok": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
        raise ServiceError("internal", {"ok": False, "error": str(exc)})
    return {"ok": True, "job_id": job_id}


def get_job(context, job_id):
    prog = context.printer.get(job_id)
    if not prog:
        archived = next(
            (job for job in jobs.all_job_records() if job["job_id"] == job_id), None
        )
        if archived:
            return {"ok": True, "job": archived}
        raise ServiceError("not_found", {"ok": False, "error": "任务不存在"})
    return {"ok": True, "job": prog.to_dict()}


def list_jobs(context):
    cur = context.printer.current
    return {
        "ok": True,
        "current": cur.to_dict() if cur else None,
        "jobs": merge_jobs(context),
        "dry_run": context.printer.dry_run,
    }


def merge_jobs(context) -> list[dict]:
    records = jobs.all_job_records()
    live = {job.job_id: job.to_dict() for job in context.printer.all_jobs()}
    for record in records:
        if record["job_id"] in live:
            record.update(live[record["job_id"]])
    return records


def cancel(context, job_id, data):
    try:
        context.printer.cancel(job_id)
    except ValueError as exc:
        raise ServiceError("busy", {"ok": False, "error": str(exc)})
    return {"ok": True}
