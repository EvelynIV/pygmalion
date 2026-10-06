from __future__ import annotations
import logging
import queue
import threading
from contextlib import contextmanager
from typing import Any
from ..config import OUTPUT_DIR
from ..machine import grbl
from ..submissions import repository as submissions
from . import repository as jobs
from ..storage import now
from ..writing.prepare import Prepared, prepare, grid_calibration_error
from .models import JobProgress

logger = logging.getLogger(__name__)


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
        self.manual_control_lock = threading.Lock()
        self.workbench_lock = threading.Lock()
        self._manual_link: grbl.GrblLink | None = None
        self._manual_key: tuple[str, int] | None = None

    def close_manual_connection(self) -> None:
        """在设备锁内释放手动控制连接，供扫描、换端口或书写接管。"""
        with self.device_lock:
            link, self._manual_link = self._manual_link, None
            self._manual_key = None
            if link is not None:
                link.__exit__(None, None, None)

    def manual_status(self) -> dict[str, str] | None:
        """实时控制空闲时查询坐标；书写中不查询，也不隐式打开设备。"""
        if self.current or not self.device_lock.acquire(blocking=False):
            return None
        try:
            if self._manual_link is None:
                return None
            return self._manual_link.status()
        except Exception:
            self.close_manual_connection()
            raise
        finally:
            self.device_lock.release()

    @contextmanager
    def device_connection(self, machine: dict[str, Any], *, reuse: bool = False):
        """手动微调复用串口；书写独占连接并沿用任务冻结的配置。"""
        with self.device_lock:
            if not reuse:
                self.close_manual_connection()
                with grbl.GrblLink(machine["port"], machine["baud"]) as link:
                    yield link
                return
            key = (str(machine["port"]), int(machine["baud"]))
            if self._manual_link is not None and (
                self._manual_key != key or not self._manual_link.is_open
            ):
                self.close_manual_connection()
            if self._manual_link is None:
                link = grbl.GrblLink(*key, timeout=0.15)
                try:
                    link.__enter__()
                except Exception:
                    link.__exit__(None, None, None)
                    raise
                self._manual_link, self._manual_key = link, key
            try:
                yield self._manual_link
            except Exception:
                self.close_manual_connection()
                raise

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
        self.close_manual_connection()

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

    def enqueue_prepared(self, sub: dict, frozen_overrides: dict, prepared: Prepared) -> int:
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
            self._link = None
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
        """真正把 G-code 发给机器。"""
        from ..machine import grbl

        m = prepared.machine_snapshot

        with self.device_connection(m) as link:
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
            prog.finished_at = now()
            submissions.set_submission_status(prog.submission_id, "failed", job_id)
        jobs.update_job(job_id, "failed", error=message, finished_at=now())
