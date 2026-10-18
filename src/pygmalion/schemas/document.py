"""纸面数据模型与纯数据校验。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
