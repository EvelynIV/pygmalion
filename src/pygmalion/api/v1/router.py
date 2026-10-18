"""v1 是 Python 包版本；保留现有 /api URL。"""

from fastapi import APIRouter

from . import jobs, machine, machine_socket, settings, submissions, system, workbench

router = APIRouter()
for routes in (jobs, machine, machine_socket, settings, submissions, system, workbench):
    router.include_router(routes.router)
