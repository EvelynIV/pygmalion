from __future__ import annotations
from dataclasses import dataclass
from ..writing.models import PageConfig


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
    table_segment_mm: float = 5.0  # 表格框线每次连续落笔的最大长度

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
