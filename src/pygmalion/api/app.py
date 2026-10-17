"""FastAPI 托管 API 和 React 构建产物。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from ..servicer.context import ServiceContext
from ..servicer.errors import ServiceError
from .v1.router import router

logger = logging.getLogger(__name__)
WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(dist_dir: Path = WEB_DIST_DIR, dry_run: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not (dist_dir / "index.html").is_file():
            raise RuntimeError("前端构建不存在，请先在 src/web 执行 pnpm run build")
        context = ServiceContext(
            dry_run=dry_run
            if dry_run is not None
            else os.getenv("PYGMALION_DRY_RUN", "0") == "1"
        )
        application.state.services = context
        try:
            context.start()
            logger.info("书写工作区启动，任务演练=%s", context.printer.dry_run)
            yield
        finally:
            context.stop()

    application = FastAPI(title="Pygmalion · 写字机控制台", lifespan=lifespan)
    application.include_router(router)

    @application.exception_handler(ServiceError)
    async def service_error(request, exc: ServiceError):
        codes = {
            "invalid": 400,
            "not_found": 404,
            "busy": 409,
            "internal": 500,
            "unavailable": 503,
        }
        return JSONResponse(exc.payload, status_code=codes[exc.kind])

    @application.api_route(
        "/api",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    @application.api_route(
        "/api/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def api_not_found(path: str = ""):
        raise HTTPException(status_code=404, detail=f"API route not found: /api/{path}")

    application.frontend(
        "/", directory=dist_dir, fallback="index.html", check_dir=False
    )
    return application


app = create_app()
