"""仅从 Hugging Face Parquet 数据集加载单线字形。"""

from pathlib import Path
import threading
from typing import Protocol

from .dataset import load_parquet_font

Polyline = list[tuple[float, float]]


class GlyphSource(Protocol):
    def strokes(self, ch: str) -> list[Polyline]: ...
    def supports(self, ch: str) -> bool: ...


_CACHE_LOCK = threading.RLock()
_DATA_CACHE: dict[tuple[str, int, int], dict[str, list[Polyline]]] = {}


class CJKSource:
    """数据集字形；坐标单位为 em，字格中心为原点，y 向下。"""

    def __init__(self, path: str | Path):
        path = Path(path)
        if path.suffix != ".parquet":
            raise ValueError("字体仅支持 Hugging Face Parquet 数据集")
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        with _CACHE_LOCK:
            if key not in _DATA_CACHE:
                raw = load_parquet_font(path)
                _DATA_CACHE[key] = {
                    ch: self._center_ink(
                        [
                            [(float(x), float(y)) for x, y in stroke]
                            for stroke in strokes
                            if len(stroke) >= 2
                        ]
                    )
                    for ch, strokes in raw.items()
                    if any(len(stroke) >= 2 for stroke in strokes)
                }
            self._data = _DATA_CACHE[key]

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
    def points(self) -> int:
        return sum(len(stroke) for strokes in self._data.values() for stroke in strokes)

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
    """所选数据集优先，其余数据集补充缺字。"""

    def __init__(self, sources: list[GlyphSource]):
        self.sources = sources
        self.warnings: list[str] = []

    def prepare_chars(self, text: str) -> None:
        """数据集已在构造时加载完成。"""

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


def source_from_dir(root: str | Path, font: str | None = None) -> CompositeSource:
    from .registry import resolve_chain

    return CompositeSource(
        [CJKSource(path) for path in resolve_chain(Path(root), font)]
    )
