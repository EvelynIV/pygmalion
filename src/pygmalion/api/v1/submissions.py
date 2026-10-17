from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request

from ...servicer import submissions as service

router = APIRouter()


@router.post("/api/submit", response_model=None)
def submit(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.submit(data, (request.client.host if request.client else "") or "")


@router.get("/api/my/{sub_id}", response_model=None)
def my_preview(request: Request, sub_id: int):
    return service.my_preview(sub_id, request.query_params)


@router.get("/api/admin/submissions", response_model=None)
def list_submissions(request: Request):
    return service.list_submissions(request.query_params)


@router.get("/api/admin/preview/{sub_id}", response_model=None)
def admin_preview(request: Request, sub_id: int):
    return service.admin_preview(sub_id, request.query_params)


@router.get("/api/admin/submission/{sub_id}")
def get_submission(request: Request, sub_id: int):
    return service.get_submission(sub_id)


@router.post("/api/admin/delete/{sub_id}", response_model=None)
def delete_submission(
    request: Request,
    sub_id: int,
    data: Annotated[dict[str, Any], Body(default_factory=dict)],
):
    return service.delete_submission(sub_id, data)
