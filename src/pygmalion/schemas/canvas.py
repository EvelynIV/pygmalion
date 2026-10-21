"""纸面数据模型与纯数据校验。"""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Element(Model):
    id: str = Field(min_length=1, max_length=120)
    x_mm: float = 20
    y_mm: float = 20
    width_mm: float = Field(default=100, gt=0, le=297)
    height_mm: float = Field(default=50, gt=0, le=297)


class TextElement(Element):
    font: str = Field(default="", max_length=240)
    font_size_mm: float = Field(default=8, ge=4, le=40)
    char_gap_mm: float = Field(default=0, ge=0, le=20)
    line_gap_mm: float = Field(default=3, ge=0, le=30)


class TextBox(TextElement):
    type: Literal["text"] = "text"
    text: str = Field(default="", max_length=20000)
    align: Literal["left", "center", "right"] = "left"


class TableCell(Model):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    text: str = Field(default="", max_length=20000)
    align: Literal["left", "center", "right"] = "left"
    valign: Literal["top", "center", "bottom"] = "center"


class Table(TextElement):
    type: Literal["table"] = "table"
    column_widths: list[float] = Field(min_length=1, max_length=40)
    row_heights: list[float] = Field(min_length=1, max_length=80)
    cells: list[TableCell] = Field(max_length=3200)
    padding_mm: float = Field(default=1.5, ge=0, le=10)
    draw_borders: bool = True

    @model_validator(mode="after")
    def valid_grid(self):
        if any(
            not math.isfinite(v) or v <= 0
            for v in self.column_widths + self.row_heights
        ):
            raise ValueError("行高和列宽必须为正有限数值")
        if (
            abs(sum(self.column_widths) - self.width_mm) > 1e-05
            or abs(sum(self.row_heights) - self.height_mm) > 1e-05
        ):
            raise ValueError("行高、列宽之和必须与表格外框一致")
        occupied = set()
        for cell in self.cells:
            if cell.row + cell.rowspan > len(
                self.row_heights
            ) or cell.col + cell.colspan > len(self.column_widths):
                raise ValueError("合并单元格超出表格范围")
            for r in range(cell.row, cell.row + cell.rowspan):
                for c in range(cell.col, cell.col + cell.colspan):
                    if (r, c) in occupied:
                        raise ValueError("单元格重叠")
                    occupied.add((r, c))
        if len(occupied) != len(self.column_widths) * len(self.row_heights):
            raise ValueError("表格必须完整覆盖所有单元格")
        return self


class Vector(Element):
    type: Literal["vector"] = "vector"
    name: str = Field(default="矢量图", max_length=120)
    paths: list[str] = Field(min_length=1, max_length=1000)
    lock_aspect: bool = True
    fill_rules: list[Literal["nonzero", "evenodd"] | None] = Field(
        default_factory=list, max_length=1000
    )
    draw_mode: Literal["outline", "solid"] = "outline"
    pen_width_mm: float = Field(default=0.3, ge=0.05, le=3)
    fill_spacing_mm: float = Field(default=0.24, ge=0.05, le=3)
    fill_angle_deg: float = Field(default=0, ge=-180, le=180)

    @model_validator(mode="after")
    def valid_paths(self):
        from .vector import MAX_SEGMENTS, checked_path

        if self.fill_rules and len(self.fill_rules) != len(self.paths):
            raise ValueError("填充规则必须与路径一一对应")
        if self.draw_mode == "solid" and not any(self.fill_rules):
            raise ValueError("缺少填充区域，请重新导入带填充信息的 SVG")
        if sum(map(len, self.paths)) > 1000000:
            raise ValueError("矢量路径数据过大")
        count = 0
        for data in self.paths:
            path = checked_path(data)
            count += len(path)
            box = path.bbox()
            if box and (min(box) < -1e-6 or max(box) > 1 + 1e-6):
                raise ValueError("矢量路径必须位于归一化外框内")
        if count > MAX_SEGMENTS:
            raise ValueError("矢量路径过于复杂")
        return self


class SvgImportRequest(Model):
    source: str = Field(max_length=1000000)
    name: str = Field(default="矢量图", max_length=120)


CanvasElement = Annotated[TextBox | Table | Vector, Field(discriminator="type")]


class CanvasDocument(Model):
    version: Literal[2] = 2
    name: str = Field(default="未命名文档", max_length=120)
    paper: Literal["A4", "A5"] = "A4"
    margin_mm: float = Field(default=20, ge=0, le=60)
    elements: list[CanvasElement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_document(self):
        if len({e.id for e in self.elements}) != len(self.elements):
            raise ValueError("元素 ID 不能重复")
        if (
            sum(sum(map(len, e.paths)) for e in self.elements if isinstance(e, Vector))
            > 1000000
        ):
            raise ValueError("整页矢量路径数据不能超过 1 MB")
        text_length = sum(
            len(e.text)
            if isinstance(e, TextBox)
            else sum(len(c.text) for c in e.cells)
            if isinstance(e, Table)
            else 0
            for e in self.elements
        )
        if text_length > 20000:
            raise ValueError("整页文字不能超过 20000 字符")
        return self

    @property
    def body(self):
        return "\n".join(
            e.text
            if isinstance(e, TextBox)
            else "\n".join(c.text for c in e.cells)
            if isinstance(e, Table)
            else ""
            for e in self.elements
        )

    @property
    def direction(self):
        return "horizontal"

    @property
    def font(self):
        return ""


class FitRequest(Model):
    document: CanvasDocument
    element_id: str
