from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request

from ...servicer import jobs as service
from ..dependencies import get_context

router = APIRouter()


@router.post("/api/admin/print/{sub_id}", response_model=None)
def submit(
    request: Request,
    sub_id: int,
    data: Annotated[dict[str, Any], Body(default_factory=dict)],
):
    return service.submit(get_context(request), sub_id, data)


@router.get("/api/admin/job/{job_id}", response_model=None)
def get_job(request: Request, job_id: int):
    return service.get_job(get_context(request), job_id)


@router.get("/api/admin/jobs", response_model=None)
def list_jobs(request: Request):
    return service.list_jobs(get_context(request))


@router.post("/api/admin/cancel/{job_id}", response_model=None)
def cancel(
    request: Request,
    job_id: int,
    data: Annotated[dict[str, Any], Body(default_factory=dict)],
):
    return service.cancel(get_context(request), job_id, data)
