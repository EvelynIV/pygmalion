"""FastAPI 托管 API 和 React 构建产物。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import config, storage
from ..jobs import repository as jobs, service as printer
from .api import router
from .machine_socket import router as machine_router

logger = logging.getLogger(__name__)
WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(dist_dir: Path = WEB_DIST_DIR, dry_run: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not (dist_dir / "index.html").is_file():
            raise RuntimeError("前端构建不存在，请先在 src/web 执行 pnpm run build")
        config.ensure_dirs()
        storage.init_db()
        jobs.interrupt_unfinished_jobs()
        service = printer.PrinterService(
            dry_run=dry_run
            if dry_run is not None
            else os.getenv("PYGMALION_DRY_RUN", "0") == "1"
        )
        application.state.printer = service
        service.start()
        logger.info("书写工作区启动，任务演练=%s", service.dry_run)
        try:
            yield
        finally:
            service.stop()

    application = FastAPI(title="Pygmalion · 写字机控制台", lifespan=lifespan)
    application.include_router(router)
    application.include_router(machine_router)

    @application.get("/api/health")
    def health():
        return {"status": "ok"}

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
