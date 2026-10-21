"""毫米坐标下的纸面元素、表格排版及固定外框自适应。"""

from __future__ import annotations

import copy
import math

from ..config import CONFIG, ROOT
from ..drawing.svg import vector_strokes
from ..fonts import registry
from ..machine import gcode
from ..schemas.canvas import (
    CanvasDocument,
    Table,
    TextBox,
    Vector,
)
from .layout import char_width_units
from .models import PlacedGlyph, Poem, Stroke
from .page import page_config
from .prepare import Prepared, _glyph_source, build_configs
from .preview import strokes_to_svg
from .render import render_glyphs
from .smooth import smooth_strokes


def wrap_text(
    text: str, width: float, size: float, gap: float
) -> tuple[list[str], bool]:
    rows = []
    too_wide = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        row, used = "", 0.0
        for ch in line.expandtabs(4):
            advance = size * char_width_units(ch)
            too_wide |= advance > width + 1e-6
            extra = gap if row else 0
            if row and used + extra + advance > width + 1e-6:
                rows.append(row)
                row, used, extra = "", 0.0, 0
            row += ch
            used += extra + advance
        rows.append(row)
    return rows, too_wide


def text_layout(text, x, y, width, height, element, align="left", valign="top"):
    size, gap = element.font_size_mm, element.char_gap_mm
    rows, overflow = wrap_text(text, width, size, gap)
    needed = (
        len(rows) * size + max(0, len(rows) - 1) * element.line_gap_mm if text else 0
    )
    overflow |= bool(text) and (width <= 0 or needed > height + 1e-6)
    offset_y = (
        max(0, height - needed) / (2 if valign == "center" else 1)
        if valign != "top"
        else 0
    )
    glyphs, lines = [], []
    for index, row in enumerate(rows):
        length = (
            sum(size * char_width_units(ch) for ch in row) + max(0, len(row) - 1) * gap
        )
        offset_x = (
            (width - length) / (2 if align == "center" else 1) if align != "left" else 0
        )
        cursor, top = x + offset_x, y + offset_y + index * (size + element.line_gap_mm)
        lines.append({"text": row, "x_mm": cursor, "y_mm": top})
        for ch in row:
            advance = size * char_width_units(ch)
            if not ch.isspace():
                glyphs.append(
                    PlacedGlyph(
                        ch,
                        cursor + advance / 2,
                        top + size / 2,
                        size,
                        scale_x=advance / size,
                    )
                )
            cursor += advance + gap
    return glyphs, lines, overflow


def boundaries(values):
    out = [0.0]
    for value in values:
        out.append(out[-1] + value)
    return out


def table_geometry(table: Table):
    xs, ys = boundaries(table.column_widths), boundaries(table.row_heights)
    cells = []
    for cell in sorted(table.cells, key=lambda c: (c.row, c.col)):
        cells.append(
            (
                cell,
                table.x_mm + xs[cell.col],
                table.y_mm + ys[cell.row],
                xs[cell.col + cell.colspan] - xs[cell.col],
                ys[cell.row + cell.rowspan] - ys[cell.row],
            )
        )
    return cells


