from __future__ import annotations

from dataclasses import dataclass

ROLE_TITLE = "title"
ROLE_BODY = "body"
ROLE_AUTHOR = "author"
ROLE_COLOPHON = "colophon"
ROLE_PUNCT = "punct"
ROLE_SEAL = "seal"


@dataclass
class PlacedGlyph:
    """一个已经摆好位置的字。cx/cy 是字格中心（页面坐标，毫米）。"""

    char: str
    cx: float
    cy: float
    size: float  # 该字的字格边长（毫米）
    role: str = ROLE_BODY
    rotate_deg: float = 0.0  # 少量字需要整体旋转（如印章内的字）
    scale_x: float = 1.0  # 通用文本半角字符的横向字格宽度


@dataclass
class Stroke:
    """一条待绘制的笔画，页面坐标下的折线。"""

    points: list[tuple[float, float]]
    role: str = ROLE_BODY
    width_mm: float = 0.35


@dataclass
class Poem:
    """一首待排版的诗。"""

    body: str
    title: str = ""
    author: str = ""
    note: str = ""
    direction: str = "vertical"  # vertical | horizontal
    colophon: str = "none"  # none | author | full | seal
    dedication: str = ""  # 落款里"XX 书"的那个 XX，留空则用 author

    def body_lines(self) -> list[str]:
        """正文按行拆开，丢掉空行。"""
        return [ln.strip() for ln in self.body.splitlines() if ln.strip()]


@dataclass
class PageConfig:
    width_mm: float = 210.0
    height_mm: float = 297.0
    margin_top_mm: float = 15.0
    margin_right_mm: float = 15.0
    margin_bottom_mm: float = 15.0
    margin_left_mm: float = 15.0

    @property
    def usable_w(self) -> float:
        return self.width_mm - self.margin_left_mm - self.margin_right_mm

    @property
    def usable_h(self) -> float:
        return self.height_mm - self.margin_top_mm - self.margin_bottom_mm


@dataclass
class LayoutConfig:
    font_size_mm: float = 12.0
    char_gap_mm: float = 1.0
    line_gap_mm: float = 5.0
    title_scale: float = 1.25
    author_scale: float = 0.6
    colophon_scale: float = 0.55
    align: str = "center"  # center | top | left
    punct_own_cell: bool = True  # 标点是否独立占一格
    seal_size_scale: float = 1.3  # 印章边长相对正文字号的倍数
