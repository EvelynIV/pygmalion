"""
打印服务 —— 把投稿变成纸上字迹。

分成明确的两段，这是刻意的：

  prepare()  纯离线：排版、渲染字形、生成 G-code、出 SVG 预览。
             不碰串口，随便调用，出错也只是画不出来，不会动机器。

  PrinterService.submit()  排队，由后台线程真正发给机器。
             走 grbl.GrblLink.run_program()，那里有"必须先备份机器设置"的闸门。

为什么要有后台线程
------------------
一首诗有几百到上千条笔画，写一张纸要好几分钟。如果在 Web 请求里同步发送，
浏览器会一直转圈直到写完，中途刷新就断了。所以放进队列，由后台线程按顺序
执行，页面只负责轮询进度。

串口是独占资源：同一时刻只能有一个任务在发指令，队列天然保证了这点。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import queue
import threading
import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import db, gcode, glyphs, grbl, layout
from .config import CONFIG, OUTPUT_DIR, ROOT
from .handwriting import JitterConfig
from .smooth import smooth_strokes


# --------------------------------------------------------------------------
# 配置装配
# --------------------------------------------------------------------------


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

    page = layout.PageConfig(
        width_mm=p["width_mm"],
        height_mm=p["height_mm"],
        margin_top_mm=p["margin_top_mm"],
        margin_right_mm=p["margin_right_mm"],
        margin_bottom_mm=p["margin_bottom_mm"],
        margin_left_mm=p["margin_left_mm"],
    )
    lay = layout.LayoutConfig(
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
        # 坐标转换参数。这几项必须靠标定确定，不要凭猜。
        swap_xy=p["axes_swapped"],
        invert_x=p.get("invert_x", False),
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
    poem: layout.Poem
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
    pending_gfont: bool = False  # 快速预览正在后台补齐真迹字形

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
        return self.strokes > 0 and not self.pending_gfont


def _glyph_source(
    font: str | None = None, gfont_blocking: bool = True
) -> glyphs.CompositeSource:
    """
    组装字形来源。font 是选定的中文字库 id，None 用默认（宋体/Arphic）。
    选定字库排在链首，其余字库在后面兜底，生僻字照样写得出来。
    """
    return glyphs.source_from_dir(ROOT, font=font, gfont_blocking=gfont_blocking)


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

    poem = layout.Poem(
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
    source = _glyph_source(cfg["font"], gfont_blocking=not fast_preview)
    strokes, missing = glyphs.render_glyphs(placed, source, jit)
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
    svg = gcode.strokes_to_svg(strokes, page)
    from . import fonts as font_registry

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
        pending_gfont=source.pending_gfont,
        machine_snapshot=copy.deepcopy(CONFIG["machine"]),
    )

    if save_gcode:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = OUTPUT_DIR / f"sub{submission['id']}-{stamp}.gcode"
        path.write_text("\n".join(program), encoding="utf-8")
        result.gcode_path = str(path)

    return result


# --------------------------------------------------------------------------
# 后台打印队列
# --------------------------------------------------------------------------


@dataclass
class JobProgress:
    job_id: int
    submission_id: int
    status: str = "queued"  # queued | printing | done | failed | cancelled
    sent: int = 0
    total: int = 0
    message: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    @property
    def percent(self) -> float:
        return (self.sent / self.total * 100.0) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "submission_id": self.submission_id,
            "status": self.status,
            "sent": self.sent,
            "total": self.total,
            "percent": round(self.percent, 1),
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class PrinterService:
    """
    打印队列 + 后台工作线程。

    单例使用。整条队列同一时刻只有一个任务在跑（串口独占），
    其余排队等待。
    """

    def __init__(self, dry_run: bool = False):
        # dry_run=True 时只走排版和 G-code 生成，不真的连机器。
        # 开发和演示用；正式书写要显式关掉。
        self.dry_run = dry_run
        self._q: queue.Queue[int] = queue.Queue()
        self._jobs: dict[int, JobProgress] = {}
        self._lock = threading.Lock()
        self._cancel: set[int] = set()
        self._current: JobProgress | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._prepared: dict[int, Prepared] = {}
        self._link: grbl.GrblLink | None = None
        self.device_lock = threading.RLock()

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="printer-worker"
        )
        self._thread.start()

    def stop(self) -> None:
        for job in self.all_jobs():
            if job.status in ("queued", "printing"):
                try:
                    self.cancel(job.job_id)
                except ValueError:
                    pass  # 任务恰好已完成
        self._stop.set()
        self._q.put(-1)  # 唤醒阻塞中的 worker
        if self._thread:
            self._thread.join(timeout=5)

    # -- 提交与查询 -------------------------------------------------------

    def submit(
        self, submission_id: int, overrides: dict[str, Any] | None = None
    ) -> int:
        """把一条投稿排进书写队列。返回 job_id。"""
        sub = db.get_submission(submission_id)
        if not sub:
            raise ValueError(f"投稿 {submission_id} 不存在")

        # 预览与离线演练仍可用来校准；真正落笔前必须确认纸张完整位于刻度区。
        if not self.dry_run:
            error = grid_calibration_error()
            if error:
                raise ValueError(error)
        # 在入队时生成并冻结完整轨迹、纸张映射和机器参数。
        # 工作线程保存并发送这份轨迹，不读取后续预览或全局配置改动。
        frozen_overrides = dict(overrides or {})
        prepared = prepare(sub, frozen_overrides, save_gcode=False)
        if not prepared.can_print:
            raise ValueError("字形尚未加载完成，或没有可绘制的字符")

        job_id = db.add_job(submission_id, prepared.gcode_path)
        prog = JobProgress(
            job_id=job_id,
            submission_id=submission_id,
            total=len(prepared.gcode),
            overrides=frozen_overrides,
        )
        with self._lock:
            self._jobs[job_id] = prog
            self._prepared[job_id] = prepared
        db.freeze_job(job_id, sub, frozen_overrides, prog.total, self.dry_run)
        db.set_submission_status(submission_id, "queued", job_id)
        self._q.put(job_id)
        return job_id

    def get(self, job_id: int) -> JobProgress | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all_jobs(self) -> list[JobProgress]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.job_id, reverse=True)

    def cancel(self, job_id: int) -> None:
        with self._lock:
            prog = self._jobs.get(job_id)
            if not prog or prog.status not in ("queued", "printing"):
                raise ValueError("任务不存在或已经结束")
            self._cancel.add(job_id)
            if prog.status == "queued":
                prog.status = "cancelled"
                prog.message = "排队任务已中止"
                prog.finished_at = db.now()
                db.update_job(job_id, "cancelled", finished_at=prog.finished_at)
                db.set_submission_status(prog.submission_id, "pending", job_id)

    def emergency_stop(self) -> bool:
        """使用正在占用的串口发送实时急停，避免第二次打开独占端口。"""
        for job in self.all_jobs():
            if job.status in ("queued", "printing"):
                try:
                    self.cancel(job.job_id)
                except ValueError:
                    pass  # 任务恰好已完成
        link = self._link or grbl.active_link()
        if link and link.is_open:
            link.emergency_interrupt()
            return True
        return False

    @property
    def current(self) -> JobProgress | None:
        return self._current

    # -- 工作线程 ---------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if job_id < 0:
                break
            try:
                self._run_job(job_id)
            except Exception as exc:  # noqa: BLE001
                # 工作线程绝不能因为单个任务崩掉 —— 崩了整条队列就死了，
                # 后面所有投稿都会永远卡在"排队中"。
                self._fail(job_id, f"未预期的错误: {type(exc).__name__}: {exc}")

    def _run_job(self, job_id: int) -> None:
        with self._lock:
            prog = self._jobs.get(job_id)
            if not prog or prog.status == "cancelled":
                self._prepared.pop(job_id, None)
                return
            prepared = self._prepared.get(job_id)
            if not prepared:
                prog.status = "failed"
                prog.message = "冻结的任务数据不存在"
                db.update_job(
                    job_id, "failed", error=prog.message, finished_at=db.now()
                )
                return
            self._current = prog
            prog.status = "printing"
            prog.started_at = db.now()
        db.set_submission_status(prog.submission_id, "printing", job_id)
        db.update_job(job_id, "printing", started_at=prog.started_at)

        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUTPUT_DIR / f"job{job_id}.gcode"
            path.write_text("\n".join(prepared.gcode), encoding="utf-8")
            prepared.gcode_path = str(path)
            prog.total = len(prepared.gcode)
            # 写入实际生成的文件，通信故障时可以逐行回溯，而非猜 output 文件。
            if prepared.gcode_path:
                db.update_job(job_id, "printing", gcode_path=prepared.gcode_path)
            if not prepared.can_print:
                raise RuntimeError(
                    "一个字都画不出来。多半是缺少中文字库，或正文里没有可书写的字符。"
                )

            if self.dry_run:
                self._simulate(prog)
            else:
                self._send(prepared, prog)

            prog.status = "done"
            prog.finished_at = db.now()
            db.set_submission_status(prog.submission_id, "done", job_id)
            db.update_job(job_id, "done", finished_at=prog.finished_at)

        except grbl.CancelledError as exc:
            # 取消是正常操作，不是故障 —— 不该标成 failed 吓人，
            # 也不该在界面上显示成红色错误
            prog.status = "cancelled"
            prog.message = str(exc)
            prog.finished_at = db.now()
            db.set_submission_status(prog.submission_id, "pending", job_id)
            db.update_job(
                job_id, "cancelled", error=prog.message, finished_at=prog.finished_at
            )

        except Exception as exc:  # noqa: BLE001
            self._fail(job_id, str(exc))
        finally:
            self._link = None
            self._current = None
            self._cancel.discard(job_id)
            self._prepared.pop(job_id, None)
            db.save_progress(job_id, prog.sent, prog.total)

    def _simulate(self, prog: JobProgress) -> None:
        """演练模式：按真实节奏空跑，用来验证界面和队列。"""
        import time

        step = max(1, prog.total // 60)
        for i in range(0, prog.total, step):
            if prog.job_id in self._cancel:
                raise grbl.CancelledError("演练任务已中止")
            prog.sent = min(i + step, prog.total)
            time.sleep(0.05)
        if prog.job_id in self._cancel:
            raise grbl.CancelledError("演练任务已中止")
        prog.sent = prog.total
        prog.message = "演练模式：未连接机器"

    def _send(self, prepared: Prepared, prog: JobProgress) -> None:
        """真正把 G-code 发给机器。"""
        from . import grbl

        m = prepared.machine_snapshot

        with self.device_lock, grbl.GrblLink(m["port"], m["baud"]) as link:
            self._link = link
            # 硬约束：任何写入性操作前必须有一份当天的完整备份。
            # 用 ensure_backup_today 复用当天已有的备份，而不是每打印一次就新建
            # 一个文件 —— 否则连写几次就能堆出几十个几乎相同的备份文件。
            grbl.ensure_backup_today(link, reason=f"书写投稿 #{prepared.submission_id}")

            # 上次异常退出可能把机器留在 Hold（已有过这个 bug）。
            # 每次开写前自检一次，别让用户面对一台"按了没反应"的机器。
            link.recover_if_stuck(pen_up_z=m["z_off"])

            def on_progress(sent: int, total: int) -> None:
                prog.sent, prog.total = sent, total

            link.run_program(
                prepared.gcode,
                on_progress=on_progress,
                cancel_flag=lambda: prog.job_id in self._cancel,
                # 取消时要抬笔，否则笔停在纸上留墨点
                pen_up_z=m["z_off"],
            )
            # 指令发送完不代表机械运动结束。
            link.wait_idle(timeout=300)
            if prog.job_id in self._cancel:
                raise grbl.CancelledError("任务已中止，缓冲区动作已结束")
        self._link = None

    def _fail(self, job_id: int, message: str) -> None:
        with self._lock:
            prog = self._jobs.get(job_id)
        if prog:
            prog.status = "failed"
            prog.message = message
            prog.finished_at = db.now()
            db.set_submission_status(prog.submission_id, "failed", job_id)
        db.update_job(job_id, "failed", error=message, finished_at=db.now())
