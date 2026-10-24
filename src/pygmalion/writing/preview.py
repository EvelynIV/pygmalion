from __future__ import annotations

from .models import PageConfig, Stroke


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
        paths.append(f'<path d="{d}" stroke-width="{s.width_mm:g}"/>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}"
     width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
  <rect x="0" y="0" width="{w:g}" height="{h:g}" fill="{bg}"/>
  <g fill="none" stroke="{color}" stroke-width="0.35"
     stroke-linecap="round" stroke-linejoin="round">
    {"".join(paths)}
  </g>
</svg>"""
