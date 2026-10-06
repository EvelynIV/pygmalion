"""
手写感引擎 —— 给规整的矢量笔画加上"人写的"那种自然误差。

原理说明
--------
商业软件所谓的"手写字体"，效果来自两层误差的叠加（这一点从 kdraw 的
FontsSetData.json 参数名可以反推出来：randomRotate / randomXScale /
randomFontSize / randomY / skeletonStrength / angleStrength / lengthStrength）：

  第一层 —— 字级别的整体变换
      每个字被轻微旋转、非等比缩放、上下左右平移。人写字时每个字的大小、
      倾斜、位置都不完全一致，这一层制造的就是这种"整字抖动"。

  第二层 —— 笔画级别的骨架扰动
      笔画本身不是数学直线，会轻微弯曲、抖动。这一层让每一笔的内部路径
      产生平滑的偏移，而不是整体平移。

关键点是"平滑"：随机数如果逐点独立，得到的是锯齿噪声，看起来像信号干扰
而不像手写。手写的抖动在空间上是连续的，所以这里用多个正弦波叠加
（value noise 的做法）生成平滑的偏移场。

可复现性
--------
给定同一个种子，输出完全一致。这样同一首诗重写一遍能拿到一模一样的
G-code（方便补写、对齐），换一个种子就是另一份"笔迹"。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import math
import random
from dataclasses import dataclass

Point = tuple[float, float]
Polyline = list[Point]


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------


@dataclass
class JitterConfig:
    """手写抖动的强度参数。全部为 0 时输出是规整的印刷体。"""

    enabled: bool = True

    # 字级别
    rotate_deg: float = 1.5  # 旋转的标准差（度）。超过 5 度就很潦草了
    scale_jitter: float = 0.025  # 缩放的标准差（比例）。0.025 = ±2.5%
    pos_jitter_mm: float = 0.3  # 水平位置抖动标准差（毫米）
    baseline_jitter_mm: float = 0.4  # 基线（垂直）抖动标准差（毫米）

    # 笔画级别
    # 骨架扰动是"最显眼"的参数，因为它直接把笔画推歪。经验值：
    #  0.02~0.04  自然的手写颤笔，笔画仍连得住
    #  0.10 以上   笔画明显散架，汉字会认不出来
    skeleton_strength: float = 0.035  # 骨架扰动强度，相对字号的倍数
    skeleton_octaves: int = 2  # 噪声叠加层数，越多细节越碎
    endpoint_taper: float = 0.30  # 笔画两端收缩比例，防止连笔处裂开

    seed: int | None = None


@dataclass
class GlyphTransform:
    """单个字的仿射变换：先绕字形中心旋转缩放，再平移到目标位置。"""

    angle_rad: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    dx: float = 0.0
    dy: float = 0.0


# --------------------------------------------------------------------------
# 平滑噪声
# --------------------------------------------------------------------------


def smooth_noise(t: float, rng: random.Random, octaves: int = 3) -> float:
    """
    在 t ∈ [0, 1] 上生成一个平滑、连续、随机的值，范围大约 [-1, 1]。

    做法是把若干个频率倍增、振幅减半的正弦波叠加（每个波的相位随机）。
    低频决定整体的流动方向，高频决定细节的细碎程度。
    逐点独立随机会得到锯齿，那不是手写抖动，是信号噪声。
    """
    if octaves <= 0:
        return 0.0

    value = 0.0
    amplitude = 1.0
    frequency = 1.0
    total_amplitude = 0.0

    for _ in range(octaves):
        phase = rng.uniform(0.0, 2.0 * math.pi)
        value += amplitude * math.sin(2.0 * math.pi * frequency * t + phase)
        total_amplitude += amplitude
        amplitude *= 0.5
        frequency *= 2.0

    return value / total_amplitude if total_amplitude else 0.0


def _taper_weight(t: float, taper: float) -> float:
    """
    端点收缩窗。t ∈ [0,1] 是点在笔画上的归一化位置。

    返回 0 表示该点不动。笔画两端权重趋近 0，中间为 1 —— 这样扰动不会把
    笔画端点推走，避免本来相接的笔画之间裂开一道缝。
    """
    if taper <= 0.0:
        return 1.0
    taper = min(taper, 0.49)
    if t < taper:
        return _smoothstep(t / taper)
    if t > 1.0 - taper:
        return _smoothstep((1.0 - t) / taper)
    return 1.0


def _smoothstep(x: float) -> float:
    """3x² - 2x³，两端导数为 0，接缝处不会有折角。"""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


# --------------------------------------------------------------------------
# 笔画扰动
# --------------------------------------------------------------------------


def _polyline_length(points: Polyline) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _cumulative_lengths(points: Polyline) -> list[float]:
    """每个点距离笔画起点的累计弧长。用来把点映射到 [0,1] 参数空间。"""
    out = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        out.append(out[-1] + math.hypot(x1 - x0, y1 - y0))
    return out


def perturb_stroke(
    points: Polyline,
    rng: random.Random,
    strength: float,
    octaves: int = 3,
    taper: float = 0.25,
) -> Polyline:
    """
    给一条笔画加骨架扰动：把每个点沿**垂直于笔画方向**偏移一段平滑随机的距离。

    沿法线方向偏移（而不是在 XY 上分别加噪声）是关键 —— 这样笔画是"弯了"，
    而不是"毛了"。加在切向上会改变笔画的长度分布，看起来像拉伸变形。

    端点（含中间断笔的端点）会被 taper 窗收住，保证笔画之间不断开。

    参数里的 strength 单位与输入坐标相同（毫米）。
    """
    if len(points) < 2 or strength <= 0.0:
        return list(points)

    total_len = _polyline_length(points)
    if total_len <= 1e-9:
        return list(points)

    cumulative = _cumulative_lengths(points)
    out: Polyline = []

    for i, (x, y) in enumerate(points):
        # 用前后点估切线方向；端点用单侧差分
        prev_i = max(0, i - 1)
        next_i = min(len(points) - 1, i + 1)
        tx = points[next_i][0] - points[prev_i][0]
        ty = points[next_i][1] - points[prev_i][1]
        seg_len = math.hypot(tx, ty)

        if seg_len <= 1e-9:
            out.append((x, y))
            continue

        # 法线 = 切线逆时针转 90 度
        nx, ny = -ty / seg_len, tx / seg_len

        t = cumulative[i] / total_len
        weight = _taper_weight(t, taper)
        offset = smooth_noise(t, rng, octaves) * strength * weight

        out.append((x + nx * offset, y + ny * offset))

    return out


# --------------------------------------------------------------------------
# 字级别变换
# --------------------------------------------------------------------------


def make_transform(rng: random.Random, cfg: JitterConfig) -> GlyphTransform:
    """为单个字抽一组随机变换参数。"""
    return GlyphTransform(
        angle_rad=math.radians(rng.gauss(0.0, cfg.rotate_deg)),
        scale_x=1.0 + rng.gauss(0.0, cfg.scale_jitter),
        scale_y=1.0 + rng.gauss(0.0, cfg.scale_jitter),
        dx=rng.gauss(0.0, cfg.pos_jitter_mm),
        dy=rng.gauss(0.0, cfg.baseline_jitter_mm),
    )


def apply_transform(
    points: Polyline,
    tf: GlyphTransform,
    origin: Point = (0.0, 0.0),
) -> Polyline:
    """
    对一条笔画施加字级别的仿射变换。

    origin 是旋转缩放的支点，一般传字形中心 —— 绕中心转才像"这个字歪了"，
    绕左下角转会让字整个甩出去。
    """
    ox, oy = origin
    cos_a, sin_a = math.cos(tf.angle_rad), math.sin(tf.angle_rad)
    out: Polyline = []

    for x, y in points:
        # 移到支点坐标系
        px, py = x - ox, y - oy
        # 缩放
        px *= tf.scale_x
        py *= tf.scale_y
        # 旋转
        rx = px * cos_a - py * sin_a
        ry = px * sin_a + py * cos_a
        # 移回并加上抖动位移
        out.append((rx + ox + tf.dx, ry + oy + tf.dy))

    return out


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def jitter_glyph(
    polylines: list[Polyline],
    cfg: JitterConfig,
    rng: random.Random,
    origin: Point = (0.0, 0.0),
    font_size_mm: float = 12.0,
) -> list[Polyline]:
    """
    对一个字的全部笔画做完整的抖动处理。

    polylines 是该字已经缩放并摆到目标位置的笔画（单位毫米），
    origin 是字形中心。返回同样结构的、已经抖过的笔画。

    两层误差的施加顺序是"先骨架、后整体"：骨架扰动在字自身的局部坐标里做，
    再整体旋转平移。反过来做的话，旋转会把噪声方向也转过去，效果不对。
    """
    if not cfg.enabled or not polylines:
        return [list(pl) for pl in polylines]

    strength = cfg.skeleton_strength * font_size_mm

    # 第一层：笔画级骨架扰动
    wobbled = [
        perturb_stroke(pl, rng, strength, cfg.skeleton_octaves, cfg.endpoint_taper)
        for pl in polylines
    ]

    # 第二层：字级整体变换
    tf = make_transform(rng, cfg)
    return [apply_transform(pl, tf, origin) for pl in wobbled]


def make_rng(seed: int | None) -> random.Random:
    """构造随机数发生器。seed=None 时每次都不一样，否则完全可复现。"""
    return random.Random(seed)
