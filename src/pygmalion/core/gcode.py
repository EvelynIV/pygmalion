"""
G-code 生成与坐标转换。

安全提示
--------
本模块只**生成** G-code 文本，不碰串口。要把文本发给机器，必须经过
grbl.py 的 GrblLink.run_program()，那里有"必须先备份"的闸门。

坐标转换为什么单独拎出来
------------------------
页面坐标（原点左上、x 右、y 下，毫米）和机器坐标是两回事。你的机器
AxesData.json 里 revxy=true，说明 X/Y 两轴是对调的；原点在哪一角的
含义也跟着变。这个映射一旦搞错，轻则写歪、重则撞限位。

所以这里把它做成一个显式的、可配置的仿射变换，并且提供一个**标定图案**
（calibration_pattern）——先用笔尖悬空跑一遍，看机器实际动的方向，
把参数填对，之后就不用再动。不要凭猜测设这个参数。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import math
from dataclasses import dataclass

from .layout import PageConfig, Stroke


# --------------------------------------------------------------------------
# 机器配置与坐标转换
# --------------------------------------------------------------------------


@dataclass
class MachineConfig:
    """机器参数。默认值来自 kdraw 的 Grbl.json / MachineControllerData.json。"""

    port: str = "auto"
    baud: int = 115200

    # 抬落笔（Z 轴步进）
    z_on: float = 2.0  # 落笔高度 mm（值越大压得越深）
    z_off: float = 0.0  # 抬笔高度 mm
    z_speed: float = 3000.0  # 抬落笔速度 mm/min

    # 书写速度
    feed_rate: float = 3000.0
    jog_speed: float = 3000.0  # 空走速度

    # 每写完一笔之后停顿多久（秒），0 = 不停。
    # 用途：笔画密的地方连续快速落笔，墨水来不及润开、纸面来不及吃墨，
    # 会出现起笔断线或墨团。停一下让墨水跟上。代价是整体变慢。
    stroke_delay: float = 0.0

    # ---- 坐标转换（务必标定后再用）----
    swap_xy: bool = True  # 对应 revxy=true
    invert_x: bool = True  # 机器 X+ 朝纸张上方 → 纵向要取反
    invert_y: bool = False  # 机器 Y+ 朝纸张右方
    # 普通模式下把纸张指定角设为原点；固定刻度模式由 fixed_grid_origin 覆盖。
    origin_corner: str = "左上"
    offset_x: float = 0.0  # 额外的微调偏移
    offset_y: float = 0.0

    # 固定工作区原点模式：G92 永远设在刻度区域左上角，纸张位置另算。
    fixed_grid_origin: bool = False
    paper_offset_x_mm: float = 0.0
    paper_offset_y_mm: float = 0.0


class PageToMachine:
    """
    页面坐标 -> 机器坐标。

    变换顺序：先对调 XY（如果需要），再翻转，最后加偏移。
    这些参数**必须靠标定确定**，不能猜。用 calibration_pattern() 跑一遍：
      1. 取笔（笔尖悬空或把笔抬起）
      2. 跑标定程序，观察机器往哪个方向走
      3. 对照纸面上画出的图案，调整 swap_xy / invert_x / invert_y
    """

    def __init__(self, cfg: MachineConfig, page: PageConfig):
        self.cfg = cfg
        self.page = page

    # 页面四角的页面坐标。key 与 config.json 里的 origin_corner 对应。
    CORNERS: dict[str, tuple[float, float]] = {
        "左上": (0.0, 0.0),
        "右上": (1.0, 0.0),  # 占位，实际按页面尺寸算，见 corner_point()
        "左下": (0.0, 1.0),
        "右下": (1.0, 1.0),
    }

    def corner_point(self, name: str) -> tuple[float, float]:
        """角名 -> 页面坐标（毫米）。"""
        fx, fy = self.CORNERS.get(name, self.CORNERS["左上"])
        return fx * self.page.width_mm, fy * self.page.height_mm

    def _raw(self, x: float, y: float) -> tuple[float, float]:
        """
        只做轴对调与方向翻转，不平移 —— 原点还没定。

        翻转时用的跨度必须是**被映射的那个页面轴的跨度**，不是机器轴的。
        swap_xy 时机器 X 承载的是页面的纵向跨度（height），如果误用
        width 去翻，纵向会整整错 90mm（290-200）—— 纸上表现为整体偏移。
        """
        if self.cfg.swap_xy:
            mx, my = y, x
            mx_span = self.page.height_mm  # 机器 X 承载页面纵向
            my_span = self.page.width_mm  # 机器 Y 承载页面横向
        else:
            mx, my = x, y
            mx_span = self.page.width_mm
            my_span = self.page.height_mm

        if self.cfg.invert_x:
            mx = mx_span - mx
        if self.cfg.invert_y:
            my = my_span - my
        return mx, my

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        """
        页面坐标 -> 机器坐标。

        三步：轴对调/翻转 -> 把选定的那个角平移到 (0,0) -> 加微调偏移。

        原点角为什么做成参数：机器 X+ 朝纸张上方（实测标定结果），所以
        "从页面顶部往底部走"等于机器 X 减小。这意味着原点放左上角还是
        左下角，会让整张纸落在正半轴还是负半轴：

            笔尖放左下角 -> 页面占机器 X ∈ [0, +H]
            笔尖放左上角 -> 页面占机器 X ∈ [-H, 0]

        两种都要页面纵向跨度对应的行程，只是方向不同。机器没有限位、软限位也关着，
        负坐标完全能用 —— 实际要求只是那个方向上有足够行程。所以做成
        可配的，由用户按自己机器的空间决定。
        """
        mx, my = self._raw(x, y)

        # 把选定角挪到原点：先算出那个角落在上面的变换里的位置，再整体减去
        cx, cy = self.corner_point(self.cfg.origin_corner)
        cxm, cym = self._raw(cx, cy)

        return (mx - cxm + self.cfg.offset_x, my - cym + self.cfg.offset_y)

    def describe(self) -> str:
        """人类可读的转换说明，显示在界面上让人一眼看出当前标定状态。"""
        if self.cfg.swap_xy:
            parts = ["页面 X → 机器 Y", "页面 Y → 机器 X"]
        else:
            parts = ["页面 X → 机器 X", "页面 Y → 机器 Y"]
        if self.cfg.invert_x:
            span = self.page.height_mm if self.cfg.swap_xy else self.page.width_mm
            parts.append(f"机器 X 翻转（以 {span:g}mm 为基准取反）")
        if self.cfg.invert_y:
            span = self.page.width_mm if self.cfg.swap_xy else self.page.height_mm
            parts.append(f"机器 Y 翻转（以 {span:g}mm 为基准取反）")
        if self.cfg.fixed_grid_origin:
            parts.append("原点在刻度区域左上角")
            parts.append(
                f"纸张左上偏移 ({self.cfg.paper_offset_x_mm:+.1f}, "
                f"{self.cfg.paper_offset_y_mm:+.1f}) mm"
            )
        else:
            parts.append(f"原点在纸张{self.cfg.origin_corner}角")
        if self.cfg.offset_x or self.cfg.offset_y:
            parts.append(
                f"微调 ({self.cfg.offset_x:+.1f}, {self.cfg.offset_y:+.1f}) mm"
            )
        return "；".join(parts)

    def page_origin_hint(self) -> str:
        """
        告诉操作者该把笔尖放在纸的哪个角。

        机器没有限位也不归零，工作原点就是笔尖当前位置，所以"笔放在哪个角"
        是由配置里的 origin_corner 决定的 —— 直接报出来，不要让操作者猜。
        """
        if self.cfg.fixed_grid_origin:
            return "把笔尖放在刻度区域的「左上」角，然后把它设为零点"
        return f"把笔尖放在纸张的「{self.cfg.origin_corner}」角，然后把它设为零点"

    def corner_map(self) -> dict[str, tuple[float, float]]:
        """页面四角映射到的机器坐标。用来核对整张纸落在机器的哪个范围内。"""
        return {
            name: self(*self.corner_point(name))
            for name in ("左上", "右上", "左下", "右下")
        }

    def bounds(self) -> tuple[float, float, float, float]:
        """
        整张纸占用的机器坐标范围 (x_min, y_min, x_max, y_max)。

        启动书写前用它检查是否超出机器行程 —— 尤其是把原点改到左上角之后，
        坐标会变成负数，得确认那个方向确实有足够行程。
        """
        pts = list(self.corner_map().values())
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------
# 笔画排序
# --------------------------------------------------------------------------


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


def strokes_to_svg(
    strokes: list[Stroke],
    page: PageConfig,
    color: str = "#1a1a1a",
    bg: str = "#fdfcf8",
) -> str:
    """
    把笔画渲染成 SVG，用于在网页上预览排版效果。

    SVG 的坐标系本来就是 y 向下、原点左上，和页面坐标一致，所以这里不需要
    任何转换 —— 直接用页面坐标当 SVG 坐标。viewBox 用毫米为单位，
    纸张尺寸和实际输出严格对应，预览里量出来的位置就是纸上的位置。
    """
    w, h = page.width_mm, page.height_mm

    # 落笔前的空走路径不画；这里只画实际有墨的线条
    paths: list[str] = []
    for s in strokes:
        pts = s.points
        if len(pts) < 2:
            continue
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
        paths.append(f'<path d="{d}"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}"
     width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
  <rect x="0" y="0" width="{w:g}" height="{h:g}" fill="{bg}"/>
  <g fill="none" stroke="{color}" stroke-width="0.35"
     stroke-linecap="round" stroke-linejoin="round">
    {"".join(paths)}
  </g>
</svg>"""
