from __future__ import annotations

from fastapi import APIRouter, Request

from ...servicer import system as service

router = APIRouter()


@router.get("/api/setup", response_model=None)
def setup(request: Request):
    return service.setup()


@router.get("/api/fonts", response_model=None)
def list_fonts(request: Request):
    return service.list_fonts()


@router.get("/api/admin/net", response_model=None)
def network(request: Request):
    return service.network()


@router.get("/api/health")
def health():
    return service.health()
