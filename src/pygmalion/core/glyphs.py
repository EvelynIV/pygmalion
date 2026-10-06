"""
单线字形的取用与摆放。

字体数据从哪来
--------------
拉丁字母/数字/西文标点：HersheyFonts 包自带的 32 套 Hershey 字体（公有领域）。
    实测 API：load_default_font(name) / normalize_rendering(f) / strokes_for_text(s)
    汉字：Hershey 不含 CJK，实测对 '汉'、'。' 均返回 0 条笔画。
    中文单线需要另行提供字库（见 CJKSource），没有就只能写西文。

坐标系（两处翻转，容易搞错，写清楚）
------------------------------------
HersheyFonts 的 normalize_rendering(factor) 把字形缩放到字身框高度 = factor，
并且输出是 **y 向上** 的（实测：字身框占 y ∈ [0, factor]，底线在 0.25*factor，
顶线在 factor）。本项目的页面坐标是 **y 向下**，所以这里做一次翻转：
    ny = (1.0 - y_normalized)
翻转后字身框仍占 [0, 1]，但 0 在上、1 在下，与页面坐标一致。

摆放策略
--------
横向：按字形自身的墨迹包围盒居中。诗歌是"一字一格"，让每个字在自己的格子里
      居中，视觉上才整齐。
纵向：按**字身框**（而不是墨迹包围盒）对齐。这样西文的大小写、升降部共享
      同一条底线，不会出现 'L' 和 'i' 各居其格中心而高低错落的情况。
      汉字字身框≈墨迹框，两种策略结果相同。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from typing import Protocol
from pathlib import Path

import threading

import HersheyFonts

from .handwriting import JitterConfig, jitter_glyph, make_rng
from .layout import PlacedGlyph, Stroke, ROLE_SEAL

Polyline = list[tuple[float, float]]


# --------------------------------------------------------------------------
# 字形来源
# --------------------------------------------------------------------------


class GlyphSource(Protocol):
    """单线字形来源。返回的坐标：原点在字形左侧居中，单位 em，y 向下。"""

    def strokes(self, ch: str) -> list[Polyline]: ...

    def supports(self, ch: str) -> bool: ...


# 用几何直接定义的字符。这些不来自字体文件。
#
# 坐标约定：以字格中心为原点，x 向右、y 向下，范围约 ±0.5（即单位字格）。
# 数组是"去中心化"之前的原始定义，见 BuiltinSource。
#
# 为什么中文字标点要手画：hanzi-writer-data 只有汉字
# （全部落在 U+4E00–U+9FFF），不含任何标点。笔式绘图仪画标点本来也就是
# 几个简单几何形状，自己定义比找字库更可控。
#
# 中文标点的位置约定：。，、在字格**左下**，：；？！等居中或上下分布。


def _circle(cx: float, cy: float, r: float, n: int = 18) -> Polyline:
    """整圆。n 越大越圆，18 段对 12mm 的字已经看不出棱角。"""
    import math

    return [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n + 1)
    ]


def _quad(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    n: int = 12,
) -> Polyline:
    """二次贝塞尔曲线采样。标点里的弧线用它画。"""
    out: Polyline = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        out.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _dot(cx: float, cy: float, r: float = 0.045) -> Polyline:
    return _circle(cx, cy, r, 8)


# 印章边框用得上的通长直线（贯穿整个字格）
_FULL_H: Polyline = [(-0.5, 0.0), (0.5, 0.0)]  # 横贯
_FULL_V: Polyline = [(0.0, -0.5), (0.0, 0.5)]  # 竖贯

BUILTIN: dict[str, list[Polyline]] = {
    # --- 印章边框 ---
    "━": [_FULL_H],
    "┃": [_FULL_V],
    # --- 中文标点 ---
    "。": [_circle(-0.14, 0.16, 0.115)],
    "，": [_quad((-0.22, 0.02), (-0.02, 0.10), (-0.17, 0.32))],
    "、": [[(-0.24, 0.06), (-0.06, 0.28)]],
    "：": [_dot(0.0, -0.15), _dot(0.0, 0.15)],
    "；": [_dot(0.0, -0.16), _quad((-0.04, 0.06), (0.10, 0.14), (-0.02, 0.30))],
    "？": [_quad((-0.16, -0.16), (0.14, -0.24), (0.02, 0.02)), _dot(0.0, 0.20)],
    "！": [[(0.0, -0.30), (0.0, 0.08)], _dot(0.0, 0.24)],
    "“": [[(-0.20, -0.22), (-0.20, -0.06)], [(-0.04, -0.22), (-0.04, -0.06)]],
    "”": [[(0.04, -0.22), (0.04, -0.06)], [(0.20, -0.22), (0.20, -0.06)]],
    "‘": [[(-0.06, -0.22), (-0.06, -0.08)]],
    "’": [[(0.06, -0.22), (0.06, -0.08)]],
    "（": [_quad((-0.06, -0.32), (-0.30, 0.0), (-0.06, 0.32))],
    "）": [_quad((0.06, -0.32), (0.30, 0.0), (0.06, 0.32))],
    "《": [[(0.10, -0.30), (-0.14, 0.0), (0.10, 0.30)]],
    "》": [[(-0.10, -0.30), (0.14, 0.0), (-0.10, 0.30)]],
    "「": [[(0.16, -0.28), (-0.16, -0.28), (-0.16, 0.16)]],
    "」": [[(-0.16, 0.28), (0.16, 0.28), (0.16, -0.16)]],
    "—": [[(-0.5, 0.0), (0.5, 0.0)]],
    "…": [_dot(-0.26, 0.16), _dot(0.0, 0.16), _dot(0.26, 0.16)],
    "·": [_dot(0.0, 0.0)],
    "　": [],  # 全角空格，不画
}


class BuiltinSource:
    """
    几何内置字符。优先级最高 —— 印章框和标点必须一定能画出来，
    不能因为字体变了就消失。
    """

    def strokes(self, ch: str) -> list[Polyline]:
        return BUILTIN.get(ch, [])

    def supports(self, ch: str) -> bool:
        return ch in BUILTIN


class HersheySource:
    """Hershey 单线西文字体。"""

    def __init__(self, font_name: str = "futural"):
        self.font_name = font_name
        self._font = HersheyFonts.HersheyFonts()
        try:
            self._font.load_default_font(font_name)
        except Exception:
            # 指定的字体不存在就退回默认，不让整个程序起不来
            self._font.load_default_font("")
            self.font_name = "default"
        # 归一化一次到"字身框高度 = 1"，之后自己按字号缩放坐标。
        # 不要每次渲染都调 normalize_rendering —— 它改的是对象内部状态。
        self._font.normalize_rendering(1.0)
        self._cache: dict[str, list[Polyline]] = {}

    def strokes(self, ch: str) -> list[Polyline]:
        if ch in self._cache:
            return self._cache[ch]

        try:
            raw = list(self._font.strokes_for_text(ch))
        except Exception:
            raw = []

        # 收集所有点，算横向墨迹范围（纵向用字身框，不做墨迹居中）
        all_pts = [(float(x), float(y)) for poly in raw for x, y in poly]
        if not all_pts:
            self._cache[ch] = []
            return []

        xs = [p[0] for p in all_pts]
        ink_cx = (min(xs) + max(xs)) / 2.0

        out: list[Polyline] = []
        for poly in raw:
            pl: Polyline = []
            for x, y in poly:
                # 横向：按墨迹包围盒中心居中
                nx = float(x) - ink_cx
                # 纵向：翻转成 y 向下，并按字身框中心（0.5）对齐
                ny = (1.0 - float(y)) - 0.5
                pl.append((nx, ny))
            out.append(pl)

        self._cache[ch] = out
        return out

    def supports(self, ch: str) -> bool:
        return bool(self.strokes(ch))


# CJK JSON 文件很大（每套约 10 MB），而预览会频繁新建字形来源。字形数据
# 初始化后只读，因此安全地由所有请求共享；锁保证高峰时不会重复解析同一文件。
_CJK_CACHE_LOCK = threading.RLock()
_CJK_DATA_CACHE: dict[tuple[str, int, int], dict[str, list[Polyline]]] = {}


def _cjk_cache_key(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


class CJKSource:
    """
    中文单线字形，来自 hanzi-writer-data 的笔画中心线。

    数据由 tools/import_cjk_font.py 预先转换好，格式是：
        {"永": [[[x,y], [x,y], ...], ...], ...}
    坐标已经是 **原点在字身框中心、y 向下、单位化字身框**（约在 ±0.5 内），
    与 HersheySource 的输出约定一致，所以这里直接返回、不再变换。

    转换之所以放在离线工具里做，是因为源数据要统计全字库才能定出真实字身框，
    而且 y 轴方向是反的 —— 这些一次性算好，运行时就不用碰了。

    没有字库时 supports() 一律返回 False，排版时该字会被跳过并在界面提示，
    而不是静默画出一个空白格。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else None
        self._data: dict[str, list[Polyline]] = {}
        if self.path:
            self._load(self.path)

    def _load(self, path: str | Path) -> None:
        import json
        from pathlib import Path as _P

        p = _P(path)
        if not p.exists():
            return
        try:
            key = _cjk_cache_key(p)
        except OSError:
            return

        # 锁覆盖首次解析，避免 20 个同时预览各自解析同一份 10 MB JSON。
        # 数据写入缓存后不再修改，后续 Source 只持有同一个只读字典引用。
        with _CJK_CACHE_LOCK:
            cached = _CJK_DATA_CACHE.get(key)
            if cached is not None:
                self._data = cached
                return
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return

            data: dict[str, list[Polyline]] = {}
            for ch, val in raw.items():
                if isinstance(val, dict):
                    val = val.get("strokes") or val.get("paths") or []
                polys: list[Polyline] = []
                for poly in val or []:
                    pts = [(float(pt[0]), float(pt[1])) for pt in poly if len(pt) >= 2]
                    if len(pts) >= 2:
                        polys.append(pts)
                if polys:
                    if p.name == "cjk-heiti.json" and ch == "一":
                        polys = self._repair_heiti_one(polys)
                    data[ch] = self._center_ink(polys)
            _CJK_DATA_CACHE[key] = data
            self._data = data

    @staticmethod
    def _repair_heiti_one(polys: list[Polyline]) -> list[Polyline]:
        """将黑体「一」的重复轮廓压成一条标准横笔。"""
        points = [point for poly in polys for point in poly]
        if len(points) < 2:
            return polys
        xs, ys = zip(*points)
        y = (min(ys) + max(ys)) / 2.0
        return [[(min(xs), y), (max(xs), y)]]

    @staticmethod
    def _center_ink(polys: list[Polyline]) -> list[Polyline]:
        """以每个字自己的墨迹框居中，但绝不改变笔画的比例或顺序。

        导入字库以整套字体的公共框心归一化；对于「一」这类只占一条细横
        的字，公共框会把它留在格子的顶端或底端。排版传入的是字格中心，
        因而应在字形层消除这份单字偏移。这样既保留字的实际大小，也让
        所有“占不满字格”的字落在自己的格心。
        """
        points = [point for poly in polys for point in poly]
        if not points:
            return polys
        xs, ys = zip(*points)
        dx = -((min(xs) + max(xs)) / 2.0)
        dy = -((min(ys) + max(ys)) / 2.0)
        return [[(x + dx, y + dy) for x, y in poly] for poly in polys]

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def strokes(self, ch: str) -> list[Polyline]:
        return self._data.get(ch, [])

    def supports(self, ch: str) -> bool:
        return ch in self._data