def table_lines(table: Table, max_segment_mm: float = 5.0) -> list[Stroke]:
    """共享边去重后分段，每段独立抬落笔，端点相接保持实线。"""
    if not math.isfinite(max_segment_mm) or max_segment_mm <= 0:
        raise ValueError("表格框线分段长度必须是正有限数值")
    xs, ys = boundaries(table.column_widths), boundaries(table.row_heights)
    horizontal, vertical = set(), set()
    for c in table.cells:
        for col in range(c.col, c.col + c.colspan):
            horizontal.update(((c.row, col), (c.row + c.rowspan, col)))
        for row in range(c.row, c.row + c.rowspan):
            vertical.update(((c.col, row), (c.col + c.colspan, row)))
    strokes = []
    for edges, axes, spans, transpose in [
        (horizontal, ys, xs, False),
        (vertical, xs, ys, True),
    ]:
        for axis in sorted({a for a, _ in edges}):
            indices = sorted(i for a, i in edges if a == axis)
            runs = []
            for i in indices:
                if runs and runs[-1][1] == i:
                    runs[-1][1] = i + 1
                else:
                    runs.append([i, i + 1])
            for start, end in runs:
                pts = (
                    [(axes[axis], spans[start]), (axes[axis], spans[end])]
                    if transpose
                    else [(spans[start], axes[axis]), (spans[end], axes[axis])]
                )
                (x0, y0), (x1, y1) = pts
                length = math.hypot(x1 - x0, y1 - y0)
                for i in range(math.ceil(length / max_segment_mm)):
                    a = min(i * max_segment_mm, length) / length
                    b = min((i + 1) * max_segment_mm, length) / length
                    strokes.append(
                        Stroke(
                            [
                                (
                                    table.x_mm + x0 + (x1 - x0) * a,
                                    table.y_mm + y0 + (y1 - y0) * a,
                                ),
                                (
                                    table.x_mm + x0 + (x1 - x0) * b,
                                    table.y_mm + y0 + (y1 - y0) * b,
                                ),
                            ],
                            role="table",
                        )
                    )
    return strokes


def prepare_canvas(doc: CanvasDocument) -> tuple[Prepared, bool]:
    page = page_config(doc)
    cfg = build_configs({"handwriting": False, "seed": 0})
    strokes, missing, diagnostics, layouts = [], [], [], []
    glyph_count = 0
    for element in doc.elements:
        invalid = (
            element.x_mm < doc.margin_mm - 1e-6
            or element.y_mm < doc.margin_mm - 1e-6
            or element.x_mm + element.width_mm > page.width_mm - doc.margin_mm + 1e-6
            or element.y_mm + element.height_mm > page.height_mm - doc.margin_mm + 1e-6
        )
        if invalid:
            diagnostics.append(
                {
                    "element_id": element.id,
                    "code": "bounds",
                    "message": "元素超出纸面可写范围",
                }
            )
        if isinstance(element, Vector):
            paths = vector_strokes(element)
            if any(
                x < doc.margin_mm - 1e-5
                or y < doc.margin_mm - 1e-5
                or x > page.width_mm - doc.margin_mm + 1e-5
                or y > page.height_mm - doc.margin_mm + 1e-5
                for stroke in paths
                for x, y in stroke.points
            ):
                diagnostics.append(
                    {
                        "element_id": element.id,
                        "code": "bounds",
                        "message": "矢量路径超出纸面可写范围",
                    }
                )
            strokes.extend(paths)
            layouts.append({"element_id": element.id, "regions": []})
            continue
        if element.font and not any(
            f.id == element.font for f in registry.discover(ROOT)
        ):
            raise ValueError("所选字库不可用，请重新选择")
        source = _glyph_source(element.font or None)
        regions = (
            [(None, element.x_mm, element.y_mm, element.width_mm, element.height_mm)]
            if isinstance(element, TextBox)
            else table_geometry(element)
        )
        if isinstance(element, Table) and element.draw_borders:
            strokes.extend(table_lines(element, cfg["machine"].table_segment_mm))
        region_layouts = []
        for cell, x, y, w, h in regions:
            padding = element.padding_mm if isinstance(element, Table) else 0
            text = cell.text if cell else element.text
            glyphs, lines, overflow = text_layout(
                text,
                x + padding,
                y + padding,
                w - 2 * padding,
                h - 2 * padding,
                element,
                cell.align if cell else element.align,
                cell.valign if cell else "top",
            )
            glyph_count += len(glyphs)
            paths, absent = render_glyphs(glyphs, source, cfg["jitter"])
            if cfg["quality"]["enabled"]:
                paths = smooth_strokes(
                    paths, **{k: v for k, v in cfg["quality"].items() if k != "enabled"}
                )
            # 实际笔画同样不能越过框内边界。
            ink_overflow = any(
                px < x + padding - 1e-5
                or px > x + w - padding + 1e-5
                or py < y + padding - 1e-5
                or py > y + h - padding + 1e-5
                for s in paths
                for px, py in s.points
            )
            overflow |= ink_overflow
            key = {
                "element_id": element.id,
                **({"row": cell.row, "col": cell.col} if cell else {}),
            }
            if overflow:
                diagnostics.append(
                    {
                        **key,
                        "code": "overflow",
                        "message": "单元格内容溢出" if cell else "文本框内容溢出",
                    }
                )
            if absent:
                diagnostics.append(
                    {**key, "code": "missing", "message": "缺字：" + "、".join(absent)}
                )
            missing.extend(ch for ch in absent if ch not in missing)
            strokes.extend(paths)
            region_layouts.append(
                {
                    **key,
                    "x_mm": x,
                    "y_mm": y,
                    "width_mm": w,
                    "height_mm": h,
                    "lines": lines,
                    "overflow": overflow,
                }
            )
        layouts.append({"element_id": element.id, "regions": region_layouts})
    svg = strokes_to_svg(strokes, page, color="#252a2a", bg="#f8f7f2")
    guide = f'<rect x="{doc.margin_mm}" y="{doc.margin_mm}" width="{page.usable_w}" height="{page.usable_h}" fill="none" stroke="#252a2a" stroke-opacity="0.16" stroke-width="0.5" stroke-dasharray="1.5 2"/>'
    svg = svg.replace("</svg>", guide + "</svg>")
    overflow = any(d["code"] in ("bounds", "overflow") for d in diagnostics)
    result = Prepared(
        submission_id=0,
        poem=Poem(body=doc.body, direction="horizontal"),
        gcode=gcode.strokes_to_gcode(strokes, page, cfg["machine"]),
        svg=svg,
        strokes=len(strokes),
        cells=glyph_count,
        font_size_mm=8,
        requested_size=8,
        missing=missing,
        machine_snapshot=copy.deepcopy(CONFIG["machine"]),
        blocked=bool(diagnostics),
        layout=layouts,
        diagnostics=diagnostics,
    )
    return result, overflow


