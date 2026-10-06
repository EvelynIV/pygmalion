"""CNC Draw .gfont 的只读兼容层。

通过 CNC Draw 随附的保护加载器读取 .gfont 字形。不会启动 CNC 界面或串口，
不会修改原始字体；每次排版仅在临时目录创建 ASCII 名称的只读副本。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import json
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

Polyline = list[tuple[float, float]]


@dataclass(frozen=True)
class LoaderStatus:
    ready: bool
    error: str = ""
    jjs: Path | None = None
    agent: Path | None = None
    jar: Path | None = None
    script: Path | None = None


@dataclass(frozen=True)
class GFontInfo:
    path: Path
    version: int
    label: str
    size: int
    vendor: str = ""
    readable: bool = False
    error: str = ""

    @property
    def id(self) -> str:
        return f"gfont:{self.path.stem}"


def loader_status(root: Path) -> LoaderStatus:
    """检查项目随附的、只读的 GFont 加载运行时。"""
    runtime_root = root / "runtime" / "cnc-draw-loader"
    jjs = runtime_root / "jre" / "bin" / "jjs.exe"
    agent = runtime_root / "jre" / "bin" / "kdrawLoader.dll"
    jar = runtime_root / "kdraw.jar"
    script = root / "tools" / "gfont_export.js"
    parts = {
        "jjs.exe": jjs,
        "kdrawLoader.dll": agent,
        "kdraw.jar": jar,
        "gfont_export.js": script,
    }
    missing = [name for name, path in parts.items() if not path.is_file()]
    if missing:
        return LoaderStatus(
            False,
            "缺少项目内置 GFont 加载运行时：" + "、".join(missing),
            jjs,
            agent,
            jar,
            script,
        )
    return LoaderStatus(True, jjs=jjs, agent=agent, jar=jar, script=script)


def inspect(path: Path, status: LoaderStatus | None = None) -> GFontInfo:
    """读取稳定文件头；不把随机二进制内容当作字体名。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(128)
    except OSError as exc:
        return GFontInfo(path, 0, path.stem, 0, error=f"无法读取：{exc}")
    if len(head) < 4:
        return GFontInfo(path, 0, path.stem, size, error="文件不足 4 字节")

    version = struct.unpack(">I", head[:4])[0]
    if size <= 4:
        return GFontInfo(
            path,
            version,
            path.stem,
            size,
            error="这是 CNC Draw 的索引占位文件，不含可导出的字形",
        )
    # 已识别的 type=4 是 CNC Draw 轮廓字；本机原反序列化器对其字形条目
    # 报数组边界错误，不能将它伪装成可书写的单线字体。
    if version == 6 and len(head) >= 13:
        name_len = struct.unpack(">H", head[4:6])[0]
        type_offset = 6 + name_len
        if (
            name_len <= 80
            and len(head) >= type_offset + 4
            and head[6:type_offset] == b"kvenjoy"
            and struct.unpack(">I", head[type_offset : type_offset + 4])[0] == 4
        ):
            return GFontInfo(
                path,
                version,
                path.stem,
                size,
                vendor="CNC Draw",
                error="CNC Draw 轮廓字格式：原加载器无法导出单线笔画",
            )
    ready = bool(status and status.ready)
    return GFontInfo(
        path,
        version,
        path.stem,
        size,
        vendor="CNC Draw",
        readable=ready,
        error="" if ready else (status.error if status else "加载器未检查"),
    )


def discover(root: Path, directory: str | None = None) -> list[GFontInfo]:
    base = Path(directory) if directory else root / "data" / "gfonts"
    if not base.is_absolute():
        base = (root / base).resolve()
    if not base.is_dir():
        return []
    status = loader_status(root)
    return [
        inspect(path, status)
        for path in sorted(base.glob("*.gfont"), key=lambda item: item.name.casefold())
    ]


@dataclass
class _SharedGlyphStore:
    """一个物理 GFont 对应的一份进程内字形缓存与加载队列。"""

    path: Path
    root: Path
    status: LoaderStatus
    cache: dict[str, list[Polyline] | None] = field(default_factory=dict)
    warning_map: dict[str, None] = field(default_factory=dict)
    error: str = ""
    pending: set[str] = field(default_factory=set)
    loading: bool = False
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock())
    )


_SHARED_STORES_LOCK = threading.Lock()
_SHARED_STORES: dict[tuple[str, int, int], _SharedGlyphStore] = {}
# CNC Draw 的受保护加载器会启动一套 JVM。并行太多只会争抢 CPU 和临时目录，
# 两个槽既能处理不同字体，又不会因 20 人同时预览而拉起 20 个 JVM。
_EXPORT_SLOTS = threading.BoundedSemaphore(2)
_EXPORT_COALESCE_SECONDS = 0.08