class CompositeSource:
    """依次询问各个来源，第一个能画的胜出。"""

    def __init__(self, sources: list[GlyphSource]):
        self.sources = sources
        self.warnings: list[str] = []

    def prepare_chars(self, text: str) -> None:
        """让批量后端先加载整页文字，避免每个字符启动一次外部解码器。"""
        for src in self.sources:
            prepare = getattr(src, "prepare_chars", None)
            if callable(prepare):
                prepare(text)
            error = getattr(src, "error", "")
            if error and error not in self.warnings:
                self.warnings.append(error)
            for warning in getattr(src, "warnings", []):
                if warning and warning not in self.warnings:
                    self.warnings.append(warning)

    @property
    def pending_gfont(self) -> bool:
        """快速预览是否仍在等待 CNC Draw 真迹字形。"""
        return any(bool(getattr(src, "pending", False)) for src in self.sources)

    def strokes(self, ch: str) -> list[Polyline]:
        for src in self.sources:
            if src.supports(ch):
                return src.strokes(ch)
        return []

    def supports(self, ch: str) -> bool:
        return any(src.supports(ch) for src in self.sources)

    def missing(self, text: str) -> list[str]:
        """返回 text 里画不出来的字（去重、保序）。界面用它提示用户。"""
        seen: dict[str, None] = {}
        for ch in text:
            if ch.isspace():
                continue
            if not self.supports(ch):
                seen.setdefault(ch, None)
        return list(seen)


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def build_source(
    latin: str = "futural", cjk_paths: list[str] | str | None = None
) -> CompositeSource:
    """
    按配置组装字形来源链。顺序即优先级，先问的在前面。

    内置几何字符排最前（印章框和标点必须一定能画出来），然后西文，
    最后按顺序挂中文字库 —— 可以挂多个，前一个查不到的字会自动落到
    后一个。楷体和 hanzi-writer 就是这么并存的：楷体字多且适合写诗，
    查不到时退回 hanzi-writer。
    """
    sources: list[GlyphSource] = [BuiltinSource(), HersheySource(latin)]

    if isinstance(cjk_paths, str):
        cjk_paths = [cjk_paths]
    for p in cjk_paths or []:
        src = CJKSource(p)
        if src.loaded:
            sources.append(src)

    return CompositeSource(sources)


