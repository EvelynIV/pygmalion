"""按 SVG 绕数规则生成毫米扫描线；每个区间独立落笔，空洞不连线。"""

import math
from itertools import pairwise

from ..schemas.vector import MAX_POINTS
from ..writing.models import Stroke


def hatch_strokes(contours, rule, spacing, angle):
    theta = math.radians(angle)
    c, s = math.cos(theta), math.sin(theta)

    def rotate(p):
        return c * p[0] + s * p[1], -s * p[0] + c * p[1]

    def restore(x, y):
        return c * x - s * y, s * x + c * y

    edges = []
    for contour in contours:
        points = [rotate(p) for p in contour.points]
        if points[0] != points[-1]:
            points.append(points[0])
        edges.extend(
            (a, b) for a, b in pairwise(points) if abs(a[1] - b[1]) > 1e-12
        )
    if not edges:
        return []
    low = min(min(a[1], b[1]) for a, b in edges)
    high = max(max(a[1], b[1]) for a, b in edges)
    rows = max(1, math.ceil((high - low) / spacing))
    if rows > MAX_POINTS or rows * len(edges) > 10_000_000:
        raise ValueError("填充计算过于复杂，请增大填充间距或简化图形")
    result = []
    for row in range(rows):
        y = low + (row + 0.5) * (high - low) / rows
        crossings = []
        for a, b in edges:
            if min(a[1], b[1]) <= y < max(a[1], b[1]):
                x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                crossings.append((x, 1 if b[1] > a[1] else -1))
        crossings.sort()
        winding = 0
        previous = None
        intervals = []
        for x, direction in crossings:
            inside = winding % 2 != 0 if rule == "evenodd" else winding != 0
            if inside and previous is not None and x - previous > 1e-9:
                if intervals and abs(intervals[-1][1] - previous) < 1e-9:
                    intervals[-1] = (intervals[-1][0], x)
                else:
                    intervals.append((previous, x))
            winding += direction
            previous = x
        if row % 2:
            intervals = [(b, a) for a, b in reversed(intervals)]
        for a, b in intervals:
            result.append(Stroke([restore(a, y), restore(b, y)], role="vector"))
        if len(result) * 2 > MAX_POINTS:
            raise ValueError("填充路径超过 100000 个点，请增大填充间距")
    return result
