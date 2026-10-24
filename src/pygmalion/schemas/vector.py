"""矢量路径纯数据校验与资源上限。"""

import math
import re

from svgelements import Move, Path

MAX_SOURCE = 1_000_000
MAX_SEGMENTS = 10000
MAX_POINTS = 100000


def checked_path(data: str) -> Path:
    if not data.strip() or re.search(r"[^MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]", data):
        raise ValueError("矢量路径格式不正确")
    # 解析器会宽容忽略不完整指数，先确保每个非分隔符都属于完整 token。
    tokens = re.compile(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    )
    if re.sub(r"[\s,]", "", tokens.sub("", data)):
        raise ValueError("矢量路径包含非法数值")
    try:
        path = Path(data)
        if len(path) > MAX_SEGMENTS or not path or not isinstance(path[0], Move):
            raise ValueError()
        box = path.bbox()
        if box is None or not all(math.isfinite(v) for v in box):
            raise ValueError()
        for segment in path:
            for t in (0, 0.5, 1):
                point = segment.point(t)
                if point is not None and not all(math.isfinite(v) for v in point):
                    raise ValueError()
        return path
    except (ValueError, TypeError, ZeroDivisionError, OverflowError, IndexError) as exc:
        raise ValueError("矢量路径格式不正确或过于复杂") from exc
