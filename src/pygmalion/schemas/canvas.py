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
    font: str = Field(default="", max_length=240)
    font_size_mm: float = Field(default=8, ge=4, le=40)
    char_gap_mm: float = Field(default=0, ge=0, le=20)
    line_gap_mm: float = Field(default=3, ge=0, le=30)


class TextBox(Element):
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


class Table(Element):
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


CanvasElement = Annotated[TextBox | Table, Field(discriminator="type")]


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
        text_length = sum(
            len(e.text) if isinstance(e, TextBox) else sum(len(c.text) for c in e.cells)
            for e in self.elements
        )
        if text_length > 20000:
            raise ValueError("整页文字不能超过 20000 字符")
        return self

    @property
    def body(self):
        return "\n".join(
            e.text if isinstance(e, TextBox) else "\n".join(c.text for c in e.cells)
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