def _store_key(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


def _shared_store(path: Path, root: Path, status: LoaderStatus) -> _SharedGlyphStore:
    key = _store_key(path)
    with _SHARED_STORES_LOCK:
        store = _SHARED_STORES.get(key)
        if store is None:
            store = _SharedGlyphStore(
                path=path,
                root=root,
                status=status,
                error="" if status.ready else status.error,
            )
            _SHARED_STORES[key] = store
        return store


def _run_export(store: _SharedGlyphStore, chars: list[str]) -> dict:
    """运行一次只读保护加载器；调用者负责批处理和并发限制。"""
    status = store.status
    assert status.ready
    assert status.jjs and status.agent and status.jar and status.script
    with tempfile.TemporaryDirectory(prefix="poem-writer-gfont-") as temp:
        staged = Path(temp) / "font.gfont"
        shutil.copy2(store.path, staged)
        completed = subprocess.run(
            [
                str(status.jjs),
                f"-J-agentpath:{status.agent}",
                f"-J-Dpoem.writer.gfont={staged}",
                f"-J-Dpoem.writer.codes={','.join(str(ord(ch)) for ch in chars)}",
                "-cp",
                str(status.jar),
                str(status.script),
            ],
            cwd=str(store.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    detail = (
        completed.stderr.strip()
        or completed.stdout.strip()
        or f"退出码 {completed.returncode}"
    )
    raise RuntimeError(detail[-500:])


class GFontSource:
    """CNC Draw GFont 字形来源，跨预览共享缓存并合并并发加载。"""

    _SQUARE_FORMS = frozenset("口日目田由甲申电白百回囗国园圆图囚囡")

    def __init__(self, path: str | Path, root: Path, blocking: bool = True):
        self.path = Path(path)
        self.root = Path(root)
        self.blocking = blocking
        self.pending = False
        self._status = loader_status(self.root)
        try:
            self._store = _shared_store(self.path, self.root, self._status)
        except OSError as exc:
            # 仍保留与旧实现一致的可回退语义：字体不可读不阻塞整篇预览。
            self._store = _SharedGlyphStore(
                self.path,
                self.root,
                self._status,
                error=f"CNC Draw 字体加载失败：{exc}",
            )
        self._cache = self._store.cache
        self.warnings: list[str] = []
        self.error = ""
        self._sync_state()

    def _sync_state(self) -> None:
        with self._store.condition:
            self.error = self._store.error
            self.warnings = list(self._store.warning_map)

    def prepare_chars(self, text: str) -> None:
        wanted = [ch for ch in dict.fromkeys(text) if not ch.isspace()]
        if not wanted:
            return
        store = self._store
        with store.condition:
            if store.error:
                self.error = store.error
                self.warnings = list(store.warning_map)
                return
            missing = [ch for ch in wanted if ch not in store.cache]
            if missing:
                store.pending.update(missing)
                if not store.loading:
                    store.loading = True
                    threading.Thread(
                        target=self._drain_exports,
                        args=(store,),
                        daemon=True,
                        name="gfont-cache-loader",
                    ).start()
            # 快速预览不等待受保护加载器：字形来源链会暂时回退到标准笔顺字库。
            # 后台批次完成后，前端自动拉取一次最终真迹预览。
            if not self.blocking:
                self.pending = any(ch not in store.cache for ch in wanted)
                self.error = store.error
                self.warnings = list(store.warning_map)
                return
            # 正式预览与实际书写必须等到自己的字形就绪，确保成品一致。
            while any(ch not in store.cache for ch in wanted) and not store.error:
                store.condition.wait()
            self.pending = False
            self.error = store.error
            self.warnings = list(store.warning_map)

    def _drain_exports(self, store: _SharedGlyphStore) -> None:
        """收集短时间内到达的请求，合并为一次 CNC Draw 解码。"""
        while True:
            time.sleep(_EXPORT_COALESCE_SECONDS)
            with store.condition:
                if store.error:
                    store.loading = False
                    store.condition.notify_all()
                    return
                batch = sorted(store.pending, key=ord)
                store.pending.clear()
                if not batch:
                    store.loading = False
                    store.condition.notify_all()
                    return
            try:
                # 不持有 condition 等待外部进程，其他请求才能并入下一批。
                with _EXPORT_SLOTS:
                    payload = _run_export(store, batch)
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                with store.condition:
                    store.error = f"CNC Draw 字体加载失败：{exc}"
                    store.loading = False
                    store.condition.notify_all()
                return

            glyphs = payload.get("glyphs", {})
            missing = {int(value) for value in payload.get("missing", [])}
            with store.condition:
                for ch in batch:
                    raw = glyphs.get(str(ord(ch)))
                    normalised = self._normalise(raw) if raw else []
                    if ord(ch) in missing:
                        store.cache[ch] = None
                    elif self._needs_square_fallback(ch, normalised):
                        store.cache[ch] = None
                        warning = (
                            f"CNC Draw 字体“{store.path.stem}”的「{ch}」字形被压扁，"
                            "已自动回退到标准笔顺字库。"
                        )
                        store.warning_map.setdefault(warning, None)
                    else:
                        store.cache[ch] = normalised or None
                store.condition.notify_all()
                if not store.pending:
                    store.loading = False
                    store.condition.notify_all()
                    return

    @classmethod
    def _needs_square_fallback(cls, ch: str, strokes: list[Polyline]) -> bool:
        """拦住把方框字压成细长装饰环的上游 GFont 异常。"""
        if ch not in cls._SQUARE_FORMS:
            return False
        points = [point for stroke in strokes for point in stroke]
        if len(points) < 3:
            return False
        xs, ys = zip(*points)
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if width <= 1e-8 or height <= 1e-8:
            return False
        return min(width, height) / max(width, height) < 0.62

    @staticmethod
    def _normalise(raw: object) -> list[Polyline]:
        if not isinstance(raw, list):
            return []
        strokes: list[Polyline] = []
        all_points: list[tuple[float, float]] = []
        for raw_stroke in raw:
            if not isinstance(raw_stroke, list):
                continue
            stroke: Polyline = []
            for point in raw_stroke:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                try:
                    xy = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue
                stroke.append(xy)
                all_points.append(xy)
            if len(stroke) >= 2:
                strokes.append(stroke)
        if not strokes:
            return []

        xs, ys = zip(*all_points)
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        return [
            [((x - cx) / span, (y - cy) / span) for x, y in stroke]
            for stroke in strokes
        ]

    def strokes(self, ch: str) -> list[Polyline]:
        if ch not in self._cache:
            self.prepare_chars(ch)
        return self._cache.get(ch) or []

    def supports(self, ch: str) -> bool:
        if ch not in self._cache:
            self.prepare_chars(ch)
        return bool(self._cache.get(ch))
