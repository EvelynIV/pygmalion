from __future__ import annotations
from .handwriting import JitterConfig, jitter_glyph, make_rng
from .models import PlacedGlyph, Stroke, ROLE_SEAL
from ..fonts.sources import CompositeSource, Polyline


def render_glyphs(
    placed: list[PlacedGlyph],
    source: CompositeSource,
    jitter: JitterConfig | None = None,
) -> tuple[list[Stroke], list[str]]:
    """
    把排好版的字渲染成笔画。

    返回 (笔画列表, 缺失字符列表)。缺失字符不静默吞掉 —— 界面必须告诉用户
    "这几个字写不出来"，否则用户会拿到一张缺字的纸却不知道为什么。

    抖动的随机种子对每个字独立：用字在页面上的格子位置做种子的一部分，
    这样同一个字在不同位置有不同的抖动，而在同一位置永远是同一份抖动
    （重印能得到一致的笔迹）。
    """
    strokes: list[Stroke] = []
    missing_set: dict[str, None] = {}
    source.prepare_chars("".join(g.char for g in placed))

    for i, g in enumerate(placed):
        if g.char.isspace():
            continue

        if not source.supports(g.char):
            missing_set.setdefault(g.char, None)
            continue

        base = source.strokes(g.char)
        if not base:
            continue

        # 缩放并平移到字格中心
        scaled: list[Polyline] = [
            [(x * g.size * g.scale_x + g.cx, y * g.size + g.cy) for x, y in pl] for pl in base
        ]

        # 印章边框要四边闭合，不做逐字手写抖动。
        if jitter and jitter.enabled and g.role != ROLE_SEAL:
            # 种子混入格子的整数化位置，保证"同位置同抖动、不同位置不同抖动"
            seed = (jitter.seed or 0) * 1000003 + int(g.cx * 10) * 7919 + int(g.cy * 10)
            rng = make_rng(seed)
            scaled = jitter_glyph(
                scaled, jitter, rng, origin=(g.cx, g.cy), font_size_mm=g.size
            )

        for pl in scaled:
            strokes.append(Stroke(points=pl, role=g.role))

    return strokes, list(missing_set)
