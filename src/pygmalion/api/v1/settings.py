from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request

from ...servicer import settings as service

router = APIRouter()


@router.get("/api/admin/grid-calibration", response_model=None)
def get_grid_calibration(request: Request):
    return service.get_grid_calibration()


@router.post("/api/admin/grid-calibration", response_model=None)
def set_grid_calibration(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.set_grid_calibration(data)


@router.get("/api/admin/config", response_model=None)
def get_config(request: Request):
    return service.get_config()


@router.post("/api/admin/config", response_model=None)
def set_config(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.set_config(data)
