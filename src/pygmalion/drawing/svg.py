"""受限 SVG 导入与毫米级曲线离散，不执行脚本或读取外部资源。"""

from __future__ import annotations

import math
from copy import copy
from io import StringIO

from defusedxml import ElementTree
from svgelements import (
    SVG,
    Arc,
    Close,
    CubicBezier,
    Line,
    Matrix,
    Move,
    Path,
    QuadraticBezier,
    Shape,
)

from ..schemas.vector import MAX_POINTS, MAX_SEGMENTS, MAX_SOURCE, checked_path
from ..writing.models import Stroke

TOLERANCE_MM = 0.05


def transform_path(path: Path, matrix: Matrix) -> Path:
    """仿射变换后重建圆弧的正交主轴，避免倾斜圆弧在 d() 中失真。"""
    result = Path()
    for segment in path:
        if not isinstance(segment, Arc):
            result.append(copy(segment) * matrix)
            continue
        theta = segment.get_rotation()
        rx, ry = segment.rx, segment.ry
        # B = affine_linear × rotation × radii；BBᵀ 的特征轴是新椭圆主轴。
        ux, uy = rx * math.cos(theta), rx * math.sin(theta)
        vx, vy = -ry * math.sin(theta), ry * math.cos(theta)
        u = (matrix.a * ux + matrix.c * uy, matrix.b * ux + matrix.d * uy)
        v = (matrix.a * vx + matrix.c * vy, matrix.b * vx + matrix.d * vy)
        xx, yy, xy = (
            u[0] ** 2 + v[0] ** 2,
            u[1] ** 2 + v[1] ** 2,
            u[0] * u[1] + v[0] * v[1],
        )
        major = math.sqrt(max(0, (xx + yy + math.hypot(xx - yy, 2 * xy)) / 2))
        minor = abs(u[0] * v[1] - u[1] * v[0]) / major if major else 0
        start, end = segment.start * matrix, segment.end * matrix
        if not major or not minor:
            result.append(Line(start, end))
            continue
        angle = math.degrees(math.atan2(2 * xy, xx - yy) / 2)
        sweep = segment.sweep * (1 if matrix.determinant >= 0 else -1)
        result.append(
            Arc(start, major, minor, angle, abs(sweep) > math.pi, sweep > 0, end)
        )
    return result


