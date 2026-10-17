"""离线制作扩展数据集；服务运行期间不生成或转换字体。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from datasets import Dataset, Features, List, Value, load_dataset
from pyarrow import parquet

from .sources import CJKSource

ASSET = Path(__file__).with_name("assets") / "NormStroke.svg"
SOURCE_SHA256 = "acc05c1a18fa3528ca4c70c0847d65b9a319dbafa38fa7f1a60436cd3d243fbc"
CHINESE_PUNCT = "，。、；：？！“”‘’（）《》〈〉【】—…·「」『』"
SYMBOLS = "±×÷≤≥≠≈∞√°℃‰¥￥€£"
TARGET_CHARS = "".join(
    dict.fromkeys(
        "".join(map(chr, range(33, 127)))
        + "".join(map(chr, range(0xFF01, 0xFF5F)))
        + CHINESE_PUNCT
        + SYMBOLS
    )
)
Point = tuple[float, float]
Polys = list[list[Point]]


def _arc(start: Point, values: list[float]) -> list[Point]:
    """SVG 端点式椭圆弧，采样弦误差不超过 0.5 个源字体单位。"""
    rx, ry, rotation, large, sweep, x, y = values
    end = (x, y)
    rx, ry = abs(rx), abs(ry)
    if start == end or not rx or not ry:
        return [end]
    phi = math.radians(rotation)
    co, si = math.cos(phi), math.sin(phi)
    dx, dy = (start[0] - x) / 2, (start[1] - y) / 2
    xp, yp = co * dx + si * dy, -si * dx + co * dy
    scale = max(1.0, math.hypot(xp / rx, yp / ry))
    rx, ry = rx * scale, ry * scale
    denominator = rx * rx * yp * yp + ry * ry * xp * xp
    factor = math.sqrt(max(0, (rx * rx * ry * ry - denominator) / denominator))
    if bool(large) == bool(sweep):
        factor = -factor
    cxp, cyp = factor * rx * yp / ry, -factor * ry * xp / rx
    cx = co * cxp - si * cyp + (start[0] + x) / 2
    cy = si * cxp + co * cyp + (start[1] + y) / 2
    theta = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    finish = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx)
    delta = (finish - theta) % (2 * math.pi)
    if not sweep:
        delta -= 2 * math.pi
    count = max(
        1, math.ceil(abs(delta) / min(math.pi / 16, math.sqrt(4 / max(rx, ry))))
    )
    points = []
    for i in range(1, count + 1):
        angle = theta + delta * i / count
        u, v = rx * math.cos(angle), ry * math.sin(angle)
        points.append((cx + co * u - si * v, cy + si * u + co * v))
    points[-1] = end
    return points


def _svg_strokes(path: str) -> Polys:
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", path)
    strokes: Polys = []
    index, command = 0, ""
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command not in {"M", "L", "A"}:
            raise ValueError(f"不支持的 SVG 指令：{command}")
        count = 7 if command == "A" else 2
        values = list(map(float, tokens[index : index + count]))
        if len(values) != count:
            raise ValueError("SVG 指令参数不完整")
        index += count
        if command == "M":
            strokes.append([(values[0], values[1])])
            command = "L"
        elif command == "L":
            strokes[-1].append((values[0], values[1]))
        else:
            strokes[-1].extend(_arc(strokes[-1][-1], values))
    return strokes


def _circle(x: float, y: float, radius: float) -> list[Point]:
    return [
        (
            x + radius * math.cos(i * math.tau / 32),
            y + radius * math.sin(i * math.tau / 32),
        )
        for i in range(33)
    ]


def _transform(polys: Polys, sx=1.0, sy=1.0, dx=0.0, dy=0.0) -> Polys:
    return [[(x * sx + dx, y * sy + dy) for x, y in stroke] for stroke in polys]


def supplemental_glyphs() -> dict[str, Polys]:
    """固定来源的真实中心线，加上项目自绘的标点和少量缺字符号。"""
    raw = ASSET.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("NormStroke 源文件校验失败")
    root = ElementTree.fromstring(raw)
    glyphs: dict[str, Polys] = {}
    for glyph in root.iter("{http://www.w3.org/2000/svg}glyph"):
        ch, path = glyph.get("unicode"), glyph.get("d")
        if ch and path:
            advance = float(glyph.get("horiz-adv-x", "350"))
            glyphs[ch] = [
                [((x / advance - 0.5) * 0.9, (0.3 - y / 1000)) for x, y in stroke]
                for stroke in _svg_strokes(path)
            ]
            ys = [y for poly in glyphs[ch] for _, y in poly]
            # 少数括号、根号超出源字体 em；围绕基线等比缩小。
            scale = min(
                1.0, 0.74 / max(0.74, 0.26 - min(ys)), 0.24 / max(0.24, max(ys) - 0.26)
            )
            if scale < 1:
                glyphs[ch] = _transform(
                    glyphs[ch], sx=scale, sy=scale, dy=0.26 * (1 - scale)
                )
    glyphs.update(
        {
            "^": [[(-0.25, -0.15), (0, -0.4), (0.25, -0.15)]],
            "`": [[(-0.1, -0.4), (0.05, -0.25)]],
            "|": [[(0, -0.45), (0, 0.4)]],
            "，": _transform(glyphs[","], sx=0.5, dx=-0.23),
            "。": [_circle(-0.25, 0.26, 0.065)],
            "、": [[(-0.3, 0.15), (-0.16, 0.3)]],
            "；": _transform(glyphs[";"], sx=0.5, dx=-0.23),
            "：": _transform(glyphs[":"], sx=0.5, dx=-0.23),
            "？": _transform(glyphs["?"], sx=0.65),
            "！": _transform(glyphs["!"], sx=0.65),
            "—": [[(-0.45, -0.05), (0.45, -0.05)]],
            "…": [[(x, -0.06), (x, -0.04)] for x in (-0.3, 0, 0.3)],
            "·": [[(0, -0.06), (0, -0.04)]],
            "〈": [[(0.15, -0.4), (-0.15, 0), (0.15, 0.4)]],
            "〉": [[(-0.15, -0.4), (0.15, 0), (-0.15, 0.4)]],
            "【": [[(0.18, -0.4), (-0.18, -0.4), (-0.18, 0.4), (0.18, 0.4)]],
            "】": [[(-0.18, -0.4), (0.18, -0.4), (0.18, 0.4), (-0.18, 0.4)]],
            "「": [[(-0.2, 0.15), (-0.2, -0.4), (0.2, -0.4)]],
            "」": [[(0.2, -0.15), (0.2, 0.4), (-0.2, 0.4)]],
            "‘": [[(-0.05, -0.45), (-0.12, -0.35), (-0.06, -0.29)]],
            "’": [[(0.06, -0.45), (0.12, -0.39), (0.05, -0.29)]],
            "°": [_circle(0, -0.35, 0.07)],
        }
    )
    for ch, ascii_ch in [("（", "("), ("）", ")")]:
        glyphs[ch] = _transform(glyphs[ascii_ch], sx=0.6)
    for ch, basic in [
        ("《", "〈"),
        ("》", "〉"),
        ("『", "「"),
        ("』", "」"),
        ("“", "‘"),
        ("”", "’"),
    ]:
        glyphs[ch] = _transform(glyphs[basic], dx=-0.1) + _transform(
            glyphs[basic], dx=0.1
        )
    glyphs["℃"] = [_circle(-0.33, -0.35, 0.07)] + _transform(
        glyphs["C"], sx=0.65, dx=0.12
    )
    glyphs["￥"] = glyphs["¥"]
    glyphs["€"] = glyphs["C"] + [[(-0.35, y), (0.18, y)] for y in (-0.12, 0.04)]
    glyphs["‰"] = _transform(glyphs["%"], sx=0.7, dx=-0.12) + [_circle(0.32, 0.2, 0.09)]
    for code in range(0xFF01, 0xFF5F):
        glyphs.setdefault(chr(code), glyphs[chr(code - 0xFEE0)])
    missing = set(TARGET_CHARS) - glyphs.keys()
    if missing:
        raise ValueError("补字来源不完整：" + "".join(sorted(missing)))
    return {ch: glyphs[ch] for ch in TARGET_CHARS}


def _row(ch: str, polys: Polys) -> dict:
    strokes = []
    for poly in polys:
        points = []
        for x, y in poly:
            point = (round(x * 10000), round(y * 10000))
            if not all(-32768 <= value <= 32767 for value in point):
                raise ValueError(f"字形坐标超出 int16：{ch}")
            if not points or point != points[-1]:
                points.append(point)
        if len(points) >= 2:
            strokes.append(points)
    if not strokes:
        raise ValueError(f"字形没有有效笔画：{ch}")
    return {
        "char": ch,
        "lengths": [len(p) for p in strokes],
        "x": [x for p in strokes for x, _ in p],
        "y": [y for p in strokes for _, y in p],
        "preserve_origin": True,
    }


def _specimens(output: Path, chars: str) -> None:
    import cv2
    import numpy as np

    source = CJKSource(output / "train.parquet")
    for size in (4, 8):
        # 每毫米 12 像素；同图包含实际比例的中文及补入字符。
        columns, width, height = 12, 118, 132
        image = np.full(
            (math.ceil(len(chars) / columns) * height, columns * width, 3),
            255,
            np.uint8,
        )
        for i, ch in enumerate(chars):
            left, top = (i % columns) * width, (i // columns) * height
            cv2.putText(
                image,
                f"U+{ord(ch):04X}",
                (left + 5, top + 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (90, 90, 90),
                1,
            )
            factor = (
                1.0
                if ch in CHINESE_PUNCT or unicodedata.east_asian_width(ch) in ("W", "F")
                else 0.5
            )
            for poly in source.strokes(ch):
                points = np.array(
                    [
                        (
                            round(left + width / 2 + x * size * 12 * factor),
                            round(top + 78 + y * size * 12),
                        )
                        for x, y in poly
                    ],
                    np.int32,
                )
                cv2.polylines(image, [points], False, (30, 30, 30), 1, cv2.LINE_AA)
        if not cv2.imwrite(str(output / f"glyphs-{size}mm.png"), image):
            raise OSError("字样图写入失败")


def build_font(base: Path, output: Path) -> dict:
    """完整校验后发布到一个新目录，拒绝覆盖已有字库。"""
    base, output = base.resolve(), output.resolve()
    if output.exists():
        raise ValueError(f"输出目录已存在，不能覆盖：{output}")
    original = load_dataset("parquet", data_files={"train": str(base)}, split="train")
    # 用现有加载器验证基础数据，再保留每条原始坐标及原点策略。
    from .dataset import load_parquet_font

    load_parquet_font(base)
    rows = [
        {**row, "preserve_origin": bool(row.get("preserve_origin", False))}
        for row in original
    ]
    existing = {row["char"] for row in rows}
    additions = [
        _row(ch, polys)
        for ch, polys in supplemental_glyphs().items()
        if ch not in existing
    ]
    rows.extend(additions)
    features = Features(
        {
            "char": Value("string"),
            "lengths": List(Value("uint16")),
            "x": List(Value("int16")),
            "y": List(Value("int16")),
            "preserve_origin": Value("bool"),
        }
    )
    report = {
        "font_id": "cjk-medians-plus",
        "created": datetime.now(UTC).isoformat(),
        "base_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
        "source": "https://github.com/octycs/norm-stroke",
        "source_sha256": SOURCE_SHA256,
        "base_chars": len(original),
        "added_chars": len(additions),
        "total_chars": len(rows),
        "added": "".join(row["char"] for row in additions),
        "missing": [],
        "modification": "保留基础字形；新增单线西文、数字、全角字符、标点和数学符号。",
    }
    pending = output.with_name(output.name + ".building")
    if pending.exists():
        raise ValueError(f"生成目录已存在，请检查上次产物：{pending}")
    pending.mkdir(parents=True)
    try:
        table = Dataset.from_list(rows, features=features).data.table
        table = table.replace_schema_metadata(
            {
                **(table.schema.metadata or {}),
                b"pygmalion.modification": json.dumps(
                    report, ensure_ascii=False
                ).encode("utf-8"),
            }
        )
        parquet.write_table(table, pending / "train.parquet", compression="zstd")
        source = CJKSource(pending / "train.parquet")
        if any(not source.supports(ch) for ch in existing | set(TARGET_CHARS)):
            raise ValueError("生成后字库覆盖校验失败")
        (pending / "coverage.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        notice = (
            "基于现有 CJK Medians 的扩展单线字库；保留原汉字。\n"
            + report["modification"]
            + "\n生成日期："
            + report["created"]
            + "\n"
        )
        (pending / "README.md").write_text(
            "---\nlanguage: [zh, en]\nlicense: other\nconfigs:\n- config_name: default\n  data_files:\n  - split: train\n    path: train.parquet\n---\n\n# 中文单线·扩展\n\n"
            + notice
            + "\n每行 char、lengths(uint16[])、x/y(int16[])；坐标除以10000，Y向下。preserve_origin=true 保留字格原点、基线与标点位置，否则沿用原字库的墨迹居中。\n"
            + "\n原始汉字沿用 Arphic Public License；新增 NormStroke 字形来自 CC0 来源，项目补绘字符一并提供。详见 ARPHICPL.TXT、SOURCES.md 和 coverage.json。\n",
            encoding="utf-8",
        )
        license_path = base.parent / "ARPHICPL.TXT"
        if not license_path.exists():
            license_path = (
                Path(__file__).resolve().parents[3] / "licenses" / "ARPHICPL.TXT"
            )
        shutil.copyfile(license_path, pending / "ARPHICPL.TXT")
        shutil.copyfile(ASSET.with_name("SOURCES.md"), pending / "SOURCES.md")
        _specimens(
            pending, "".join(ch for ch in "天地年月日" if ch in existing) + TARGET_CHARS
        )
        pending.rename(output)
    except BaseException:
        shutil.rmtree(pending)
        raise
    return report
