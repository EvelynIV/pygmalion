"""
GRBL 串口驱动。

安全约定（用户明确要求，代码强制执行）：
  1. 绝不刷固件。本模块只能通过串口发 G-code 和 $ 指令，没有任何写 flash 的路径。
  2. 任何**写入性**操作（改 $ 参数、$H 归零、发运动指令）之前，
     必须先调用 backup_before_write()，把机器当前全部设置导出到 backups/。
     这一步不是靠使用者自觉 —— write_settings() 和 jog()/run_program() 会检查
     本次进程内是否已备份，没备份就抛 MachineSafetyError。
  3. 只读操作（$$、$I、$G、$#、?）不受限制，随时可以发。

流控：发送大量短指令时用 character-counting 协议（GRBL 的接收缓冲区是 128 字节），
      按已发送但未确认的字节数控制，避免缓冲区溢出丢指令。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import re
import time
import threading
import math
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import serial  # pyserial

from ..config import BACKUP_DIR

# GRBL 的串口接收缓冲区大小。发满就会丢字符，必须按这个数字做流控。
RX_BUFFER_SIZE = 128
# 预留一点余量，别把缓冲区填得刚刚好
RX_BUFFER_MARGIN = 16


class GrblError(RuntimeError):
    """GRBL 返回 error:N 或串口通信失败。"""


class MachineSafetyError(RuntimeError):
    """触碰安全约定时抛出，例如未备份就想改机器设置。"""


class AlarmError(GrblError):
    """GRBL 进入 ALARM 状态，机器已锁定，需要 $X 解锁或 $H 归零。"""


class CancelledError(GrblError):
    """
    用户主动取消。

    单独一个类型是为了让上层能区分"取消"和"出错" —— 取消是正常操作，
    不该在界面上显示成红色错误，也不该把投稿标成 failed。
    """


# --------------------------------------------------------------------------
# 串口枚举与自动识别（只读）
# --------------------------------------------------------------------------


def serial_ports() -> list[dict[str, str | int | None]]:
    """列出系统可见串口；只读取操作系统信息，不打开任何设备。"""
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception:  # noqa: BLE001 - 枚举失败时让界面显示空列表
        return []
    return [
        {
            "device": str(item.device),
            "description": str(item.description or "未知设备"),
            "hwid": str(item.hwid or ""),
            "vid": item.vid,
            "pid": item.pid,
        }
        for item in sorted(ports, key=lambda item: str(item.device).casefold())
    ]


def probe_grbl_ports(
    baud: int = 115200, devices: Iterable[str] | None = None
) -> list[dict[str, object]]:
    """用 `$I` 识别 GRBL；默认跳过蓝牙虚拟串口，可显式指定蓝牙设备。"""
    allowed = (
        {str(item).casefold() for item in devices} if devices is not None else None
    )
    results: list[dict[str, object]] = []
    for info in serial_ports():
        device = str(info["device"])
        if allowed is not None and device.casefold() not in allowed:
            continue
        result: dict[str, object] = dict(info)
        result.update({"reachable": False, "grbl": False, "firmware": "", "error": ""})
        # Windows 蓝牙串口的打开阶段可能阻塞数秒；Serial.timeout 仅约束读取。
        # 枚举中仍保留它们供手动选择，自动扫描不建立蓝牙连接。
        hwid = str(info.get("hwid") or "").upper()
        description = str(info.get("description") or "").upper()
        if allowed is None and (
            hwid.startswith("BTH")
            or "BLUETOOTH" in description
            or "蓝牙" in description
        ):
            result.update(
                {"skipped": True, "error": "蓝牙虚拟串口不参与自动扫描，请手动选择"}
            )
            results.append(result)
            continue
        started = time.monotonic()
        ser: serial.Serial | None = None
        lines: list[str] = []
        try:
            # 只读探测仍可能让某些 USB 串口板复位，因此由管理端显式触发，
            # 且服务有正在书写的任务时会拒绝调用此函数。
            ser = serial.Serial(device, baud, timeout=0.15, write_timeout=1.0)
            time.sleep(0.3)
            # 不发空行：空行也产生 ok，旧实现会在 $I 的版本应答之前退出。
            # 不清空启动信息：部分控制板打开串口后复位，欢迎信息也可识别 GRBL。
            ser.write(b"$I\n")
            deadline = time.monotonic() + 3.0
            retry_at = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if retry_at is not None and time.monotonic() >= retry_at:
                    # 首次查询可能落在控制板启动期间；仅重试一次只读查询。
                    ser.write(b"$I\n")
                    retry_at = None
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                lines.append(line)
                if "grbl" in line.casefold() or line.startswith("[VER:"):
                    result["grbl"] = True
                    result["firmware"] = line
                # 缓冲区可能已有旧 ok；没有版本证据时不能据此结束探测。
                if line == "ok" and result["grbl"]:
                    break
            result["reachable"] = bool(lines)
            if not result["firmware"] and lines:
                result["firmware"] = lines[0]
            if not lines:
                result["error"] = "无响应"
            elif not result["grbl"]:
                result["error"] = "设备有响应，但未返回 GRBL 版本信息"
        except (OSError, serial.SerialException) as exc:
            result["error"] = str(exc)
        finally:
            if ser and ser.is_open:
                ser.close()
        result["responses"] = lines
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        logger.info(
            "串口探测 %s：耗时 %s ms，GRBL=%s，应答=%r，错误=%s",
            device,
            result["elapsed_ms"],
            result["grbl"],
            lines,
            result["error"],
        )
        results.append(result)
    return results


def resolve_port(port: str | None, baud: int = 115200) -> str:
    """解析手动端口或自动检测结果；不猜测未验证的普通串口。"""
    requested = (port or "auto").strip()
    if requested and requested.casefold() != "auto":
        return requested
    matched = [item for item in probe_grbl_ports(baud) if item.get("grbl")]
    if len(matched) == 1:
        return str(matched[0]["device"])
    if not matched:
        raise GrblError(
            "自动检测未找到可响应的 GRBL 串口；请连接设备后重试，或手动选择端口"
        )
    names = "、".join(str(item["device"]) for item in matched)
    raise GrblError(f"自动检测到多个 GRBL 串口：{names}；请在管理页手动选择")


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------

_SETTING_RE = re.compile(r"^\$(\d+|[A-Z]+)=")


def backup_settings(link: "GrblLink", reason: str = "manual") -> Path:
    """
    把机器当前的全部设置导出到 backups/ 目录，返回备份文件路径。

    导出内容：
      $$  全部 $ 参数（steps/mm、行程、加速度等）
      $I  固件版本与编译信息
      $G  当前模态（单位、坐标系、进给率）
      $#  坐标偏移（G54-G59、G28、G30、刀具长度）
      $N  启动块
      $S  当前 spindle/laser 的 S 值

    这些是恢复到当前状态的完整依据。文件是纯文本，出问题时可以逐行发回去。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    sections = {
        "设置 ($$)": "$$",
        "固件信息 ($I)": "$I",
        "当前模态 ($G)": "$G",
        "坐标偏移 ($#)": "$#",
        "启动块 ($N)": "$N",
        "主轴/激光 ($S)": "$S",
    }

    lines: list[str] = [
        "# GRBL 机器设置备份",
        f"# 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# 原因: {reason}",
        f"# 端口: {link.port} @ {link.baud}",
        "# 恢复方法: 逐行把下面的 $ 指令发回机器即可（顺序无所谓）",
        "",
    ]

    collected: dict[str, list[str]] = {}
    for label, cmd in sections.items():
        try:
            collected[label] = link.query(cmd)
        except GrblError as exc:
            collected[label] = [f"# 读取失败: {exc}"]

    for label, body in collected.items():
        lines.append(f"# ---------- {label} ----------")
        lines.extend(body)
        lines.append("")

    # 参数表单独抽出来存一份，方便程序化对比和恢复
    params = [ln for ln in collected.get("设置 ($$)", []) if _SETTING_RE.match(ln)]
    lines.append("# ---------- 关键参数速查 ----------")
    lines.extend(params or ["# 未能读到任何 $ 参数"])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"grbl-settings-{stamp}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")

    # 同时更新一个 latest 副本，方便程序快速取用
    (BACKUP_DIR / "grbl-settings-latest.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    return path


def latest_backup() -> Path | None:
    """返回最近一次备份文件，没有则 None。"""
    p = BACKUP_DIR / "grbl-settings-latest.txt"
    return p if p.exists() else None


def ensure_backup_today(
    link: "GrblLink", reason: str = "写入前自动备份"
) -> tuple[Path, bool]:
    """
    确保今天已经有一份机器设置备份，有就复用。返回 (路径, 是否新建)。

    安全闸门要求"本次必须备份过"才放行写入。但手动点动、连续微调这类操作
    会触发很多次写入，每次都新建备份会堆一堆重复文件。所以这里检测当天的
    备份是否已存在 —— 存在就复用它并把闸门放行。

    **保证没有放松**：任何写入之前，一定有一份当天的完整 `$$` 快照。
    """
    latest = latest_backup()
    today = datetime.now().strftime("%Y-%m-%d")
    if latest and latest.exists():
        try:
            if today in latest.read_text(encoding="utf-8")[:500]:
                link.mark_backed_up()
                return latest, False
        except OSError:
            pass

    return link.backup(reason=reason), True


# --------------------------------------------------------------------------
# 串口连接
# --------------------------------------------------------------------------


def settings_commands(changes: dict[str, str | float | int]) -> list[str]:
    commands = []
    for key, value in changes.items():
        if not isinstance(key, str) or not re.fullmatch(r"\$\d{1,3}", key):
            raise ValueError(f"参数编号不合法: {key}")
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"参数 {key} 必须为数字") from None
        if not math.isfinite(number):
            raise ValueError(f"参数 {key} 必须为有限数值")
        commands.append(f"{key}={number:g}")
    return commands


