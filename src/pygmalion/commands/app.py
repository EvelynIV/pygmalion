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
    ] = "0.0.0.0",
    port: Annotated[
        int, typer.Option(envvar="PYGMALION_PORT", min=1, max=65535, help="监听端口")
    ] = 8080,
    dry_run: Annotated[
        bool,
        typer.Option(
            envvar="PYGMALION_DRY_RUN", help="演练书写任务；手动控制仍操作真实设备"
        ),
    ] = False,
    fonts_dir: Annotated[
        Path | None,
        typer.Option(
            envvar="PYGMALION_FONTS_DIR",
            help="字体数据集父目录；相对路径基于运行数据根目录",
        ),
    ] = None,
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
    if fonts_dir is not None:
        os.environ["PYGMALION_FONTS_DIR"] = str(fonts_dir)
    logger.info("工作区：http://%s:%s/admin", host, port)
    uvicorn.run("pygmalion.api.app:app", host=host, port=port)


@app.command()
def build_font(
    base: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="基础 train.parquet")
    ],
    output: Annotated[Path, typer.Option(help="新字体目录；必须尚不存在")],
):
    """离线扩充数字、字母、标点并生成 OpenCV 字样图。"""
    from ..fonts.build import build_font as generate

    try:
        report = generate(base, output)
    except (ValueError, OSError) as error:
        logger.error("字体生成失败：%s", error)
        raise typer.Exit(1) from error
    logger.info(
        "字体已生成：%s，原有 %s 字，新增 %s 字，共 %s 字",
        output,
        report["base_chars"],
        report["added_chars"],
        report["total_chars"],
    )


if __name__ == "__main__":
    app()