def source_from_dir(
    root: str | Path,
    latin: str = "futural",
    font: str | None = None,
    prefer: tuple[str, ...] | None = None,
    gfont_blocking: bool = True,
) -> CompositeSource:
    """
    组装字形来源链。

    root   项目根目录（里面要有 data/fonts/）
    font   选定的中文字库 id（如 "cjk-kaiti"）。None 用默认字库。
           选定的字库排在最前，其余按默认顺序跟在后面对称兜底 ——
           这样生僻字不会因为换字体就写不出来。
    prefer 显式指定字库文件名的优先顺序，会覆盖 font 参数。
           给测试和特殊用途用。

    为什么默认是 hanzi-writer（宋体 / Arphic）而不是字更多的楷体
    -----------------------------------------------------------
    实测同样写"床前明月光/疑是地上霜"十个字：

        hanzi-writer (Arphic)   83 条笔画 / 494 个点  —— 平均每字 51 个点
        chinese-hershey (楷体)   91 条笔画 / 217 个点  —— 平均每字 22 个点

    点数直接决定观感：楷体那套平均每条笔画只有 2.4 个点，等于用折线代替
    曲线，写出来细而方硬，撇捺没有弧度。而且它是用计算机视觉从字体里
    **推断**骨架的，笔画顺序不是真实笔顺，复杂字（"疑""霜"）还会糊。

    hanzi-writer 的数据提取自 Arphic 字体，而且它本身就是教笔顺的库，
    medians 就是标准笔顺 —— 写出来既有形、下笔次序也对。

    代价是字数少一半。所以别的字库留在链后面兜底，生僻字照样写得出来。

    所有调用方（打印、预览、测试）都走这一个函数 —— 否则很容易出现
    "预览用的是一种字体、实际写出来是另一种"这种难查的不一致。
    """
    from pathlib import Path as _P

    if prefer is not None:
        d = _P(root) / "data" / "fonts"
        paths = [str(d / name) for name in prefer if (d / name).exists()]
    else:
        from . import fonts as font_registry

        paths = font_registry.resolve_chain(_P(root), font)

    source = build_source(latin=latin, cjk_paths=paths)
    if font in ("cjk-kaiti", "cjk-kaiti.json"):
        source.warnings.append(
            "楷体原始单线数据含畸形笔画；常用字已自动使用标准笔顺字形，"
            "楷体只用于补足缺字。"
        )
    gfont_path = font_registry.selected_gfont(_P(root), font)
    if gfont_path:
        from .gfont import GFontSource

        # 内置标点必须最优先（GFont 将每个字独立放大，逗号句号会被放成整格）；其余字由 GFont 优先。
        source.sources.insert(
            1, GFontSource(gfont_path, _P(root), blocking=gfont_blocking)
        )
    return source


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
            [(x * g.size + g.cx, y * g.size + g.cy) for x, y in pl] for pl in base
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