_active_link: GrblLink | None = None


def active_link() -> GrblLink | None:
    return _active_link


class GrblLink:
    """与 GRBL 主板的串口连接。建议用 with 语句管理生命周期。"""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0):
        self.port = resolve_port(port, baud)
        self.baud = baud
        self.timeout = timeout
        self._ser: serial.Serial | None = None
        self._backed_up = False
        self._buffer_used = 0
        self._emergency = threading.Event()

    # -- 连接管理 ---------------------------------------------------------

    def open(self) -> "GrblLink":
        """打开串口并等 GRBL 的欢迎信息。"""
        if self._ser and self._ser.is_open:
            return self
        try:
            self._ser = serial.Serial(
                self.port, self.baud, timeout=self.timeout, write_timeout=2.0
            )
        except serial.SerialException as exc:
            raise GrblError(f"打不开串口 {self.port}: {exc}") from exc

        # GRBL 上电/复位后会吐一段欢迎信息，里面通常带版本号。
        # 如果已经启动过就不会再发，所以这里只是尽力读一下，读不到不算错。
        time.sleep(0.3)
        try:
            self._ser.reset_input_buffer()
            self._ser.write(b"\r\n")
            time.sleep(0.3)
            greeting = self._ser.read(self._ser.in_waiting or 1).decode(
                "ascii", "replace"
            )
            if greeting.strip():
                self._greeting = greeting.strip()
        except (OSError, serial.SerialException):
            pass
        return self

    def close(self) -> None:
        global _active_link
        if _active_link is self:
            _active_link = None
        if self._ser and self._ser.is_open:
            self._ser.close()

    def __enter__(self) -> "GrblLink":
        global _active_link
        link = self.open()
        _active_link = link
        return link

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def _require_open(self) -> serial.Serial:
        if not self.is_open:
            raise GrblError("串口未打开")
        return self._ser  # type: ignore[return-value]

    # -- 底层收发 ---------------------------------------------------------

    def _read_line(self, retries: int = 3) -> str:
        """
        读一行。超时重试几次再放弃。

        为什么要重试：GRBL 在规划缓冲区排满、或者正在做较长的运动时，
        会推迟发 `ok`。单次读超时就判失败太急躁 —— 一次误判会让整个
        书写任务半途中止，而机器其实还在正常干活。重试几次给足了余量，
        真正断线的情况仍会如实报错。
        """
        ser = self._require_open()
        for _ in range(max(1, retries)):
            if self._emergency.is_set():
                raise CancelledError("急停：请重新对位后开始新任务")
            raw = ser.readline()
            if raw:
                return raw.decode("ascii", "replace").strip()
        raise GrblError("读取超时：机器连续多次没有回应")

    def _consume_response(self, response_timeout: float = 30.0) -> str:
        """
        读一行应答，直到拿到 ok / error / ALARM 之类的结论性回包。
        直接吞掉 GRBL 主动推送的状态行（以 < 开头）。
        """
        # Serial.timeout 保持较短，让状态查询和界面响应及时；但它不能
        # 充当 GRBL `ok` 的总等待上限。部分控制板会在规划器满时延后 ok。
        # 旧实现中 _read_line 的三次短读会直接抛出，实际只等了约 3 秒。
        deadline = time.monotonic() + max(response_timeout, self.timeout)
        last_read_error: GrblError | None = None
        while time.monotonic() < deadline:
            if self._emergency.is_set():
                raise CancelledError("急停：未完成的运动已丢弃，请重新对位")
            try:
                line = self._read_line()
            except GrblError as exc:
                # 不重发：该命令可能已在规划器中，重发会重复写一笔。
                last_read_error = exc
                continue
            if not line or line.startswith("<"):
                continue
            if line == "ok" or line.startswith("ok"):
                return line
            if line.startswith("error:"):
                raise GrblError(f"GRBL 错误: {line}")
            if line.startswith("ALARM:"):
                raise AlarmError(
                    f"机器进入 ALARM 状态: {line}（需发 $X 解锁或 $H 归零）"
                )
            if line.startswith("[") and line.endswith("]"):
                return line  # $$ / $I / $G 这类信息行
            return line
        detail = f"（串口单次读取超时：{last_read_error}）" if last_read_error else ""
        raise GrblError(f"等待 GRBL 应答超时 {response_timeout:g} 秒{detail}")

    def send(self, line: str) -> str:
        """发一行 G-code 或 $ 指令，阻塞等应答。"""
        ser = self._require_open()
        payload = (line.strip() + "\n").encode("ascii")
        ser.write(payload)
        return self._consume_response()

    def emergency_interrupt(self) -> None:
        self._emergency.set()
        self._realtime(b"!\x18")

    def query(self, cmd: str, max_lines: int = 2000) -> list[str]:
        """
        发送 $$ / $I / $G / $# 这类查询指令，收集全部返回行直到 ok。
        这些是只读操作，随时可以发。
        """
        ser = self._require_open()
        ser.write((cmd.strip() + "\n").encode("ascii"))
        out: list[str] = []
        deadline = time.time() + 10.0
        while time.time() < deadline and len(out) < max_lines:
            try:
                line = self._read_line()
            except GrblError:
                break
            if line.startswith("ok"):
                break
            if line.startswith("error:"):
                raise GrblError(f"GRBL 错误: {line}")
            if line:
                out.append(line)
        return out

    # -- 实时指令（不占缓冲区，立即生效）----------------------------------

    def _realtime(self, byte: bytes) -> None:
        """发实时指令。这些指令单字节、不排队、立刻生效。"""
        self._require_open().write(byte)

    def wait_idle(self, timeout: float = 60.0, poll: float = 0.15) -> dict[str, str]:
        """
        等机器停止运动，返回最终状态。

        为什么必须等：GRBL 回的 `ok` 只表示指令被**接受**进规划缓冲区，
        不代表运动**执行完了** —— 缓冲区有十几条指令的余量，机器可能还在
        走。发完就走立刻查 `?`，读到的位置是运动中途的快照，会让人以为
        移动量不对。

        状态含义：Run/Jog 是运动中；Idle 是停稳；Alarm/Door 是异常锁定，
        这两种不再等（等也等不到）。
        """
        deadline = time.time() + timeout
        last: dict[str, str] = {}
        while time.time() < deadline:
            if self._emergency.is_set():
                raise CancelledError("急停：未完成的动作已丢弃")
            try:
                last = self.status()
            except GrblError:
                time.sleep(poll)
                continue
            state = last.get("state", "")
            if state in ("Alarm", "Door", "Sleep"):
                raise AlarmError(f"机器未完成运动，状态：{state}")
            if state == "Idle":
                return last
            time.sleep(poll)
        raise GrblError(f"等待机器停止超时，最后状态：{last}")

    def status(self) -> dict[str, str]:
        """
        查询实时状态（?）。返回形如
          {'state': 'Idle', 'MPos': '10.000,20.000,0.000', 'Bf': '15,128', 'FS': '0,0'}
        """
        while self._require_open().in_waiting:
            self._read_line()
        self._realtime(b"?")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = self._read_line()
            if line.startswith("<") and line.endswith(">"):
                body = line[1:-1]
                parts = body.split("|")
                out = {"state": parts[0]}
                for p in parts[1:]:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        out[k] = v
                return out
        raise GrblError("状态查询超时")

    # -- 暂停 / 恢复 / 急停 --------------------------------------------------

    def feed_hold(self) -> None:
        """
        `!` 进给保持 —— 立即暂停运动，机器保持通电。

        ⚠️ **发完这个必须配套 clear_hold() 或 emergency_stop()**，
        否则机器会永久停在 Hold 状态 —— Hold 下 GRBL 忽略一切运动指令，
        表现就是"再也动不了"。串口断了也不会自动恢复，因为 GRBL 不知道
        主机已经走了。

        正常取消不要用这个，用 run_program 的 cancel 机制（它会干净地
        收尾）。这个只给急停用。
        """
        self._realtime(b"!")

    def resume(self) -> None:
        """
        `~` 恢复被 `!` 暂停的运动。

        注意：它会**继续执行缓冲区里剩下的运动**。想中止而不是暂停，
        要用 clear_hold()。
        """
        self._realtime(b"~")

    def soft_reset(self) -> None:
        """
        Ctrl-X 软复位。清空规划缓冲区和接收缓冲区，回到 Idle。

        GRBL 1.1 的软复位会从 EEPROM 恢复机器位置，但为保险起见，
        复位后应该让用户确认一下笔尖位置。
        """
        self._realtime(b"\x18")
        time.sleep(0.6)
        # 复位后 GRBL 会重发欢迎信息，读掉它，免得污染后续应答
        try:
            if self._ser:
                self._ser.reset_input_buffer()
        except (OSError, serial.SerialException):
            pass

    def state(self) -> str:
        """当前状态字，如 Idle / Run / Hold / Alarm。读不到返回空串。"""
        try:
            return self.status().get("state", "")
        except GrblError:
            return ""

    def wait_state(self, prefix: str, timeout: float = 10.0, poll: float = 0.1) -> bool:
        """等状态变成指定前缀（如 "Hold"）。返回是否等到。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state().startswith(prefix):
                return True
            time.sleep(poll)
        return False

    def clear_hold(self) -> bool:
        """
        把机器从 Hold 里放出来，回到 Idle。返回是否成功。

        **用软复位而不是 `~`。** 因为 `~` 会继续执行规划缓冲区里剩下的
        运动 —— 那是"暂停后继续"，不是"中止"。要中止就得把缓冲区丢掉，
        只有软复位做得到。

        这一条是保命的：只要机器进过 Hold，就必须有人把它弄出来。
        Hold 状态下 GRBL 忽略一切运动指令，串口断开也不会自动恢复，
        在用户看来就是"机器死了"。
        """
        if not self.state().startswith("Hold"):
            return True

        self.soft_reset()
        if self.wait_state("Idle", timeout=4.0):
            return True
        # 软复位后可能落进 ALARM，再试一次解锁
        try:
            self.unlock()  # $X
            return self.wait_state("Idle", timeout=3.0)
        except GrblError:
            return False

    def emergency_stop(self, pen_up_z: float | None = None) -> str:
        """
        急停：立即停下，丢掉未完成的运动，抬笔，**确保不留下 Hold 状态**。

        这是"按了就要马上停"的路径。代价是规划缓冲区里没走完的运动被丢弃，
        笔可能停在半路 —— 这是急停本来就该有的代价。

        返回结束时的状态字。

        为什么 finally 里一定要 clear_hold：如果只发 `!` 就退出，机器会
        永久卡在 Hold。这个 bug 真实发生过 —— 终止按钮按完机器就再也不动了。
        """
        try:
            self.feed_hold()
            self.wait_state("Hold", timeout=5.0)
        except GrblError:
            pass
        finally:
            self.clear_hold()  # 无论如何都要解掉 Hold

        # 回到 Idle 之后再抬笔（这时候指令才会被正常处理）。
        # 用 _recover_move 而不是 run_program —— 恢复动作不能被备份闸门挡住。
        if pen_up_z is not None and self.is_open:
            if self.state().startswith("Idle"):
                self._recover_move(f"G0 Z{pen_up_z}")

        return self.state()

    def recover_if_stuck(self, pen_up_z: float | None = None) -> str:
        """
        开机/操作前的自检：如果机器卡在 Hold 或 ALARM，把它救回来。

        对位面板和每次书写前都该调用一次 —— 免得用户面对一台"按了没反应"
        的机器不知道该干什么。
        """
        st = self.state()
        if st.startswith("Hold"):
            self.clear_hold()
        elif st.startswith("Alarm"):
            try:
                self.unlock()  # $X
            except GrblError:
                pass

        if pen_up_z is not None and self.state().startswith("Idle"):
            self._recover_move(f"G0 Z{pen_up_z}")
        return self.state()

    def unlock(self) -> None:
        """$X 解除 ALARM 锁定。注意：这不会归零，只是允许继续发指令。"""
        self.send("$X")

    def home(self) -> None:
        """$H 归零。机器会自己找限位开关，过程中会移动 —— 属于写入性操作。"""
        self._require_backup("归零 ($H)")
        self.send("$H")

    # -- 安全闸门 ---------------------------------------------------------

    def _require_backup(self, action: str) -> None:
        """
        写入性操作前必须已经备份过。这是用户要求的硬约束，不是建议。
        """
        if not self._backed_up:
            raise MachineSafetyError(
                f"拒绝执行「{action}」：本次运行还没有备份机器设置。"
                f"请先调用 backup_settings(link)，确认无误后再改。"
            )

    def mark_backed_up(self) -> None:
        """标记已完成备份，之后的写入性操作才被放行。"""
        self._backed_up = True

    def _recover_move(self, line: str) -> None:
        """
        恢复用的最小运动（目前只用来抬笔）。**刻意绕开备份闸门。**

        闸门的目的是"改机器设置或跑整段运动程序之前先留一份快照"。
        但要把机器从卡死状态里救出来的时候，先要求备份是本末倒置：
        机器可能正卡着不应答，备份必然失败，于是你永远卡在那里 ——
        这是真实发生过的死锁。

        这里只发**一条**把笔抬起来的指令：不改变任何机器设置、不写 EEPROM、
        不跑用户内容，风险为零。所以豁免是安全且必要的。
        """
        try:
            self.send(line)
        except GrblError:
            pass

    def backup(self, reason: str = "manual") -> Path:
        """备份并放行后续的写入性操作。"""
        path = backup_settings(self, reason)
        self.mark_backed_up()
        return path

    # -- 写入性操作 -------------------------------------------------------

    def write_settings(
        self, changes: dict[str, str | float | int], dry_run: bool = True
    ) -> list[str]:
        """
        修改 $ 参数。

        dry_run=True（默认）只返回将要发送的指令，不真的写。
        dry_run=False 才会写，且要求先备份。

        changes 例如 {'$112': 3000, '$32': 0}
        """
        cmds = settings_commands(changes)

        if dry_run:
            return [f"[预览] {c}" for c in cmds]

        self._require_backup("修改 $ 参数")
        out = []
        for cmd in cmds:
            out.append(f"{cmd} -> {self.send(cmd)}")
        return out

    # -- 发送程序 ---------------------------------------------------------

    def run_program(
        self,
        lines: Iterable[str],
        on_progress: Callable[[int, int], None] | None = None,
        cancel_flag: Callable[[], bool] | None = None,
        pen_up_z: float | None = None,
    ) -> int:
        """
        发送一段 G-code 程序。用 character-counting 协议做流控。

        正确性关键（这里踩过坑）
        ------------------------
        GRBL 每回一个 `ok`，从接收缓冲区里释放的是**那一条指令的实际长度**。
        如果按固定值扣减（比如一律扣 32 字节），而实际指令平均只有 25~30 字节，
        就会每次少扣几个字节 —— 误差随程序长度累积，最后代码以为缓冲区还有
        空间、实际已经溢出。机器收到被截断的半条指令，回 `error:1`
        （"G-code words consist of a letter and a value. Letter was not found."），
        或者干脆噎住不应答，表现为读取超时。

        这正是"短诗能写、长诗必失败"的原因 —— 程序越短，累积误差越小。

        所以这里用一个 FIFO 记录每条已发出但还没被 ok 确认的指令长度，
        收到一个 ok 就弹出队首、按它的真实长度扣减。
        """
        self._require_backup("发送运动程序")
        ser = self._require_open()

        program = [
            ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(";")
        ]
        total = len(program)

        # (占用字节数, 程序行号, 指令)：故障日志可定位到具体 GCode。
        inflight: deque[tuple[int, int, str]] = deque()
        pending = 0
        idx = 0
        cancelled = False

        while idx < total:
            if cancel_flag and cancel_flag():
                # ── 取消：停止发送新指令，但**不**急停 ──
                #
                # 这里以前发的是 `!`（进给保持），结果是机器永久卡在 Hold：
                # Hold 状态下 GRBL 忽略一切运动指令，串口断开也不会自动恢复，
                # 用户看到的就是"按了终止以后机器再也不动了"。
                #
                # 正确做法是让已经发进缓冲区的那点运动走完 —— 那只有几秒钟的
                # 量，走完机器自然回到 Idle。不丢位置、不留状态、笔画也收得住。
                # 想立刻停是急停（emergency_stop），那是另一个按钮的事。
                cancelled = True
                break

            line = program[idx]
            nbytes = len(line) + 1  # +1 是换行符，它也占缓冲区

            if pending + nbytes <= RX_BUFFER_SIZE - RX_BUFFER_MARGIN:
                ser.write((line + "\n").encode("ascii"))
                inflight.append((nbytes, idx + 1, line))
                pending += nbytes
                idx += 1
                if on_progress:
                    on_progress(idx, total)
                continue

            # 缓冲区余量不够，等一个 ok 腾出空间
            try:
                self._consume_response()
            except CancelledError:
                raise
            except GrblError as exc:
                if inflight:
                    _, waiting_line_no, waiting_line = inflight[0]
                    raise GrblError(
                        f"{exc}；发送进度 {idx}/{total}，"
                        f"最早未确认的是程序第 {waiting_line_no} 行：{waiting_line}"
                    ) from exc
                raise
            if inflight:
                pending -= inflight.popleft()[0]

        # 收尾：把剩下的 ok 全部读干净。取消时也必须收 ——
        # 残留的 ok 会被下一次连接当成自己的应答，整体错位一条。
        while inflight:
            try:
                self._consume_response()
            except CancelledError:
                raise
            except GrblError as exc:
                _, waiting_line_no, waiting_line = inflight[0]
                raise GrblError(
                    f"{exc}；发送进度 {idx}/{total}，"
                    f"最早未确认的是程序第 {waiting_line_no} 行：{waiting_line}"
                ) from exc
            inflight.popleft()

        if cancelled:
            self._finish_cancel(pen_up_z, idx, total)
            raise CancelledError(f"用户取消，已停在第 {idx}/{total} 行")

        return total

    def _finish_cancel(self, pen_up_z: float | None, idx: int, total: int) -> None:
        """
        取消后的收尾：等机器停稳，抬笔，确认没有卡在异常状态。

        抬笔是为了不让笔一直压在纸上留墨点。等停稳是为了别在机器还在动的时候
        插指令 —— 那会排进缓冲区，在错误的位置执行。
        """
        try:
            self.wait_idle(timeout=30.0)
            if pen_up_z is not None:
                self.send(f"G0 Z{pen_up_z}")
        except GrblError:
            pass

        # 万一因为别的原因进了 Hold（比如用户同时按了急停），兜底解掉。
        # 宁可多查一次，也不能留下一台"按了没反应"的机器。
        try:
            if self.state().startswith("Hold"):
                self.clear_hold()
        except GrblError:
            pass
