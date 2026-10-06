"""
诗词自动排版引擎。

坐标约定（本模块内部统一用"页面坐标"）
--------------------------------------
  原点在纸张**左上角**，x 向右、y 向下，单位毫米。
  这与 kdraw 的 AxesData.json 里 pos="LeftUp" 一致。

  注意：这只描述纸面上的位置，与机器坐标是两回事。机器那边还有 revxy
  轴对调和原点的换算，那部分被隔离在 gcode.py 的 to_machine() 里，
  一旦标定好就不用再动。本模块只负责"字该出现在纸的哪个位置"。

排版模型
--------
每个字占一个"字格"（cell），字格是边长 font_size 的正方形，字画在格心。
字格之间留 char_gap 的间隙。这样中文（全角）和西文（半角）可以用同一套
逻辑：全角占 1 格，半角占 0.5 格。

两种方向：
  竖排右起 (vertical)   传统诗词写法，列从右向左排，列内从上到下
  横排     (horizontal) 现代写法，行从上到下排，行内从左到右
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import math
from datetime import datetime

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

# 字在版面上的角色，渲染时可以用不同的字号/字体
from .models import (
    PlacedGlyph,
    Poem,
    PageConfig,
    LayoutConfig,
    ROLE_BODY,
    ROLE_PUNCT,
    ROLE_SEAL,
)

# --------------------------------------------------------------------------
# 字符宽度
# --------------------------------------------------------------------------

# 需要当作全角处理的标点（中文标点在中文字体里是全角）
_FULLWIDTH_PUNCT = set("，。、；：？！“”‘’（）《》〈〉【】—…·「」『』")


def char_width_units(ch: str) -> float:
    """
    一个字符占几个字格。全角（汉字、中文标点）返回 1.0，半角返回 0.5。

    用 unicodedata 的 East Asian Width 判断比手写范围表可靠，
    但标点要单独处理 —— 它们在 Unicode 里往往是 Ambiguous，
    在中文语境下应当按全角算。
    """
    if ch in _FULLWIDTH_PUNCT:
        return 1.0
    if ch.isspace():
        return 0.5

    import unicodedata

    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    return 0.5


def visual_width(text: str) -> float:
    """整串文本占几个字格。"""
    return sum(char_width_units(c) for c in text)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _is_punct(ch: str) -> bool:
    return ch in _FULLWIDTH_PUNCT or ch in ",.;:!?\"'"


def _split_units(text: str, punct_own_cell: bool) -> list[tuple[str, float, str]]:
    """
    把一行文本拆成排版单元，返回 [(字符, 宽度格数, 角色)]。

    标点处理有两种：
      punct_own_cell=True   标点独立占一格（现代数字排版的做法）
      punct_own_cell=False  标点挂在上一格，宽度记 0 —— 排版函数看到宽度为 0
                            就把这个字贴到前一格的角上，不推进排版游标。
                            传统竖排的标点就是这样处理的。
    """
    out: list[tuple[str, float, str]] = []
    for ch in text:
        if _is_punct(ch):
            out.append(
                (ch, char_width_units(ch) if punct_own_cell else 0.0, ROLE_PUNCT)
            )
        else:
            out.append((ch, char_width_units(ch), ROLE_BODY))
    return out


# --------------------------------------------------------------------------
# 竖排
# --------------------------------------------------------------------------


def layout_vertical(
    poem: Poem,
    page: PageConfig,
    cfg: LayoutConfig,
) -> list[PlacedGlyph]:
    """
    竖排右起。列从右往左排，列内从上往下。

    列的顺序（从右到左）：标题 → 正文各行 → 署名 → 落款／印章
    """
    size = cfg.font_size_mm
    title_size = size * cfg.title_scale
    small_size = size * cfg.author_scale

    # 组装列：(单元列表, 字号, 角色)
    columns: list[tuple[list[tuple[str, float, str]], float]] = []

    if poem.title:
        columns.append((_split_units(poem.title, cfg.punct_own_cell), title_size))

    for line in poem.body_lines():
        columns.append((_split_units(line, cfg.punct_own_cell), size))

    # 署名
    if poem.author and poem.colophon in ("author", "full", "seal"):
        columns.append((_split_units(poem.author, cfg.punct_own_cell), small_size))

    # 完整落款：干支纪年 + 季 + 署名 + "书"
    if poem.colophon in ("full", "seal"):
        who = poem.dedication or poem.author
        colophon_text = f"{ganzhi_year()}{season_name()}{who}书"
        columns.append(
            (_split_units(colophon_text, cfg.punct_own_cell), size * cfg.colophon_scale)
        )

    if not columns:
        return []

    # 列间距：正文字的宽度 + 行间隙
    col_pitch = size + cfg.line_gap_mm

    # 整个列块在可用宽度内居中
    block_w = col_pitch * (len(columns) - 1) + max(c[1] for c in columns)
    start_x = page.margin_left_mm + (page.usable_w - block_w) / 2.0 + block_w

    glyphs: list[PlacedGlyph] = []
    # 记录每一列的几何信息，印章要挂在最左列的下方
    col_info: list[tuple[float, float, float]] = []  # (中心x, 顶y, 列高)

    for col_i, (units, col_size) in enumerate(columns):
        # 从右往左：第一列最靠右
        col_center_x = start_x - col_i * col_pitch - col_size / 2.0

        # 列内每个单元占的高度
        cell_pitch = col_size + cfg.char_gap_mm
        col_h = cell_pitch * len(units)
        # 整列在可用高度内居中
        col_top = page.margin_top_mm + (page.usable_h - col_h) / 2.0
        y = col_top + col_size / 2.0
        col_info.append((col_center_x, col_top, col_h))

        # 本列内上一个字，用于挂标点。不能直接用 glyphs[-1] ——
        # 如果标点恰好是某列的第一个单元，那个会取到上一列的字。
        prev_in_col: PlacedGlyph | None = None

        # 竖排里每个单元都占满一格：半角的西文在竖排中同样是竖着往下摞，
        # 所以不按 w_units 缩放纵向步进。
        # w_units == 0 的标点是"挂"在上一格右上角的，不推进游标。
        for ch, w_units, role in units:
            if w_units == 0.0 and prev_in_col is not None:
                g = PlacedGlyph(
                    char=ch,
                    cx=prev_in_col.cx + col_size * 0.30,
                    cy=prev_in_col.cy - col_size * 0.30,
                    size=col_size * 0.5,
                    role=ROLE_PUNCT,
                )
                glyphs.append(g)
                continue

            g = PlacedGlyph(char=ch, cx=col_center_x, cy=y, size=col_size, role=role)
            glyphs.append(g)
            prev_in_col = g
            y += cell_pitch

    # 印章：挂在最左列（落款列）的正下方
    if poem.colophon == "seal" and poem.author and col_info:
        last_cx, last_top, last_h = col_info[-1]
        seal_side = size * cfg.seal_size_scale
        seal_cy = last_top + last_h + seal_side / 2.0 + size * 0.9
        glyphs.extend(
            _make_seal(poem.dedication or poem.author, last_cx, seal_cy, seal_side)
        )

    return glyphs


def _make_seal(name: str, cx: float, cy: float, side: float) -> list[PlacedGlyph]:
    """
    生成一枚方印：正方形印框 + 框内排名字。

    单色笔只能画线条，所以这里是"朱文印"的做法 —— 勾出边框和名字的笔画。
    真朱文印是白底红字，靠线条围出来；轮廓近似到这一步已经够像了。

    印框用四条**通长直线**拼成，而不是四个角字符：角字符之间会留缺口，
    画出来是四个孤立的 L 形，不成框。
    """
    out: list[PlacedGlyph] = []
    half = side / 2.0

    # 印框：上下两条横贯线 + 左右两条竖贯线，四角恰好相接
    out.append(PlacedGlyph(char="━", cx=cx, cy=cy - half, size=side, role=ROLE_SEAL))
    out.append(PlacedGlyph(char="━", cx=cx, cy=cy + half, size=side, role=ROLE_SEAL))
    out.append(PlacedGlyph(char="┃", cx=cx - half, cy=cy, size=side, role=ROLE_SEAL))
    out.append(PlacedGlyph(char="┃", cx=cx + half, cy=cy, size=side, role=ROLE_SEAL))

    chars = [c for c in name if not c.isspace()][:4]
    if not chars:
        return out

    # 印章竖排、从右往左读。两字排一列，三字以上排两列（右列先填满）。
    n = len(chars)
    if n <= 2:
        cols, rows = 1, n
    else:
        cols = 2
        rows = math.ceil(n / 2)

    # 先把字按"第几列、第几行"（从右数）排好，再翻成从左数的列号
    slots: list[tuple[int, int]] = []
    for i in range(n):
        col_from_right = i // rows
        row = i % rows
        slots.append((cols - 1 - col_from_right, row))

    # 内区占印面 70% —— 留够边距，字才不会顶到框线上。
    # 70% 是刻印的常见比例，太满显得局促，太空显得单薄。
    inner = side * 0.70
    cell = inner / max(cols, rows)
    x0 = cx - inner / 2.0 + cell / 2.0
    y0 = cy - inner / 2.0 + cell / 2.0

    for (col, row), ch in zip(slots, chars):
        out.append(
            PlacedGlyph(
                char=ch,
                cx=x0 + col * cell,
                cy=y0 + row * cell,
                size=cell * 0.84,
                role=ROLE_SEAL,
            )
        )
    return out


# --------------------------------------------------------------------------
# 横排
# --------------------------------------------------------------------------


def layout_horizontal(
    poem: Poem,
    page: PageConfig,
    cfg: LayoutConfig,
) -> list[PlacedGlyph]:
    """
    横排。标题居中在最上，正文每行一横行，署名靠右下。

    这是现代排版的做法，比竖排简单：每一行独立计算宽度，然后按对齐方式摆。
    """
    size = cfg.font_size_mm
    title_size = size * cfg.title_scale
    small_size = size * cfg.author_scale

    # 组装行：(单元列表, 字号, 角色)
    rows: list[tuple[list[tuple[str, float, str]], float]] = []

    if poem.title:
        rows.append((_split_units(poem.title, cfg.punct_own_cell), title_size))
        rows.append(([], 0.0))  # 标题与正文之间的空行

    for line in poem.body_lines():
        rows.append((_split_units(line, cfg.punct_own_cell), size))

    if poem.author and poem.colophon in ("author", "full", "seal"):
        rows.append(([], 0.0))
        rows.append((_split_units(poem.author, cfg.punct_own_cell), small_size))

    if poem.colophon in ("full", "seal"):
        who = poem.dedication or poem.author
        rows.append(
            (
                _split_units(
                    f"{ganzhi_year()}{season_name()}{who}书", cfg.punct_own_cell
                ),
                size * cfg.colophon_scale,
            )
        )

    if not rows:
        return []

    # 每行的行高 = 该行字号 + 行间隙
    row_heights = [
        (r_size + cfg.line_gap_mm) if units else size * 0.8 for units, r_size in rows
    ]
    block_h = sum(row_heights)

    glyphs: list[PlacedGlyph] = []
    y = page.margin_top_mm + (page.usable_h - block_h) / 2.0

    for (units, row_size), row_h in zip(rows, row_heights):
        if not units:
            y += row_h
            continue

        # 行宽只算真正占位的单元，挂在前一格的标点不占宽度也不算间隙
        laid = [(c, w, r) for c, w, r in units if w > 0.0]
        row_w = sum(w * row_size for _, w, _ in laid)
        if laid:
            row_w += cfg.char_gap_mm * (len(laid) - 1)

        if cfg.align == "left":
            x = page.margin_left_mm
        else:
            x = page.margin_left_mm + (page.usable_w - row_w) / 2.0

        cy = y + row_h / 2.0
        for ch, w_units, role in units:
            if w_units == 0.0 and glyphs:
                # 挂标点：贴到前一个字的右下角，不推进游标
                prev = glyphs[-1]
                glyphs.append(
                    PlacedGlyph(
                        char=ch,
                        cx=prev.cx + row_size * 0.34,
                        cy=prev.cy + row_size * 0.30,
                        size=row_size * 0.5,
                        role=ROLE_PUNCT,
                    )
                )
                continue
            cell_w = w_units * row_size
            glyphs.append(
                PlacedGlyph(
                    char=ch, cx=x + cell_w / 2.0, cy=cy, size=row_size, role=role
                )
            )
            x += cell_w + cfg.char_gap_mm

        y += row_h

    # 横排也必须追加印章；此前这里只做完整落款，所以 seal 与 full 没有区别。
    if poem.colophon == "seal" and poem.author:
        seal_side = size * cfg.seal_size_scale
        seal_cx = page.width_mm - page.margin_right_mm - seal_side / 2.0
        # 落款行之下留一段空白放印；空间不足时贴近页面下边距。
        seal_cy = min(
            page.height_mm - page.margin_bottom_mm - seal_side / 2.0,
            y + size * 0.55 + seal_side / 2.0,
        )
        glyphs.extend(
            _make_seal(poem.dedication or poem.author, seal_cx, seal_cy, seal_side)
        )
    return glyphs


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def layout_poem(
    poem: Poem,
    page: PageConfig | None = None,
    cfg: LayoutConfig | None = None,
) -> list[PlacedGlyph]:
    """按 poem.direction 选择排版方式，返回摆好位置的字。"""
    page = page or PageConfig()
    cfg = cfg or LayoutConfig()
    if poem.direction == "horizontal":
        return layout_horizontal(poem, page, cfg)
    return layout_vertical(poem, page, cfg)


def fit_font_size(
    poem: Poem,
    page: PageConfig,
    cfg: LayoutConfig,
    min_size: float = 6.0,
) -> LayoutConfig:
    """
    自动缩小字号直到内容装得下。

    诗词长度差别很大，五言绝句和长篇排在一起，固定字号必然有装不下的。
    这里从给定字号开始按比例缩小，直到排版结果的包围盒落在可用区域内。

    返回调整后的 LayoutConfig（原对象不被修改）。
    """
    import dataclasses

    size = cfg.font_size_mm
    while size >= min_size:
        trial = dataclasses.replace(cfg, font_size_mm=size)
        glyphs = layout_poem(poem, page, trial)
        if not glyphs:
            return trial
        if _fits(glyphs, page):
            return trial
        size *= 0.92  # 每轮缩 8%，收敛够快也不会跳过头

    return dataclasses.replace(cfg, font_size_mm=min_size)


def _fits(glyphs: list[PlacedGlyph], page: PageConfig) -> bool:
    """
    检查排版结果的包围盒是否落在页边距内。

    放 0.5mm 的容差：字形本身还有笔画外扩，卡得太死会把刚好装得下的
    情况误判成装不下，白白缩小字号。
    """
    tol = 0.5
    max_x = max(g.cx + g.size / 2.0 for g in glyphs)
    max_y = max(g.cy + g.size / 2.0 for g in glyphs)
    min_x = min(g.cx - g.size / 2.0 for g in glyphs)
    min_y = min(g.cy - g.size / 2.0 for g in glyphs)

    return (
        min_x >= page.margin_left_mm - tol
        and min_y >= page.margin_top_mm - tol
        and max_x <= page.width_mm - page.margin_right_mm + tol
        and max_y <= page.height_mm - page.margin_bottom_mm + tol
    )


# --------------------------------------------------------------------------
# 落款用的纪年
# --------------------------------------------------------------------------

_TIANGAN = "甲乙丙丁戊己庚辛壬癸"
_DIZHI = "子丑寅卯辰巳午未申酉戌亥"
_SEASON = {
    1: "春",
    2: "春",
    3: "春",
    4: "夏",
    5: "夏",
    6: "夏",
    7: "秋",
    8: "秋",
    9: "秋",
    10: "冬",
    11: "冬",
    12: "冬",
}
_SEASON_PREFIX = ["孟", "仲", "季"]


def ganzhi_year(year: int | None = None) -> str:
    """
    干支纪年，例如 2026 -> 丙午年。

    注意：这里用的是公历年份直接换算，没有处理农历新年分界
    （立春或正月初一之前应算上一年）。落款用，误差可接受；
    如果要绝对准确，需要引入农历库。
    """
    y = year or datetime.now().year
    stem = _TIANGAN[(y - 4) % 10]
    branch = _DIZHI[(y - 4) % 12]
    return f"{stem}{branch}年"


def season_name(month: int | None = None) -> str:
    """季节，带孟/仲/季前缀，例如 九月 -> 季秋。"""
    m = month or datetime.now().month
    season = _SEASON[m]
    # 该季的第几个月：1/4/7/10 是孟，2/5/8/11 是仲，3/6/9/12 是季
    idx = (m - 1) % 3
    return f"{_SEASON_PREFIX[idx]}{season}月"
