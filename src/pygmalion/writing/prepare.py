from __future__ import annotations
import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from ..config import CONFIG, OUTPUT_DIR, ROOT
from ..fonts import sources
from ..machine import gcode
from . import layout, models
from .handwriting import JitterConfig
from .smooth import smooth_strokes
from .render import render_glyphs
from .preview import strokes_to_svg


def build_configs(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    从 config.json 装配各模块的配置对象。

    overrides 允许单次打印覆盖排版参数（例如用户在提交时选了不同方向或字号），
    不写回配置文件。
    """
    ov = overrides or {}
    m, p, l, h = (
        CONFIG["machine"],
        CONFIG["page"],
        CONFIG["layout"],
        CONFIG["handwriting"],
    )

    page = models.PageConfig(
        width_mm=p["width_mm"],
        height_mm=p["height_mm"],
        margin_top_mm=p["margin_top_mm"],
        margin_right_mm=p["margin_right_mm"],
        margin_bottom_mm=p["margin_bottom_mm"],
        margin_left_mm=p["margin_left_mm"],
    )
    lay = models.LayoutConfig(
        font_size_mm=float(ov.get("font_size_mm", l["font_size_mm"])),
        char_gap_mm=float(ov.get("char_gap_mm", l["char_gap_mm"])),
        line_gap_mm=float(ov.get("line_gap_mm", l["line_gap_mm"])),
        title_scale=float(ov.get("title_scale", l["title_scale"])),
        align=ov.get("align", l["align"]),
    )
    mach = gcode.MachineConfig(
        port=m["port"],
        baud=m["baud"],
        z_on=float(ov.get("z_on", m["z_on"])),
        z_off=float(ov.get("z_off", m["z_off"])),
        z_speed=float(ov.get("z_speed", m["z_speed"])),
        feed_rate=float(ov.get("feed_rate", m["feed_rate"])),
        jog_speed=float(ov.get("jog_speed", m["jog_speed"])),
        stroke_delay=float(ov.get("stroke_delay", m.get("stroke_delay", 0.0))),
        table_segment_mm=float(m.get("table_segment_mm", 5.0)),
        # 坐标转换参数。这几项必须靠标定确定，不要凭猜。
        swap_xy=p["axes_swapped"],
        invert_x=p.get("invert_x", True),
        invert_y=p.get("invert_y", False),
        origin_corner="左上"
        if p.get("fixed_grid_origin", False)
        else p.get("origin_corner", "左上"),
        offset_x=p.get("offset_x", 0.0),
        offset_y=p.get("offset_y", 0.0),
        fixed_grid_origin=bool(p.get("fixed_grid_origin", False)),
        paper_offset_x_mm=float(p.get("paper_offset_x_mm", 0.0)),
        paper_offset_y_mm=float(p.get("paper_offset_y_mm", 0.0)),
    )
    if mach.fixed_grid_origin:
        # 页面 x/y 和刻度区 x/y 都是“右、下为正”。先用已经标定过的
        # PageToMachine 求两点差，再叠加到机器偏移；不把纸张位置硬编码为
        # 某一根轴的正负方向，轴对调/翻转配置改变时仍然正确。
        tf = gcode.PageToMachine(mach, page)
        mx0, my0 = tf(0.0, 0.0)
        mx1, my1 = tf(mach.paper_offset_x_mm, mach.paper_offset_y_mm)
        mach.offset_x += mx1 - mx0
        mach.offset_y += my1 - my0

    # 手写感强度：界面上给一个 0~2 的倍数，1.0 是配置里的原始强度。
    # 这是个比逐项改 rotate/scale/skeleton 更直观的旋钮 —— 用户想的是
    # "再潦草一点/再规整一点"，不是"把 skeleton_strength 从 0.035 调到 0.05"。
    strength = float(ov.get("handwriting_strength", 1.0))
    strength = max(0.0, min(3.0, strength))

    jit = JitterConfig(
        enabled=bool(ov.get("handwriting", h["enabled"])),
        rotate_deg=h["rotate_deg"] * strength,
        scale_jitter=h["scale_jitter"] * strength,
        pos_jitter_mm=h["pos_jitter_mm"] * strength,
        baseline_jitter_mm=h["baseline_jitter_mm"] * strength,
        skeleton_strength=h["skeleton_strength"] * strength,
        skeleton_octaves=h.get("skeleton_octaves", 2),
        endpoint_taper=h.get("endpoint_taper", 0.30),
        seed=ov.get("seed", h.get("seed")),
    )
    q = CONFIG.get("stroke_quality", {})
    quality = {
        "enabled": bool(q.get("enabled", True)),
        "corner_deg": float(q.get("corner_deg", 45.0)),
        "samples": int(q.get("samples", 4)),
        "max_seg_mm": float(q.get("max_seg_mm", 0.4)),
        "simplify_mm": float(q.get("simplify_mm", 0.02)),
    }
    return {
        "page": page,
        "layout": lay,
        "machine": mach,
        "jitter": jit,
        "quality": quality,
        # 字库：None 表示用默认字库。投稿时选的那个由 prepare() 合并进来，
        # build_configs() 只看 overrides，不关心数据从哪来。
        "font": ov.get("font") or None,
    }


# --------------------------------------------------------------------------
# 固定刻度原点的打印前安全检查
# --------------------------------------------------------------------------


def grid_calibration_error() -> str | None:
    """纸张越过固定写字盘范围时返回原因；预览不受这个检查限制。"""
    p = CONFIG["page"]
    if not p.get("fixed_grid_origin", False):
        return None
    grid_w = float(p.get("grid_width_mm", 190.0))
    grid_h = float(p.get("grid_height_mm", 280.0))
    paper_w = float(p["width_mm"])
    paper_h = float(p["height_mm"])
    paper_x = float(p.get("paper_offset_x_mm", 0.0))
    paper_y = float(p.get("paper_offset_y_mm", 0.0))
    min_x = float(p.get("paper_min_x_mm", -100.0))
    min_y = float(p.get("paper_min_y_mm", -50.0))
    if (
        paper_x < min_x
        or paper_y < min_y
        or paper_x + paper_w > grid_w + 1e-6
        or paper_y + paper_h > grid_h + 1e-6
    ):
        return (
            "纸张位置校准无效：纸张超出可写盘面。请在管理页「纸张位置校准」中调整位置。"
        )
    return None


# --------------------------------------------------------------------------
# 准备（离线）
# --------------------------------------------------------------------------


@dataclass
class Prepared:
    """一首诗排版渲染后的全部产物。不含任何机器状态。"""

    submission_id: int
    poem: models.Poem
    gcode: list[str]
    svg: str
    strokes: int
    cells: int
    font_size_mm: float  # 实际用的字号（装不下时可能比要求的小）
    requested_size: float = 0.0  # 用户要求的字号，用来判断有没有被缩
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gcode_path: str = ""
    machine_snapshot: dict[str, Any] = field(default_factory=dict)
    font: str = ""  # 实际选用的字库 id
    layout: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    blocked: bool = False  # 通用单页入口：缺字或超出纸面时禁止落笔

    @property
    def shrunk(self) -> bool:
        """
        字号是否被自动缩小过。

        内容装不下时 fit_font_size() 会自动缩字号，这本身是对的（否则字会
        写出版面）。但**不能悄悄缩** —— 用户明确要了 20mm 却拿到 18.4mm
        而不自知，会以为程序算错了。界面拿这个标志去提示。
        """
        return self.font_size_mm < self.requested_size - 0.01

    @property
    def can_print(self) -> bool:
        """
        能否送去写。缺失字符不阻止打印 —— 用户可能明知某个生僻字写不出来
        也愿意先写，界面上给提示即可。真正不能打的是"一个字都画不出来"。
        """
        return self.strokes > 0 and not self.blocked


def _glyph_source(font: str | None = None) -> sources.CompositeSource:
    """
    组装字形来源。font 是选定的中文字库 id，None 用默认（宋体/Arphic）。
    选定字库排在链首，其余字库在后面兜底，生僻字照样写得出来。
    """
    return sources.source_from_dir(ROOT, font=font)


def prepare(
    submission: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    save_gcode: bool = True,
    fast_preview: bool = False,
) -> Prepared:
    """
    把一条投稿排版、渲染、生成 G-code。纯离线，不碰机器。
    """
    # 字库优先级：本次调用显式指定 > 投稿时选定的 > 默认。
    # 合并放在这里而不是 build_configs 里，因为后者只该处理"参数"，
    # 不该知道投稿记录长什么样。
    ov = dict(overrides or {})
    ov.setdefault("seed", submission["id"])
    if not ov.get("font") and submission.get("font"):
        ov["font"] = submission["font"]

    cfg = build_configs(ov)
    page, lay, mach, jit = cfg["page"], cfg["layout"], cfg["machine"], cfg["jitter"]
    quality = cfg["quality"]

    # 方向优先级：本次调用显式指定 > 投稿时选定的 > 配置文件默认
    direction = (
        ov.get("direction")
        or submission.get("direction")
        or CONFIG["layout"]["direction"]
    )

    poem = models.Poem(
        body=submission["body"],
        title=submission.get("title", "") or "",
        author=submission.get("author", "") or "",
        direction=direction,
        # 本次管理端选择优先于投稿者原始选择；任务会冻结 ov，故预览与实际书写一致。
        colophon=ov.get("colophon") or submission.get("colophon", "none") or "none",
        dedication=submission.get("signature", "") or "",
    )

    # 装不下就自动缩字号
    requested_size = lay.font_size_mm
    lay = layout.fit_font_size(poem, page, lay)

    placed = layout.layout_poem(poem, page, lay)
    source = _glyph_source(cfg["font"])
    strokes, missing = render_glyphs(placed, source, jit)
    if quality["enabled"]:
        # 平滑在页面坐标中完成；预览与机器落笔因此共用同一条笔画。
        strokes = smooth_strokes(
            strokes,
            corner_deg=quality["corner_deg"],
            samples=quality["samples"],
            max_seg_mm=quality["max_seg_mm"],
            simplify_mm=quality["simplify_mm"],
        )

    program = gcode.strokes_to_gcode(strokes, page, mach)
    svg = strokes_to_svg(strokes, page)
    from ..fonts import registry as font_registry

    actual_font = cfg["font"] or font_registry.default_id(ROOT)

    result = Prepared(
        submission_id=submission["id"],
        poem=poem,
        gcode=program,
        svg=svg,
        strokes=len(strokes),
        cells=len(placed),
        font_size_mm=lay.font_size_mm,
        requested_size=requested_size,
        missing=missing,
        warnings=source.warnings,
        font=actual_font,
        machine_snapshot=copy.deepcopy(CONFIG["machine"]),
    )

    if save_gcode:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = OUTPUT_DIR / f"sub{submission['id']}-{stamp}.gcode"
        path.write_text("\n".join(program), encoding="utf-8")
        result.gcode_path = str(path)

    return result
