"""机器实时控制：持续串口、有限顺序队列和实时坐标反馈。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ...servicer import machine
from ...servicer.errors import ServiceError
from ..dependencies import get_context

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/api/admin/machine/ws")
async def machine_socket(socket: WebSocket):
    await socket.accept()
    context = get_context(socket)
    session = machine.begin_session(context)
    if session is None:
        await socket.send_json({"type": "error", "error": "另一个页面正在控制机器"})
        await socket.close(code=1008)
        return

    commands: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
    send_lock = asyncio.Lock()
    released = False
    outstanding = 0
    last_id = 0

    async def send(message):
        async with send_lock:
            await socket.send_json(message)

    async def release():
        nonlocal released
        if not released:
            try:
                await run_in_threadpool(machine.end_session, context, session)
            finally:
                released = True

    async def cancel_pending(reason):
        nonlocal outstanding
        while not commands.empty():
            command = commands.get_nowait()
            outstanding -= 1
            await send(
                {
                    "type": "result",
                    "id": command["id"],
                    "ok": False,
                    "cancelled": True,
                    "error": reason,
                }
            )

    async def worker():
        nonlocal outstanding
        previous = None
        while True:
            try:
                command = await asyncio.wait_for(commands.get(), timeout=0.2)
            except TimeoutError:
                try:
                    state = await run_in_threadpool(machine.manual_status, context)
                    if state is not None and state != previous:
                        previous = state
                        await send({"type": "state", "state": state})
                except Exception as exc:  # noqa: BLE001 — 将操作失败转换成既有错误响应
                    await send({"type": "error", "error": str(exc)})
                continue
            kind = command["type"]
            if kind == "disconnect":
                await release()
                await send({"type": "result", "id": command["id"], "ok": True})
                await socket.close()
                return
            try:
                if kind == "inspect":
                    result = await run_in_threadpool(
                        machine.inspect, context, session=session
                    )
                elif kind == "jog":
                    payload = command.get("payload", {})
                    if not isinstance(payload, dict):
                        result = {"ok": False, "error": "点动参数必须是对象"}
                    else:
                        result = await run_in_threadpool(
                            machine.jog, context, payload, session=session
                        )
                elif kind == "backup":
                    result = await run_in_threadpool(
                        machine.backup, context, session=session
                    )
                else:
                    result = await run_in_threadpool(
                        machine.recover, context, {}, session=session
                    )
            except ServiceError as exc:
                result = exc.payload
            except Exception as exc:
                logger.exception("实时控制指令失败")
                result = {"ok": False, "error": str(exc)}
            outstanding -= 1
            await send({**result, "type": "result", "id": command["id"]})
            if not result.get("ok"):
                await cancel_pending("前一条指令失败，待执行动作已取消")

    task = asyncio.create_task(worker())
    try:
        await send({"type": "connected"})
        while True:
            try:
                command = await socket.receive_json()
            except ValueError:
                await send({"type": "error", "error": "无法读取控制消息"})
                continue
            if not isinstance(command, dict):
                await send({"type": "error", "error": "控制消息必须是对象"})
                continue
            identifier = command.get("id")
            if type(identifier) is not int or not last_id < identifier <= 2**53 - 1:
                await send({"type": "error", "error": "指令编号必须递增，拒绝重复执行"})
                continue
            last_id = identifier
            kind = command.get("type")
            if kind == "cancel_pending":
                await cancel_pending("待执行点动已取消")
                await send({"type": "result", "id": identifier, "ok": True})
            elif kind not in ("inspect", "jog", "backup", "recover", "disconnect"):
                await send(
                    {
                        "type": "result",
                        "id": identifier,
                        "ok": False,
                        "error": "未知控制指令",
                    }
                )
            elif outstanding >= 4:
                await send(
                    {
                        "type": "result",
                        "id": identifier,
                        "ok": False,
                        "error": "待执行动作已满，请等待",
                    }
                )
            else:
                outstanding += 1
                await send({"type": "queued", "id": identifier, "pending": outstanding})
                commands.put_nowait(command)
    except WebSocketDisconnect:
        pass
    finally:
        # 离开页面即丢弃未执行动作；已发送的有限点动等待串口线程结束再释放。
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await release()
