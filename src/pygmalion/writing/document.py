"""通用单页正文排版；文档名称不参与轨迹，保留换行与缩进。"""

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import CONFIG, ROOT
from ..fonts import registry
from ..machine import gcode
from .layout import char_width_units
from .models import PageConfig, PlacedGlyph, Poem
from .prepare import Prepared, _glyph_source, build_configs
from .preview import strokes_to_svg
from .render import render_glyphs
from .smooth import smooth_strokes


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="未命名文档", max_length=120)
    body: str = Field(default="", max_length=20000)
    font: str = Field(default="", max_length=120)
    direction: Literal["horizontal", "vertical"] = "horizontal"
    paper: Literal["A4", "A5"] = "A4"
    font_size_mm: float = Field(default=8, ge=4, le=40)
    char_gap_mm: float = Field(default=0, ge=0, le=20)
    line_gap_mm: float = Field(default=3, ge=0, le=30)
    margin_mm: float = Field(default=20, ge=0, le=60)
    align: Literal["left", "center", "right"] = "left"

    @model_validator(mode="after")
    def valid_page(self):
        width, height = (210, 297) if self.paper == "A4" else (148, 210)
        if 2 * self.margin_mm + self.font_size_mm > min(width, height):
            raise ValueError("页边距过大，纸面没有足够的书写空间")
        return self

    def page_config(self) -> PageConfig:
        width, height = (210, 297) if self.paper == "A4" else (148, 210)
        return PageConfig(width, height, *([self.margin_mm] * 4))


def layout_document(doc: DocumentRequest) -> tuple[list[PlacedGlyph], bool]:
    page = doc.page_config()
    vertical = doc.direction == "vertical"
    limit = page.usable_h if vertical else page.usable_w
    rows: list[list[tuple[str, float]]] = []
    for line in doc.body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        row: list[tuple[str, float]] = []
        used = 0.0
        # tab 占四个空格；不 trim，保留空行、行首/尾空白。
        for ch in line.expandtabs(4):
            width = doc.font_size_mm * (1 if vertical else char_width_units(ch))
            next_width = width + (doc.char_gap_mm if row else 0)
            if row and used + next_width > limit + 1e-6:
                rows.append(row)
                row, used, next_width = [], 0.0, width
            row.append((ch, width))
            used += next_width
        rows.append(row)
    block = len(rows) * doc.font_size_mm + max(0, len(rows) - 1) * doc.line_gap_mm
    overflow = block > (page.usable_w if vertical else page.usable_h) + 1e-6
    placed = []
    for index, row in enumerate(rows):
        length = sum(w for _, w in row) + max(0, len(row) - 1) * doc.char_gap_mm
        overflow |= length > limit + 1e-6
        offset = (
            0
            if doc.align == "left"
            else (limit - length) / (2 if doc.align == "center" else 1)
        )
        cursor = (page.margin_top_mm if vertical else page.margin_left_mm) + offset
        cross = index * (doc.font_size_mm + doc.line_gap_mm)
        for ch, width in row:
            if not ch.isspace():
                x = (
                    page.width_mm - page.margin_right_mm - cross - doc.font_size_mm / 2
                    if vertical
                    else cursor + width / 2
                )
                y = (
                    cursor + doc.font_size_mm / 2
                    if vertical
                    else page.margin_top_mm + cross + doc.font_size_mm / 2
                )
                placed.append(
                    PlacedGlyph(
                        ch,
                        x,
                        y,
                        doc.font_size_mm,
                        scale_x=1 if vertical else width / doc.font_size_mm,
                    )
                )
            cursor += width + doc.char_gap_mm
    return placed, overflow


def prepare_document(
    doc: DocumentRequest, *, fast: bool = False
) -> tuple[Prepared, bool]:
    if doc.font and not any(
        f.id == doc.font and f.available for f in registry.discover(ROOT)
    ):
        raise ValueError("所选字库不可用，请重新选择")
    cfg = build_configs({"font": doc.font, "handwriting": False, "seed": 0})
    page = doc.page_config()
    placed, overflow = layout_document(doc)
    source = _glyph_source(doc.font or None, gfont_blocking=not fast)
    strokes, missing = render_glyphs(
        placed, source, replace(cfg["jitter"], enabled=False)
    )
    quality = cfg["quality"]
    if quality["enabled"]:
        strokes = smooth_strokes(
            strokes, **{k: v for k, v in quality.items() if k != "enabled"}
        )
    svg = strokes_to_svg(strokes, page, color="#252a2a", bg="#f8f7f2")
    # 页边距只存在于预览 SVG；不进入 G-code。
    guide = f'<rect x="{doc.margin_mm}" y="{doc.margin_mm}" width="{page.usable_w}" height="{page.usable_h}" fill="none" stroke="#252a2a" stroke-opacity="0.16" stroke-width="0.5" stroke-dasharray="1.5 2"/>'
    svg = svg.replace("</svg>", guide + "</svg>")
    import copy

    prepared = Prepared(
        submission_id=0,
        poem=Poem(body=doc.body, direction=doc.direction),
        gcode=gcode.strokes_to_gcode(strokes, page, cfg["machine"]),
        svg=svg,
        strokes=len(strokes),
        cells=len(placed),
        font_size_mm=doc.font_size_mm,
        requested_size=doc.font_size_mm,
        missing=missing,
        warnings=source.warnings,
        font=doc.font or registry.default_id(ROOT),
        pending_gfont=source.pending_gfont,
        machine_snapshot=copy.deepcopy(CONFIG["machine"]),
        blocked=overflow or bool(missing),
    )
    return prepared, overflow
