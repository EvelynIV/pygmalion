"""
笔画平滑 —— 把稀疏折线变成光滑曲线。

为什么需要
----------
汉字单线数据里，每条笔画平均只有 6 个点（一个字 65 个点左右）。直接用
G1 把这些点连起来，撇捺的弧度会变成明显的多边形棱角，看起来"方硬、不像
手写"——这是"字丑"最主要的来源。数据本身没错，是点太少。

两个必须同时处理的问题
----------------------
1. **要平滑**：把折线用样条插值成曲线，弧度才连续。
2. **不能全都平滑**：汉字里有大量真正的**尖角**——横折的直角、竖钩的转折、
   "口"字的四个角。这些是字形的一部分，磨圆了就不是那个字了。

所以流程是：先找出尖角，在尖角处把笔画切成若干段，每段内部做样条插值，
尖角本身原样保留。

为什么用向心 Catmull-Rom
------------------------
普通（均匀）Catmull-Rom 在点距不均时会**过冲**——曲线冲出原始折线之外，
尖锐转折处甚至自交打圈。汉字数据点距本来就不均匀，正好踩这个坑。

向心参数化（alpha = 0.5）是专门为解决这件事提出的：它保证曲线不会自交、
不会打圈，过冲也小得多。公式来自 Barry-Goldman 的递归求值。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import math

Point = tuple[float, float]
Polyline = list[Point]

# 默认参数。这几个值都经过实测权衡，改动前先想清楚影响：
#
#   corner_deg    判定"这里是尖角"的转折角阈值。太小会把平缓的弧也当尖角
#                 （平滑失效），太大则会把"口"字的直角磨圆。45° 附近比较稳。
#   samples       每段插值取几个点。4 已经看不出棱角，再大只是让 G-code 变长。
#   max_seg_mm    重采样后的最大点间距（毫米）。写字时 0.4mm 一段足够光滑，
#                 又不会让文件爆炸。
DEFAULT_CORNER_DEG = 45.0
DEFAULT_SAMPLES = 4
DEFAULT_MAX_SEG_MM = 0.4

_EPS = 1e-9


# --------------------------------------------------------------------------
# 尖角检测
# --------------------------------------------------------------------------


def _turn_angle_deg(a: Point, b: Point, c: Point) -> float:
    """
    b 点处的转折角（度）。0 = 笔直向前，180 = 完全折返。

    用向量夹角算，注意要处理零长度段（重复点会造成这个）。
    """
    v1x, v1y = b[0] - a[0], b[1] - a[1]
    v2x, v2y = c[0] - b[0], c[1] - b[1]
    n1 = math.hypot(v1x, v1y)
    n2 = math.hypot(v2x, v2y)
    if n1 < _EPS or n2 < _EPS:
        return 0.0
    cos_t = (v1x * v2x + v1y * v2y) / (n1 * n2)
    cos_t = max(-1.0, min(1.0, cos_t))
    return math.degrees(math.acos(cos_t))


def split_at_corners(
    points: Polyline, corner_deg: float = DEFAULT_CORNER_DEG
) -> list[Polyline]:
    """
    在尖角处把折线切成若干段。返回的每一段内部都是"平缓的"，适合做样条。

    判据是转折角：相邻两段的夹角超过 corner_deg 就认为这里是字形本身的
    角，不参与平滑。切点会被**同时保留在前后两段里**，这样拼接之后曲线
    仍然连续，尖角也还在。
    """
    if len(points) < 3:
        return [list(points)]

    # 先去重：重复点会让向量长度为 0，转折角算不出来
    pts: Polyline = [points[0]]
    for p in points[1:]:
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-6:
            pts.append(p)
    if len(pts) < 3:
        return [pts]

    runs: list[Polyline] = []
    current: Polyline = [pts[0], pts[1]]

    for i in range(1, len(pts) - 1):
        angle = _turn_angle_deg(pts[i - 1], pts[i], pts[i + 1])
        if angle >= corner_deg:
            # pts[i] 是尖角：当前段到此为止，新段从它开始
            runs.append(current)
            current = [pts[i], pts[i + 1]]
        else:
            current.append(pts[i + 1])

    runs.append(current)
    return [r for r in runs if len(r) >= 2]


# --------------------------------------------------------------------------
# 样条插值
# --------------------------------------------------------------------------


def _centripetal_catmull_rom(
    p0: Point, p1: Point, p2: Point, p3: Point, samples: int
) -> Polyline:
    """
    在 p1 和 p2 之间插入 samples 个点，用向心 Catmull-Rom。

    alpha = 0.5 就是"向心"参数化。取 0 是均匀参数化（会过冲打圈），
    取 1 是弦长参数化（过冲小但对点距突变敏感）。0.5 是权衡点。

    返回**不含** p1 的中间点（p1 由调用方负责放进去，免得重复）。
    """

    def tj(ti: float, pa: Point, pb: Point) -> float:
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        return ti + max(math.hypot(dx, dy), _EPS) ** 0.5

    t0 = 0.0
    t1 = tj(t0, p0, p1)
    t2 = tj(t1, p1, p2)
    t3 = tj(t2, p2, p3)

    # 时间区间退化（三点重合之类）时直接退回直线，别产生 NaN
    if t1 - t0 < _EPS or t2 - t1 < _EPS or t3 - t2 < _EPS:
        return [
            (
                p1[0] + (p2[0] - p1[0]) * i / samples,
                p1[1] + (p2[1] - p1[1]) * i / samples,
            )
            for i in range(1, samples + 1)
        ]

    out: Polyline = []
    for i in range(1, samples + 1):
        t = t1 + (t2 - t1) * i / samples

        # Barry-Goldman 递归：三层线性插值
        a1x = ((t1 - t) * p0[0] + (t - t0) * p1[0]) / (t1 - t0)
        a1y = ((t1 - t) * p0[1] + (t - t0) * p1[1]) / (t1 - t0)
        a2x = ((t2 - t) * p1[0] + (t - t1) * p2[0]) / (t2 - t1)
        a2y = ((t2 - t) * p1[1] + (t - t1) * p2[1]) / (t2 - t1)
        a3x = ((t3 - t) * p2[0] + (t - t2) * p3[0]) / (t3 - t2)
        a3y = ((t3 - t) * p2[1] + (t - t2) * p3[1]) / (t3 - t2)

        b1x = ((t2 - t) * a1x + (t - t0) * a2x) / (t2 - t0)
        b1y = ((t2 - t) * a1y + (t - t0) * a2y) / (t2 - t0)
        b2x = ((t3 - t) * a2x + (t - t1) * a3x) / (t3 - t1)
        b2y = ((t3 - t) * a2y + (t - t1) * a3y) / (t3 - t1)

        cx = ((t2 - t) * b1x + (t - t1) * b2x) / (t2 - t1)
        cy = ((t2 - t) * b1y + (t - t1) * b2y) / (t2 - t1)
        out.append((cx, cy))

    return out


def _smooth_run(run: Polyline, samples: int) -> Polyline:
    """
    对一段"没有尖角"的折线做样条平滑。

    两端用端点自身外推当虚拟控制点（标准做法），这样曲线从端点出发、
    到端点结束，不会因为缺控制点而改变起止位置 —— 起止位置变了，笔画
    之间就会出现缝隙。
    """
    if len(run) < 3:
        return list(run)

    # 前后各补一个虚拟控制点
    ext: Polyline = [run[0]] + list(run) + [run[-1]]
    out: Polyline = [run[0]]

    for i in range(1, len(ext) - 2):
        out.extend(
            _centripetal_catmull_rom(
                ext[i - 1], ext[i], ext[i + 1], ext[i + 2], samples
            )
        )

    # 保证终点精确等于原始终点
    if out[-1] != run[-1]:
        out.append(run[-1])
    return out


# --------------------------------------------------------------------------
# 重采样
# --------------------------------------------------------------------------


def resample(points: Polyline, max_seg: float = DEFAULT_MAX_SEG_MM) -> Polyline:
    """
    按最大间距重新取点。

    平滑之后点的疏密很不均匀（曲率大的地方点密），但写字时真正影响观感的是
    **每段的物理长度**：太长的段落走快了会有折角感，太短又让 G-code 白白膨胀。
    所以按弧长均匀重采样。

    长度本来就短于 max_seg 的段落原样保留，不做无谓加密。
    """
    if len(points) < 2 or max_seg <= 0:
        return list(points)

    out: Polyline = [points[0]]
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(seg / max_seg)))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def smooth_polyline(
    points: Polyline,
    corner_deg: float = DEFAULT_CORNER_DEG,
    samples: int = DEFAULT_SAMPLES,
    max_seg_mm: float = DEFAULT_MAX_SEG_MM,
    simplify_mm: float = 0.0,
) -> Polyline:
    """
    完整流程：切尖角 -> 分段样条平滑 -> 按弧长重采样 ->（可选）抽稀。

    simplify_mm > 0 时在最后做一次 RDP 抽稀，把重采样产生的冗余点压掉，
    让 G-code 短一些。写字场景 0.02mm 的容差肉眼看不出来。
    """
    if len(points) < 3:
        return list(points)

    runs = split_at_corners(points, corner_deg)

    merged: Polyline = []
    for run in runs:
        seg = _smooth_run(run, samples)
        # 分段之间共享端点，拼接时跳过重复的第一个点
        merged.extend(seg if not merged else seg[1:])

    merged = resample(merged, max_seg_mm)

    if simplify_mm > 0 and len(merged) > 2:
        merged = simplify_rdp(merged, simplify_mm)

    return merged


def simplify_rdp(points: Polyline, tolerance: float) -> Polyline:
    """
    Ramer-Douglas-Peucker 抽稀。递归保留偏离直线超过 tolerance 的点。

    用迭代而不是递归实现 —— 一条笔画成千上万个点时，Python 的递归深度
    会爆。
    """
    if len(points) < 3 or tolerance <= 0:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(points) - 1)]

    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)

        far_idx, far_dist = -1, -1.0
        for k in range(i + 1, j):
            px, py = points[k]
            if norm < _EPS:
                d = math.hypot(px - ax, py - ay)
            else:
                # 点到直线 ab 的距离
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > far_dist:
                far_idx, far_dist = k, d

        if far_dist > tolerance and far_idx > 0:
            keep[far_idx] = True
            stack.append((i, far_idx))
            stack.append((far_idx, j))

    return [p for p, k in zip(points, keep) if k]


def smooth_strokes(strokes, **kwargs):
    """
    对一批 Stroke 逐条平滑。返回新的 Stroke 列表，不修改输入。

    role 等属性原样带过去 —— 平滑只改几何。
    """
    from .models import Stroke  # 放在这里避免循环导入

    out = []
    for s in strokes:
        pts = smooth_polyline(s.points, **kwargs)
        out.append(Stroke(points=pts, role=s.role))
    return out
