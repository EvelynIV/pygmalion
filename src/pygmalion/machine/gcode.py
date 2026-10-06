from __future__ import annotations
import math
from ..writing.models import PageConfig, Stroke
from .coordinates import MachineConfig, PageToMachine


def order_strokes(
    strokes: list[Stroke], start: tuple[float, float] = (0.0, 0.0)
) -> list[Stroke]:
    """
    最近邻法重排笔画以减少空走距离。

    ⚠️ **不要对文字内容使用这个函数。** 它对非文字的矢量图案（单幅图形、
    装饰纹样）是有效的，但对汉字/文字是错的，会同时造成两个可见的错误：

    1. **打散字的分组。** 它纯粹按几何距离找下一条最近的笔画，会把一个字
       内部的笔画拆到不同的时间点去写。实测一首 14 字的诗会产生 49 次
       "跳回已经写过的字再补笔画"，纸面上看就是笔来回横跳。

    2. **反向写笔画。** 为了就近起笔，它会把整条笔画反过来画（见下面
       reversed 那段）。汉字有笔顺 —— 横该从左到右、撇该从上到下，
       反着写就是"倒笔画"，完全不是那个字。

    按排版顺序依次写完每个字才是正确做法，而且空走并不长：相邻的字只有
    十几毫米，抬笔空走本来就很快。这个"优化"对文字是负收益。

    所以 strokes_to_gcode() 的 optimize_order 默认为 False。这个函数保留
    下来是给非文字内容用的。
    """
    if len(strokes) <= 2:
        return list(strokes)

    remaining = list(strokes)
    out: list[Stroke] = []
    cx, cy = start

    while remaining:
        best_i, best_d = 0, float("inf")
        for i, s in enumerate(remaining):
            if not s.points:
                continue
            hx, hy = s.points[0]
            tx, ty = s.points[-1]
            d = min(math.hypot(hx - cx, hy - cy), math.hypot(tx - cx, ty - cy))
            if d < best_d:
                best_i, best_d = i, d

        chosen = remaining.pop(best_i)
        if chosen.points:
            cx, cy = chosen.points[-1]
        out.append(chosen)

    return out


# --------------------------------------------------------------------------
# G-code 生成
# --------------------------------------------------------------------------


def strokes_to_gcode(
    strokes: list[Stroke],
    page: PageConfig,
    machine: MachineConfig,
    optimize_order: bool = False,
    precision: int = 3,
) -> list[str]:
    """
    把笔画列表转成 G-code 行。

    生成的程序结构：
      G21 G90           毫米、绝对坐标
      G0 Z<抬笔>        先抬笔，避免上电后第一下就划纸
      ... 每条笔画：抬笔 -> 空走到起点 -> 落笔 -> 连线 -> 抬笔 ...
      G0 Z<抬笔>        收尾抬笔
      G0 X0 Y0          回原点
      M2                程序结束
    """
    tf = PageToMachine(machine, page)

    if optimize_order:
        strokes = order_strokes(strokes)

    f = lambda v: f"{v:.{precision}f}".rstrip("0").rstrip(".")

    lines: list[str] = [
        "; 由 poem-writer 生成",
        f"; 页面 {page.width_mm:g} x {page.height_mm:g} mm，共 {len(strokes)} 条笔画",
        f"; 坐标转换: {tf.describe()}",
        "G21",  # 毫米
        "G90",  # 绝对坐标
        f"G0 Z{f(machine.z_off)}",  # 先抬笔
    ]

    for s in strokes:
        pts = s.points
        if len(pts) < 2:
            # 单点：落笔点一下就走，用来画点号之类的
            if pts:
                mx, my = tf(*pts[0])
                lines.append(f"G0 X{f(mx)} Y{f(my)}")
                lines.append(f"G1 Z{f(machine.z_on)} F{f(machine.z_speed)}")
                lines.append(f"G0 Z{f(machine.z_off)}")
            continue

        mx, my = tf(*pts[0])
        lines.append(f"G0 X{f(mx)} Y{f(my)}")
        lines.append(f"G1 Z{f(machine.z_on)} F{f(machine.z_speed)}")

        # 连续线段。同一行的 XY 用一条 G1 连出去，F 只在需要时给
        for px, py in pts[1:]:
            mx, my = tf(px, py)
            lines.append(f"G1 X{f(mx)} Y{f(my)} F{f(machine.feed_rate)}")

        lines.append(f"G0 Z{f(machine.z_off)}")

        # 每笔之后的停顿。G4 P 的单位是秒，GRBL 1.1 支持小数。
        # 放在抬笔之后、下一笔空走之前 —— 这段时间机器静止，正好让墨水定住。
        if machine.stroke_delay > 0:
            lines.append(f"G4 P{f(machine.stroke_delay)}")

    lines += [
        f"G0 Z{f(machine.z_off)}",
        "G0 X0 Y0",
        "M2",
    ]
    return lines


# --------------------------------------------------------------------------
# 标定图案
# --------------------------------------------------------------------------


def calibration_pattern(page: PageConfig, machine: MachineConfig) -> list[str]:
    """
    生成标定用的测试图案。用来确定 swap_xy / invert_x / invert_y 三个参数。

    图案说明（在**页面坐标**下的设计）：
      左下角一个小十字（纸面方位标记，不代表当前工作原点）
      整个可用区域画一个大方框
      方框的左上角标"1"，右上角标"2"，右下角"3"，左下角"4"

    跑完后看纸上的实际结果：
      - 如果方框画出来是竖长条（而不是横着的），说明 swap_xy 设错了
      - 如果"1"出现在纸的右下角而不是左上角，说明原点方向反了
      - 数字编号能告诉你翻转该怎么调

    安全提醒：第一次跑务必**取下笔**或把笔抬离纸面，只观察机器的运动方向。
    """
    m = page.margin_left_mm
    w = page.usable_w
    h = page.usable_h
    top = page.margin_top_mm
    right = m + w
    bottom = top + h

    strokes: list[Stroke] = []

    # 大方框
    strokes.append(
        Stroke(
            [(m, top), (right, top), (right, bottom), (m, bottom), (m, top)],
            role="calib",
        )
    )
    # 对角线：一眼看出有没有被翻转
    strokes.append(Stroke([(m, top), (right, bottom)], role="calib"))

    # 四角标记：角上的小十字，尺寸随页面自适应
    arm = min(w, h) * 0.04
    for cx, cy in ((m, top), (right, top), (right, bottom), (m, bottom)):
        strokes.append(Stroke([(cx - arm, cy), (cx + arm, cy)], role="calib"))
        strokes.append(Stroke([(cx, cy - arm), (cx, cy + arm)], role="calib"))

    # 不排序，保持可预测的绘制顺序
    return strokes_to_gcode(strokes, page, machine, optimize_order=False)


# --------------------------------------------------------------------------
# 预览
# --------------------------------------------------------------------------