def fit_table(table: Table) -> tuple[Table, list[dict]]:
    """确定性多起点列宽搜索 + 跨行高度约束 + 0.1mm 字号搜索。"""
    if not any(c.text for c in table.cells):
        return table, []
    if all(
        not text_layout(
            c.text,
            x + table.padding_mm,
            y + table.padding_mm,
            w - 2 * table.padding_mm,
            h - 2 * table.padding_mm,
            table,
            c.align,
            c.valign,
        )[2]
        for c, x, y, w, h in table_geometry(table)
    ):
        return table, []
    cols, rows, pad = len(table.column_widths), len(table.row_heights), table.padding_mm
    floor = max(0.25, 2 * pad)
    if table.width_mm < cols * floor or table.height_mm < rows * floor:
        return table, [
            {
                "element_id": table.id,
                "code": "overflow",
                "message": "表格太小，无法容纳单元格内边距",
            }
        ]

    def candidate(size):
        cache = {}

        def evaluate(widths):
            edges = [[] for _ in range(rows + 1)]
            penalty = 0.0
            for c in table.cells:
                w = sum(widths[c.col : c.col + c.colspan]) - 2 * pad
                key = (c.text, round(w, 6))
                if key not in cache:
                    wrapped, bad = wrap_text(c.text, w, size, table.char_gap_mm)
                    height = (
                        len(wrapped) * size
                        + max(0, len(wrapped) - 1) * table.line_gap_mm
                        if c.text
                        else 0
                    )
                    widest = max(
                        (
                            size * char_width_units(ch)
                            for ch in c.text
                            if ch not in "\r\n\t"
                        ),
                        default=0,
                    )
                    cache[key] = (height + 2 * pad, max(0, widest - w) if bad else 0)
                need, deficit = cache[key]
                penalty += deficit * 1000
                edges[c.row + c.rowspan].append((c.row, need))
            ys = [0.0]
            for end in range(1, rows + 1):
                ys.append(
                    max(
                        [ys[-1] + floor]
                        + [ys[start] + need for start, need in edges[end]]
                    )
                )
            return ys[-1] + (table.height_mm + penalty if penalty > 1e-6 else 0), [
                ys[i + 1] - ys[i] for i in range(rows)
            ]

        weights = [1.0] * cols
        for c in table.cells:
            for col in range(c.col, c.col + c.colspan):
                weights[col] += sum(char_width_units(ch) for ch in c.text) / c.colspan
        spare = table.width_mm - floor * cols
        seeds = [
            list(table.column_widths),
            [table.width_mm / cols] * cols,
            [floor + spare * w / sum(weights) for w in weights],
        ]
        best = None
        for widths in seeds:
            score, heights = evaluate(widths)
            for step in (2.0, 1.0, 0.25):
                for _ in range(24):
                    improvement = None
                    for donor in range(cols):
                        if widths[donor] - step < floor - 1e-6:
                            continue
                        for receiver in range(cols):
                            if donor == receiver:
                                continue
                            trial = list(widths)
                            trial[donor] -= step
                            trial[receiver] += step
                            value, hs = evaluate(trial)
                            if value < score - 1e-6 and (
                                improvement is None or value < improvement[0] - 1e-6
                            ):
                                improvement = (value, trial, hs)
                    if improvement is None:
                        break
                    score, widths, heights = improvement
            if best is None or score < best[0] - 1e-6:
                best = (score, widths, heights)
        return best

    result = candidate(4)
    if result[0] > table.height_mm + 1e-6:
        probe = table.model_copy(update={"font_size_mm": 4.0})
        diagnostics = []
        for c, x, y, w, h in table_geometry(probe):
            if text_layout(
                c.text,
                x + pad,
                y + pad,
                w - 2 * pad,
                h - 2 * pad,
                probe,
                c.align,
                c.valign,
            )[2]:
                diagnostics.append(
                    {
                        "element_id": table.id,
                        "row": c.row,
                        "col": c.col,
                        "code": "overflow",
                        "message": "4 毫米字号仍无法容纳，请扩大表格或减少内容",
                    }
                )
        return table, diagnostics or [
            {
                "element_id": table.id,
                "code": "overflow",
                "message": "4 毫米字号仍无法容纳，请扩大表格或减少内容",
            }
        ]
    current = candidate(table.font_size_mm)
    low, high = 40, math.floor(table.font_size_mm * 10)
    best_size, best = 4.0, result
    if current[0] <= table.height_mm + 1e-6:
        best_size, best = table.font_size_mm, current
        high = low - 1
    while low <= high:
        mid = (low + high) // 2
        found = candidate(mid / 10)
        if found[0] <= table.height_mm + 1e-6:
            best_size, best = mid / 10, found
            low = mid + 1
        else:
            high = mid - 1
    _, widths, heights = best
    remaining = max(0, table.height_mm - sum(heights))
    heights = [
        h + remaining * old / table.height_mm
        for h, old in zip(heights, table.row_heights)
    ]
    widths[-1] = table.width_mm - sum(widths[:-1])
    heights[-1] = table.height_mm - sum(heights[:-1])
    fitted = Table.model_validate(
        {
            **table.model_dump(),
            "font_size_mm": best_size,
            "column_widths": widths,
            "row_heights": heights,
        }
    )
    for c, x, y, w, h in table_geometry(fitted):
        if text_layout(
            c.text,
            x + pad,
            y + pad,
            w - 2 * pad,
            h - 2 * pad,
            fitted,
            c.align,
            c.valign,
        )[2]:
            return table, [
                {
                    "element_id": table.id,
                    "row": c.row,
                    "col": c.col,
                    "code": "overflow",
                    "message": "自适应校验失败，请调整内容",
                }
            ]
    return fitted, []