def import_svg(source: str) -> dict:
    if len(source.encode("utf-8")) > MAX_SOURCE:
        raise ValueError("SVG 文件不能超过 1 MB")
    try:
        root = ElementTree.fromstring(source, forbid_dtd=True)
        if root.tag.split("}")[-1] != "svg":
            raise ValueError("文件必须是 SVG")
        allowed = {
            "svg",
            "g",
            "path",
            "line",
            "polyline",
            "polygon",
            "rect",
            "circle",
            "ellipse",
            "title",
            "desc",
            "metadata",
            "defs",
        }
        nodes = list(root.iter())
        if len(nodes) > MAX_SEGMENTS:
            raise ValueError("SVG 元素过多")
        for node in nodes:
            tag = node.tag.split("}")[-1]
            if tag not in allowed:
                raise ValueError(
                    f"暂不支持 SVG 元素 {tag}，请先转换为路径并移除位图、裁剪及特效"
                )
            for key, value in node.attrib.items():
                key = key.split("}")[-1].lower()
                if (
                    key.startswith("on")
                    or key
                    in {
                        "href",
                        "clip-path",
                        "mask",
                        "filter",
                        "stroke-dasharray",
                        "stroke-dashoffset",
                    }
                    or "url(" in value.lower()
                ):
                    raise ValueError("SVG 不支持脚本、资源引用、裁剪、蒙版及滤镜")
                if key == "style":
                    for declaration in value.split(";"):
                        if declaration.strip() and declaration.split(":")[
                            0
                        ].strip() not in {
                            "fill",
                            "stroke",
                            "stroke-width",
                            "fill-opacity",
                            "stroke-opacity",
                            "opacity",
                            "display",
                            "visibility",
                            "fill-rule",
                            "stroke-linecap",
                            "stroke-linejoin",
                            "stroke-miterlimit",
                        }:
                            raise ValueError("SVG 包含不支持的样式，请先转换为普通路径")
            if tag == "path" and node.get("d"):
                checked_path(node.get("d"))

        # opacity 是组级合成属性：父组为零时，子元素不能重新变得可见。
        def prune_hidden(node):
            styles = dict(
                part.split(":", 1)
                for part in node.get("style", "").split(";")
                if ":" in part
            )
            styles = {key.strip(): value.strip() for key, value in styles.items()}
            if (
                styles.get("display", node.get("display")) == "none"
                or float(styles.get("opacity", node.get("opacity", "1"))) == 0
            ):
                return False
            for child in list(node):
                if not prune_hidden(child):
                    node.remove(child)
            return True

        if not prune_hidden(root):
            raise ValueError("SVG 没有可绘制的轮廓")
        # 已验证的 XML 才交给几何解析器；不允许 use/image/style 等间接内容。
        svg = SVG.parse(
            StringIO(ElementTree.tostring(root, encoding="unicode")),
            ppi=96,
            reify=False,
            on_error="raise",
        )
        paths = []
        fill_rules = []
        count = 0
        for shape in svg.elements():
            if not isinstance(shape, Shape):
                continue
            if (
                shape.values.get("visibility") in {"hidden", "collapse"}
                or float(shape.values.get("opacity", 1)) == 0
            ):
                continue
            filled = shape.fill.value is not None and shape.fill.alpha != 0
            stroked = (
                shape.stroke.value is not None
                and shape.stroke.alpha != 0
                and shape.stroke_width > 0
            )
            if not filled and not stroked:
                continue
            path = transform_path(
                Path(shape.segments(transformed=False)), shape.transform
            )
            if filled:
                outlined = Path()
                for subpath in path.as_subpaths():
                    part = Path(subpath)
                    segments = [s for s in part if not isinstance(s, Move)]
                    if not segments:
                        continue
                    if len(segments) == 1 and isinstance(segments[0], Line):
                        if not stroked:
                            continue
                    elif not isinstance(part[-1], Close):
                        part.closed()
                    outlined += part
                path = outlined
            if not any(
                not isinstance(s, Move)
                and (
                    s.start != s.end
                    or isinstance(s, (CubicBezier, QuadraticBezier, Arc))
                )
                for s in path
            ):
                continue
            count += len(path)
            if count > MAX_SEGMENTS:
                raise ValueError("SVG 路径过于复杂，最多支持 10000 段")
            rule = shape.values.get("fill-rule", "nonzero") if filled else None
            if rule not in (None, "nonzero", "evenodd"):
                raise ValueError("不支持的 SVG 填充规则")
            fill_rules.append(rule)
            paths.append(path)
            if len(paths) > 1000:
                raise ValueError("SVG 最多支持 1000 个图形")
        if not paths:
            raise ValueError("SVG 没有可绘制的轮廓")
        boxes = [p.bbox() for p in paths]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            raise ValueError("SVG 坐标必须是有限数值")
        w, h = max(x1 - x0, 96 / 25.4), max(y1 - y0, 96 / 25.4)
        normalized = []
        for path in paths:
            path = transform_path(path, Matrix(1 / w, 0, 0, 1 / h, -x0 / w, -y0 / h))
            # Arc.d() 只保留 6 位有效数字；保留主轴精度以免归一化后越界。
            normalized.append(
                " ".join(
                    f"A {s.rx:.17g} {s.ry:.17g} {math.degrees(s.get_rotation()):.17g} {int(abs(s.sweep) > math.pi)} {int(s.sweep >= 0)} {s.end.x:.17g} {s.end.y:.17g}"
                    if isinstance(s, Arc)
                    else s.d(relative=False, smooth=False)
                    for s in path
                )
            )
        return {
            "paths": normalized,
            "fill_rules": fill_rules,
            "draw_mode": "solid" if any(fill_rules) else "outline",
            "width_mm": float(w * 25.4 / 96),
            "height_mm": float(h * 25.4 / 96),
        }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法解析 SVG，请检查文件格式") from exc


