"""Pygmalion 命令行入口。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
app = typer.Typer(help="Pygmalion · 诗词自动书写", no_args_is_help=True)


@app.callback()
def entry():
    """Pygmalion · 诗词自动书写工作区。"""


@app.command()
def serve(
    host: Annotated[
        str, typer.Option(envvar="PYGMALION_HOST", help="监听地址")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option(envvar="PYGMALION_PORT", min=1, max=65535, help="监听端口")
    ] = 8080,
    dry_run: Annotated[
        bool,
        typer.Option(
            envvar="PYGMALION_DRY_RUN", help="演练书写任务；手动控制仍操作真实设备"
        ),
    ] = False,
    data_home: Annotated[
        Path | None,
        typer.Option(envvar="PYGMALION_HOME", help="配置、字库和运行数据根目录"),
    ] = None,
):
    """启动 Web 工作区。"""
    import uvicorn

    os.environ["PYGMALION_DRY_RUN"] = "1" if dry_run else "0"
    os.environ["PYGMALION_PORT"] = str(port)
    if data_home is not None:
        os.environ["PYGMALION_HOME"] = str(data_home.resolve())
    logger.info("工作区：http://%s:%s/admin", host, port)
    uvicorn.run("pygmalion.web.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
