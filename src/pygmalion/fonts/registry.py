"""发现配置目录下的 Hugging Face Parquet 字体数据集。"""

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .sources import CJKSource

DEFAULT_FONT = "cjk-medians"


@dataclass
class FontInfo:
    file: str
    label: str
    chars: int
    points: int
    dataset_path: Path = field(repr=False)
    sublabel: str = "Hugging Face Dataset"
    notes: str = ""
    kind: str = "parquet"
    available: bool = True
    error: str = ""

    @property
    def id(self) -> str:
        return self.file.removesuffix(".parquet")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "label": self.label,
            "sublabel": self.sublabel,
            "notes": self.notes,
            "chars": self.chars,
            "avg_points": round(self.points / self.chars, 1) if self.chars else 0.0,
            "kind": self.kind,
            "available": self.available,
            "error": self.error,
        }


def dataset_dir(root: Path) -> Path:
    """相对字体目录以运行数据根目录为基准；空值使用 model-bin。"""
    path = Path(os.getenv("PYGMALION_FONTS_DIR") or "model-bin").expanduser()
    return path if path.is_absolute() else root / path


def discover(root: Path) -> list[FontInfo]:
    fonts = []
    base = dataset_dir(root)
    paths = sorted(base.rglob("train.parquet"))
    names = Counter(path.parent.name for path in paths)
    for path in paths:
        source = CJKSource(path)
        if not source.loaded:
            continue
        name = path.parent.name
        font_id = name if names[name] == 1 else path.parent.relative_to(base).as_posix()
        fonts.append(
            FontInfo(
                file=font_id + ".parquet",
                dataset_path=path,
                label="宋体" if path.parent.name == DEFAULT_FONT else path.parent.name,
                chars=len(source),
                points=source.points,
            )
        )
    fonts.sort(key=lambda font: (font.id != DEFAULT_FONT, font.id))
    return fonts


def default_id(root: Path) -> str:
    from ..config import CONFIG

    configured = CONFIG.get("fonts", {}).get("default", DEFAULT_FONT)
    fonts = discover(root)
    for font in fonts:
        if configured in (font.id, font.file, font.label):
            return font.id
    return fonts[0].id if fonts else ""


def resolve_chain(root: Path, chosen: str | None) -> list[str]:
    fonts = discover(root)
    requested = chosen or default_id(root)
    selected = next(
        (font for font in fonts if requested in (font.id, font.file, font.label)), None
    )
    if selected:
        fonts = [selected] + [font for font in fonts if font.id != selected.id]
    return [str(font.dataset_path) for font in fonts]
