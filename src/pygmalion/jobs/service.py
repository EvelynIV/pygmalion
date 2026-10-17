from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from ..config import OUTPUT_DIR
from ..machine import grbl
from ..machine.controller import DeviceController
from ..storage import now
from ..submissions import repository as submissions
from ..writing.prepare import Prepared, grid_calibration_error, prepare
from . import repository as jobs
from .models import JobProgress

logger = logging.getLogger(__name__)


class PrinterService:
    """
    打印队列 + 后台工作线程。

    单例使用。整条队列同一时刻只有一个任务在跑（串口独占），
    其余排队等待。
    """

    def __init__(
        self, dry_run: bool = False, controller: DeviceController | None = None
    ):
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
        self.workbench_lock = threading.Lock()
        self.controller = controller or DeviceController()
        self.controller.busy = lambda: self.current is not None

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="printer-worker"
        )
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            raise

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
        sub = submissions.get_submission(submission_id)
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

        return self.enqueue_prepared(sub, frozen_overrides, prepared)

    def enqueue_prepared(
        self, sub: dict, frozen_overrides: dict, prepared: Prepared
    ) -> int:
        """接受已经验证的冻结轨迹；通用正文不会再走诗词排版。"""
        submission_id = sub["id"]
        prepared.submission_id = submission_id
        job_id = jobs.add_job(submission_id, prepared.gcode_path)
        prog = JobProgress(
            job_id=job_id,
            submission_id=submission_id,
            total=len(prepared.gcode),
            overrides=frozen_overrides,
        )
        with self._lock:
            self._jobs[job_id] = prog
            self._prepared[job_id] = prepared
        jobs.freeze_job(job_id, sub, frozen_overrides, prog.total, self.dry_run)
        submissions.set_submission_status(submission_id, "queued", job_id)
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
                prog.finished_at = now()
                jobs.update_job(job_id, "cancelled", finished_at=prog.finished_at)
                submissions.set_submission_status(prog.submission_id, "pending", job_id)

    def cancel_active(self) -> None:
        """中止排队和正在执行的任务；实时设备急停由控制器负责。"""
        for job in self.all_jobs():
            if job.status in ("queued", "printing"):
                try:
                    self.cancel(job.job_id)
                except ValueError:
                    pass

    def emergency_stop(self) -> bool:
        self.cancel_active()
        return self.controller.emergency_interrupt()

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
                jobs.update_job(job_id, "failed", error=prog.message, finished_at=now())
                return
            self._current = prog
            prog.status = "printing"
            prog.started_at = now()
        submissions.set_submission_status(prog.submission_id, "printing", job_id)
        jobs.update_job(job_id, "printing", started_at=prog.started_at)

        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUTPUT_DIR / f"job{job_id}.gcode"
            path.write_text("\n".join(prepared.gcode), encoding="utf-8")
            prepared.gcode_path = str(path)
            prog.total = len(prepared.gcode)
            # 写入实际生成的文件，通信故障时可以逐行回溯，而非猜 output 文件。
            if prepared.gcode_path:
                jobs.update_job(job_id, "printing", gcode_path=prepared.gcode_path)
            if not prepared.can_print:
                raise RuntimeError(
                    "一个字都画不出来。多半是缺少中文字库，或正文里没有可书写的字符。"
                )

            if self.dry_run:
                self._simulate(prog)
            else:
                self._send(prepared, prog)

            prog.status = "done"
            prog.finished_at = now()
            submissions.set_submission_status(prog.submission_id, "done", job_id)
            jobs.update_job(job_id, "done", finished_at=prog.finished_at)

        except grbl.CancelledError as exc:
            # 取消是正常操作，不是故障 —— 不该标成 failed 吓人，
            # 也不该在界面上显示成红色错误
            prog.status = "cancelled"
            prog.message = str(exc)
            prog.finished_at = now()
            submissions.set_submission_status(prog.submission_id, "pending", job_id)
            jobs.update_job(
                job_id, "cancelled", error=prog.message, finished_at=prog.finished_at
            )

        except Exception as exc:  # noqa: BLE001
            self._fail(job_id, str(exc))
        finally:
            self._current = None
            self._cancel.discard(job_id)
            self._prepared.pop(job_id, None)
            jobs.save_progress(job_id, prog.sent, prog.total)

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
        def on_progress(sent: int, total: int) -> None:
            prog.sent, prog.total = sent, total

        self.controller.send_program(
            prepared.machine_snapshot,
            prepared.gcode,
            submission_id=prepared.submission_id,
            on_progress=on_progress,
            cancelled=lambda: prog.job_id in self._cancel,
        )

    def _fail(self, job_id: int, message: str) -> None:
        with self._lock:
            prog = self._jobs.get(job_id)
        if prog:
            prog.status = "failed"
            prog.message = message
            prog.finished_at = now()
            submissions.set_submission_status(prog.submission_id, "failed", job_id)
        jobs.update_job(job_id, "failed", error=message, finished_at=now())