def outline_strokes(element, tolerance=TOLERANCE_MM) -> list[Stroke]:
    """每个子路径一笔；曲线误差在最终纸面毫米坐标中控制。"""
    strokes = []
    total = 0
    points = []

    def append(point):
        nonlocal total, points
        p = (float(point[0]), float(point[1]))
        if not all(math.isfinite(v) for v in p):
            raise ValueError("矢量坐标必须是有限数值")
        if not points or points[-1] != p:
            total += 1
            if total > MAX_POINTS:
                raise ValueError("矢量图离散后超过 100000 个点，请简化图形")
            points.append(p)

    def finish():
        nonlocal points
        if len(points) > 1:
            strokes.append(Stroke(points, role="vector"))
        points = []

    def bezier(controls, depth=0):
        a, b = controls[0], controls[-1]
        # 控制多边形到弦段的距离给出 Bézier 曲线的保守误差界。
        dx, dy = b[0] - a[0], b[1] - a[1]

        def distance(p):
            t = (
                max(
                    0,
                    min(
                        1,
                        ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy),
                    ),
                )
                if dx or dy
                else 0
            )
            return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)

        if max(distance(p) for p in controls) <= tolerance:
            append(b)
            return
        if depth >= 24:
            raise ValueError("矢量曲线过于复杂")
        levels = [controls]
        while len(levels[-1]) > 1:
            levels.append(
                [
                    ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    for a, b in zip(levels[-1], levels[-1][1:])
                ]
            )
        bezier([level[0] for level in levels], depth + 1)
        bezier([level[-1] for level in reversed(levels)], depth + 1)

    for data in element.paths:
        path = checked_path(data)
        path = transform_path(
            path,
            Matrix(
                element.width_mm, 0, 0, element.height_mm, element.x_mm, element.y_mm
            ),
        )

        for segment in path:
            if isinstance(segment, Move):
                finish()
                append(segment.end)
                continue
            append(segment.start)
            if isinstance(segment, (Line, Close)):
                append(segment.end)
            elif isinstance(segment, (CubicBezier, QuadraticBezier)):
                controls = (
                    [segment.start, segment.control1, segment.control2, segment.end]
                    if isinstance(segment, CubicBezier)
                    else [segment.start, segment.control, segment.end]
                )
                bezier([tuple(p) for p in controls])
            elif isinstance(segment, Arc):
                radius = abs(segment.prx - segment.center) + abs(
                    segment.pry - segment.center
                )
                n = max(
                    1,
                    math.ceil(abs(segment.sweep) * math.sqrt(radius / (8 * tolerance))),
                )
                if n > MAX_POINTS:
                    raise ValueError("矢量曲线过于复杂")
                for i in range(1, n + 1):
                    append(segment.point(i / n))
            else:
                raise ValueError("不支持的矢量路径段")  # noqa: TRY004
        finish()
    return strokes


def vector_strokes(element) -> list[Stroke]:
    """预览和机器共用路径；填充在最终毫米坐标中生成。"""
    from .fill import hatch_strokes

    tolerance = min(TOLERANCE_MM, element.fill_spacing_mm / 4)
    outlines = outline_strokes(element, tolerance)
    if element.draw_mode == "solid":
        fills = []
        for data, rule in zip(element.paths, element.fill_rules):
            if rule is None:
                continue
            contours = outline_strokes(
                element.model_copy(update={"paths": [data]}), tolerance
            )
            fills.extend(
                hatch_strokes(
                    contours, rule, element.fill_spacing_mm, element.fill_angle_deg
                )
            )
            if sum(len(s.points) for s in fills) > MAX_POINTS:
                raise ValueError("填充路径超过 100000 个点，请增大填充间距")
        outlines = outlines + fills
    unique = []
    seen = set()
    for stroke in outlines:
        key = tuple(stroke.points)
        if key in seen or key[::-1] in seen:
            continue
        seen.add(key)
        stroke.width_mm = element.pen_width_mm
        unique.append(stroke)
    if sum(len(s.points) for s in unique) > MAX_POINTS:
        raise ValueError("矢量图绘制路径超过 100000 个点，请增大填充间距")
    return unique
