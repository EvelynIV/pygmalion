"""串口生命周期、设备独占、手动控制会话与 GRBL 操作。"""

from __future__ import annotations

import functools
import logging
import math
import re
import threading
from contextlib import contextmanager
from typing import Any

from ..config import CONFIG
from . import grbl
from .errors import MachineError

logger = logging.getLogger(__name__)


def device_guard(function):
    @functools.wraps(function)
    def guarded(controller, *args, session=None, **kwargs):
        with controller.operation(
            session=session, reuse=function.__name__ in ("inspect", "jog")
        ):
            return function(controller, *args, **kwargs)

    return guarded


class DeviceController:
    def __init__(self, busy=lambda: False):
        self.busy = busy
        self.device_lock = threading.RLock()
        self.manual_control_lock = threading.Lock()
        self._manual_session = None
        self._manual_link = None
        self._manual_key = None
        self._link = None

    def begin_session(self):
        if not self.manual_control_lock.acquire(blocking=False):
            return None
        self._manual_session = object()
        return self._manual_session

    def end_session(self, session):
        if session is not self._manual_session:
            return
        try:
            self.close_manual_connection()
        finally:
            self._manual_session = None
            self.manual_control_lock.release()

    @contextmanager
    def operation(self, *, session=None, reuse=False):
        if (
            self.busy()
            or (
                self.manual_control_lock.locked()
                and (session is None or session is not self._manual_session)
            )
            or not self.device_lock.acquire(blocking=False)
        ):
            raise MachineError(
                "busy", {"ok": False, "error": "机器正在书写或处理另一项操作"}
            )
        try:
            if not reuse:
                self.close_manual_connection()
            yield
        finally:
            self.device_lock.release()

    def emergency_interrupt(self):
        link = self._link or grbl.active_link()
        if link and link.is_open:
            link.emergency_interrupt()
            return True
        return False

    def stop(self):
        self.close_manual_connection()

    def send_program(self, machine, program, *, submission_id, on_progress, cancelled):
        try:
            with self.device_connection(machine) as link:
                self._link = link
                grbl.ensure_backup_today(link, reason=f"书写投稿 #{submission_id}")
                link.recover_if_stuck(pen_up_z=machine["z_off"])
                link.run_program(
                    program,
                    on_progress=on_progress,
                    cancel_flag=cancelled,
                    pen_up_z=machine["z_off"],
                )
                link.wait_idle(timeout=300)
                if cancelled():
                    raise grbl.CancelledError("任务已中止，缓冲区动作已结束")
        finally:
            self._link = None

    def close_manual_connection(self) -> None:
        """在设备锁内释放手动控制连接，供扫描、换端口或书写接管。"""
        with self.device_lock:
            link, self._manual_link = (self._manual_link, None)
            self._manual_key = None
            if link is not None:
                link.__exit__(None, None, None)

    def manual_status(self) -> dict[str, str] | None:
        """实时控制空闲时查询坐标；书写中不查询，也不隐式打开设备。"""
        if self.busy() or not self.device_lock.acquire(blocking=False):
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
                self._manual_link, self._manual_key = (link, key)
            try:
                yield self._manual_link
            except Exception:
                self.close_manual_connection()
                raise

    def estop(self, data):
        """
        急停。立即停下并**确保机器不会卡在 Hold**。

        和「中止」的区别：
          中止   停止发送新指令，让已经排进缓冲区的运动走完（几秒钟），
                 位置不丢、文件可续写。日常用这个。
          急停   立刻停，丢弃未完成的运动，笔可能停在半路。出事时用这个。

        两种情况都不会把机器留在 Hold 状态 —— 那是之前的 bug，会让机器
        "按了没反应"。
        """
        m = CONFIG["machine"]
        if self.emergency_interrupt():
            return {"ok": True, "state": "急停指令已发送，请重新读取设备状态并对位"}
        try:
            with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
                state = link.emergency_stop(pen_up_z=m["z_off"])
            return {"ok": True, "state": state}
        except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
            raise MachineError(
                "unavailable", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )

    @device_guard
    def recover(self, data):
        """
        把机器从 Hold / Alarm 里救出来。

        防的就是"机器卡住了按什么都没反应" —— 这个接口只做恢复，
        不做任何运动（除了抬笔），随时可以安全地点。
        """
        m = CONFIG["machine"]
        try:
            with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
                before = link.state()
                after = link.recover_if_stuck(pen_up_z=m["z_off"])
            return {
                "ok": True,
                "before": before,
                "after": after,
                "recovered": before != after,
            }
        except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
            raise MachineError(
                "unavailable", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )

    def serial_ports(self):
        """列出系统串口；不打开设备、不发送串口数据。"""
        return {
            "ok": True,
            "configured_port": CONFIG["machine"].get("port", "auto"),
            "baud": CONFIG["machine"].get("baud", 115200),
            "ports": grbl.serial_ports(),
        }

    @device_guard
    def set_serial_port(self, data):
        """保存手动选择的串口，或保存 `auto` 让书写前自动识别 GRBL。"""
        data = data or {}
        port = str(data.get("port") or "").strip()
        if not port:
            raise MachineError("invalid", {"ok": False, "error": "请选择串口"})
        if port.casefold() == "auto":
            port = "auto"
        elif not re.fullmatch("COM[1-9][0-9]*", port, flags=re.IGNORECASE):
            raise MachineError(
                "invalid", {"ok": False, "error": "串口格式应为 COM 加数字，例如 COM8"}
            )
        else:
            port = port.upper()
        from .. import config as config_mod

        config_mod.save_section("machine", {"port": port})
        return {"ok": True, "port": port}

    @device_guard
    def detect_serial_port(self, data):
        """显式扫描并以 `$I` 只读识别 GRBL；书写中绝不探测。"""
        if self.busy():
            raise MachineError(
                "busy",
                {"ok": False, "error": "机器正在书写，不能扫描串口；请等待任务结束"},
            )
        baud = int(CONFIG["machine"].get("baud", 115200))
        checked = grbl.probe_grbl_ports(baud)
        matches = [item for item in checked if item.get("grbl")]
        if len(matches) != 1:
            if not matches:
                failures = [
                    f"{item['device']}：{item.get('error') or '未识别到 GRBL'}"
                    for item in checked
                    if not item.get("skipped")
                ]
                detail = (
                    "；".join(failures)
                    if failures
                    else "没有可扫描的有线串口；蓝牙设备请手动选择"
                )
                raise MachineError(
                    "not_found",
                    {
                        "ok": False,
                        "error": f"未检测到可响应的 GRBL 设备：{detail}",
                        "ports": checked,
                    },
                )
            raise MachineError(
                "busy",
                {
                    "ok": False,
                    "error": "检测到多个 GRBL 设备，请手动选择",
                    "ports": checked,
                },
            )
        port = str(matches[0]["device"])
        from .. import config as config_mod

        config_mod.save_section("machine", {"port": port})
        return {
            "ok": True,
            "port": port,
            "ports": checked,
            "firmware": matches[0].get("firmware", ""),
        }

    @device_guard
    def inspect(self):
        """
        查询机器状态。只读 —— 发 $$ 和 ? 不会让机器动。

        这是唯一主动碰串口的只读接口。查不到就如实报错，不假装正常。
        """
        m = CONFIG["machine"]
        try:
            with self.device_connection(m, reuse=True) as link:
                state = link.status()
                info = link.query("$I", max_lines=20)
                backup = grbl.latest_backup()
                return {
                    "ok": True,
                    "port": link.port,
                    "configured_port": m["port"],
                    "baud": m["baud"],
                    "state": state,
                    "firmware": info[:3],
                    "backup": str(backup) if backup else None,
                }
        except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
            raise MachineError(
                "unavailable",
                {
                    "ok": False,
                    "port": m["port"],
                    "error": f"{type(exc).__name__}: {exc}",
                    "hint": "检查：kdraw 是否已完全退出（串口独占）、USB 是否插好、COM 口号是否变了",
                },
            )

    @device_guard
    def jog(self, data):
        """
        手动点动 —— 把笔尖对到当前工作原点。

        为什么必须有这个：机器没有限位开关也不归零，工作原点就是笔尖当前所在
        的位置。所以每次书写前都要手动把笔尖移到固定刻度区域左上角，再把那里设为
        零点。没有点动就只能靠手推小车，而手推会让坐标计数失准。

        所有动作都走"先备份再动"的闸门，且限制单次位移上限。
        """
        data = data or {}
        if not isinstance(data.get("action"), str):
            raise MachineError("invalid", {"ok": False, "error": "动作格式不正确"})
        action = data["action"].strip()
        if action not in ("x", "y", "pen_down", "pen_up", "zero", "goto_zero"):
            raise MachineError("invalid", {"ok": False, "error": "不认识的动作"})
        if action in ("x", "y"):
            try:
                distance = float(data.get("dist", 0))
            except (TypeError, ValueError):
                raise MachineError("invalid", {"ok": False, "error": "位移不是数字"})
            if not math.isfinite(distance) or not 0.01 <= abs(distance) <= 60:
                raise MachineError(
                    "invalid", {"ok": False, "error": "单次点动应在 0.01–60 mm 内"}
                )
        m = CONFIG["machine"]
        try:
            with self.device_connection(m, reuse=True) as link:
                backup, _ = grbl.ensure_backup_today(link, reason="手动点动前自动备份")
                if action in ("x", "y"):
                    speed = float(m.get("jog_speed", 500))
                    link.run_program(
                        ["G91", f"G1 {action.upper()}{distance:g} F{speed:g}", "G90"]
                    )
                elif action == "pen_down":
                    link.run_program([f"G1 Z{m['z_on']} F{m['z_speed']}"])
                elif action == "pen_up":
                    link.run_program([f"G0 Z{m['z_off']}"])
                elif action == "zero":
                    link.send("G92 X0 Y0")
                elif action == "goto_zero":
                    link.run_program(["G90", "G0 X0 Y0"])
                else:
                    raise MachineError(
                        "invalid", {"ok": False, "error": f"不认识的动作: {action}"}
                    )
                st = link.wait_idle(poll=0.03)
                return {
                    "ok": True,
                    "action": action,
                    "port": link.port,
                    "state": st,
                    "backup": str(backup),
                }
        except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
            raise MachineError(
                "unavailable",
                {
                    "ok": False,
                    "port": m["port"],
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    @device_guard
    def backup(self):
        """
        备份机器设置。只读操作（发 $$ / $I / $G / $#），不会动机器。

        这也是"改前必须先备份"那条硬约束的入口 —— 任何时候都可以先点这个，
        拿到一份可以逐行发回去的完整快照。
        """
        m = CONFIG["machine"]
        try:
            with grbl.GrblLink(m["port"], m["baud"], timeout=2.0) as link:
                path = grbl.backup_settings(link, reason="管理页手动备份")
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
            raise MachineError(
                "unavailable", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )

    @device_guard
    def write_settings(self, data):
        """默认离线预览；明确确认后备份并写入真实控制板。"""
        changes = data.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise MachineError("invalid", {"ok": False, "error": "没有要修改的参数"})
        if "confirm" in data and (not isinstance(data["confirm"], bool)):
            raise MachineError(
                "invalid", {"ok": False, "error": "确认字段必须为布尔值"}
            )
        try:
            commands = grbl.settings_commands(changes)
        except ValueError as exc:
            raise MachineError("invalid", {"ok": False, "error": str(exc)})
        if data.get("confirm") is not True:
            return {
                "ok": True,
                "dry_run": True,
                "will_send": commands,
                "note": "预览未写入控制板",
            }
        machine = CONFIG["machine"]
        try:
            with grbl.GrblLink(machine["port"], machine["baud"], timeout=2) as link:
                path = link.backup(reason=f"修改参数 {list(changes)}")
                result = link.write_settings(changes, dry_run=False)
            return {"ok": True, "dry_run": False, "backup": str(path), "result": result}
        except Exception as exc:
            logger.exception("控制板参数写入失败")
            raise MachineError(
                "unavailable", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
