from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request

from ...servicer import machine as service
from ..dependencies import get_context

router = APIRouter()


@router.post("/api/admin/estop", response_model=None)
def estop(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.estop(get_context(request), data)


@router.post("/api/admin/recover", response_model=None)
def recover(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.recover(get_context(request), data)


@router.get("/api/admin/serial-ports", response_model=None)
def serial_ports(request: Request):
    return service.serial_ports(get_context(request))


@router.post("/api/admin/serial-port", response_model=None)
def set_serial_port(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.set_serial_port(get_context(request), data)


@router.post("/api/admin/serial-ports/detect", response_model=None)
def detect_serial_port(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.detect_serial_port(get_context(request), data)


@router.get("/api/admin/machine", response_model=None)
def inspect(request: Request):
    return service.inspect(get_context(request))


@router.post("/api/admin/jog", response_model=None)
def jog(request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]):
    return service.jog(get_context(request), data)


@router.get("/api/admin/backup", response_model=None)
def backup(request: Request):
    return service.backup(get_context(request))


@router.post("/api/admin/settings", response_model=None)
def write_settings(
    request: Request, data: Annotated[dict[str, Any], Body(default_factory=dict)]
):
    return service.write_settings(get_context(request), data)
