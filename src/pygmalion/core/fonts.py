"""
字库注册表 —— 发现 `data/fonts/` 下的中文字库，给出可读的名字和说明。

设计目标
--------
**加一套新字库不需要改代码。** 把转换好的 `cjk-xxx.json` 丢进 `data/fonts/`，
它就会自动出现在界面上的字体下拉框里；再往 `fonts.json` 里加一条元数据，
就能给它一个好看的中文名和说明。

元数据格式（`data/fonts/fonts.json`，可选）：

    {
      "fonts": [
        {"file": "cjk-medians.json", "label": "宋体", "sublabel": "Arphic",
         "notes": "笔画饱满，笔顺标准", "order": 10}
      ]
    }

没有元数据的字库也能用，只是名字直接显示文件名。

回退链
------
用户选定一套字库后，**它排第一，其余自动跟在后面对称兜底** —— 选定字库
查不到的生僻字仍能从别的字库里写出来，不会变成空白格。这比"选谁只用谁"
实用得多：没人愿意因为一个生僻字就换字体。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import json
from dataclasses import dataclass
from pathlib import Path

# 字库文件放哪、元数据叫什么
FONTS_SUBDIR = "data/fonts"
META_FILE = "fonts.json"

# 内置的默认元数据。用户没写 fonts.json 时用这套，保证开箱即用。
BUILTIN_META: dict[str, dict] = {
    "cjk-medians.json": {
        "label": "宋体",
        "sublabel": "Arphic",
        "notes": "笔画饱满带弧度，笔顺是标准笔顺。9574 字。",
        "order": 10,
    },
    "cjk-kaiti.json": {
        "label": "楷体（兼容）",
        "sublabel": "chinese-hershey-font",
        "notes": "上游自动骨架存在畸形笔画；常用字改用标准笔顺，楷体仅补生僻字。",
        "order": 20,
    },
    "cjk-heiti.json": {
        "label": "黑体",
        "sublabel": "chinese-hershey-font",
        "notes": "方正无衬线。20976 字。",
        "order": 30,
    },
}

DEFAULT_FONT = "cjk-medians.json"

# 数字库要解析 10MB 的 JSON，一两秒起步。字体列表在界面上是经常拉的，
# 所以结果缓存在这里，一个进程只算一次。
_COUNT_CACHE: dict[tuple[str, str], tuple[int, int]] = {}


@dataclass
class FontInfo:
    """一套可用字库的描述。"""

    file: str  # 文件名，如 cjk-kaiti.json
    label: str  # 显示名，如「楷体」
    sublabel: str = ""  # 副标题，一般是来源
    notes: str = ""  # 一句话说明
    chars: int = 0  # 收录字数
    points: int = 0  # 总点数，反映笔画的细腻程度
    order: int = 100  # 下拉框排序权重，小的在前
    kind: str = "json"
    available: bool = True
    error: str = ""

    @property
    def id(self) -> str:
        """对外标识 —— 去掉扩展名，短一点，URL 里好看。"""
        if self.kind == "gfont":
            return f"gfont:{Path(self.file).stem}"
        return Path(self.file).stem

    @property
    def avg_points(self) -> float:
        """平均每字的点数。低于 3 说明笔画接近直线，观感偏硬。"""
        return (self.points / self.chars) if self.chars else 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "label": self.label,
            "sublabel": self.sublabel,
            "notes": self.notes,
            "chars": self.chars,
            "avg_points": round(self.avg_points, 1),
            "kind": self.kind,
            "available": self.available,
            "error": self.error,
        }


def _load_meta(fonts_dir: Path) -> dict[str, dict]:
    """读 fonts.json，与内置默认值合并（文件里的配置优先）。"""
    meta = {k: dict(v) for k, v in BUILTIN_META.items()}
    p = fonts_dir / META_FILE
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            for item in raw.get("fonts", []):
                f = item.get("file")
                if f:
                    meta[f] = {**meta.get(f, {}), **item}
        except (json.JSONDecodeError, OSError, AttributeError, TypeError):
            pass  # 元数据坏了不影响字库本身能用
    return meta


def _count(fonts_dir: Path, name: str) -> tuple[int, int]:
    """数字库的字数和总点数（带缓存）。"""
    key = (str(fonts_dir), name)
    if key in _COUNT_CACHE:
        return _COUNT_CACHE[key]
    try:
        data = json.loads((fonts_dir / name).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("不是字典结构")
        chars = len(data)
        points = sum(len(pl) for v in data.values() for pl in v)
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        chars, points = 0, 0
    _COUNT_CACHE[key] = (chars, points)
    return chars, points


def discover(root: Path) -> list[FontInfo]:
    """
    扫描字库目录，返回所有可用的中文字库，按 order 排序。

    只认 cjk-*.json —— 那是本项目转换后的格式。原始的 .hf.txt、元数据
    fonts.json 之类的都不算。
    """
    fonts_dir = root / FONTS_SUBDIR
    if not fonts_dir.is_dir():
        return []

    meta = _load_meta(fonts_dir)
    out: list[FontInfo] = []

    for p in sorted(fonts_dir.glob("cjk-*.json")):
        m = meta.get(p.name, {})
        chars, points = _count(fonts_dir, p.name)
        if chars == 0:
            continue  # 空文件或坏文件，不列出来误导人
        out.append(
            FontInfo(
                file=p.name,
                label=m.get("label") or p.stem,
                sublabel=m.get("sublabel", ""),
                notes=m.get("notes", ""),
                chars=chars,
                points=points,
                order=int(m.get("order", 100)),
            )
        )

    # GFont 是 CNC Draw 的私有二进制容器。先完整列出本机资产及版本，
    # 未有验证过的解码器时绝不能伪造笔画或将未知数据送进机床。
    from . import gfont
    from .config import CONFIG

    gfont_dir = CONFIG.get("fonts", {}).get("gfont_dir")
    for item in gfont.discover(root, gfont_dir):
        out.append(
            FontInfo(
                file=item.path.name,
                label=item.label,
                sublabel=f"CNC Draw GFont v{item.version}",
                notes=item.error or "通过 CNC Draw 保护加载器读取原始笔顺与分笔。",
                order=50,
                kind="gfont",
                available=item.readable,
                error=item.error,
            )
        )

    out.sort(key=lambda f: (f.order, f.label))
    return out


def resolve_chain(root: Path, chosen: str | None) -> list[str]:
    """
    把"用户选的字库"变成一条完整的回退链，返回绝对路径列表。

    选定字库排第一，其余按默认顺序跟在后面兜底。chosen 为空或认不出来时
    退回默认字库。返回空列表表示一个字库都没有（调用方要能处理）。
    """
    # 只有已验证的单线字库能进入当前 Python 走笔链路。
    available = [f for f in discover(root) if f.kind == "json" and f.available]
    if not available:
        return []

    chosen_file = None
    if chosen:
        for f in available:
            if chosen in (f.id, f.file, f.label):
                chosen_file = f.file
                break
    if chosen_file is None:
        chosen_file = (
            DEFAULT_FONT
            if any(f.file == DEFAULT_FONT for f in available)
            else available[0].file
        )

    # chinese-hershey-font 的楷体是从位图自动提取的单线骨架。它的上游原始
    # 路径本身就含交叉、重复和错误分笔（不是本项目的缩放/坐标错误）。常用字
    # 优先走笔顺数据，楷体仅补足未收录的生僻字，保证成品可读。
    if chosen_file == "cjk-kaiti.json" and DEFAULT_FONT != chosen_file:
        chain = [DEFAULT_FONT, chosen_file]
        chain.extend(f.file for f in available if f.file not in chain)
    else:
        chain = [chosen_file] + [f.file for f in available if f.file != chosen_file]
    fonts_dir = root / FONTS_SUBDIR
    return [str(fonts_dir / name) for name in chain]


def selected_gfont(root: Path, chosen: str | None) -> Path | None:
    """返回当前选择的可读取 CNC Draw GFont；未选择时尊重配置默认值。"""
    from . import gfont
    from .config import CONFIG

    requested = chosen or CONFIG.get("fonts", {}).get("default") or ""
    directory = CONFIG.get("fonts", {}).get("gfont_dir")
    for item in gfont.discover(root, directory):
        if item.readable and requested in (
            item.id,
            item.path.name,
            item.path.stem,
            item.label,
        ):
            return item.path
    return None


def default_id(root: Path) -> str:
    """返回配置指定且可用的默认字体，缺失时安全退回开源字库。"""
    from .config import CONFIG

    configured = CONFIG.get("fonts", {}).get("default") or ""
    available = [font for font in discover(root) if font.available]
    for font in available:
        if configured in (font.id, font.file, font.label):
            return font.id
    for font in available:
        if font.kind == "json" and font.file == DEFAULT_FONT:
            return font.id
    return available[0].id if available else ""
