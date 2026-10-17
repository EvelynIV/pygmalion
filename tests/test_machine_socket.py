import threading
from datetime import datetime

import pytest

from pygmalion import config
from pygmalion.machine import grbl


@pytest.fixture
def fake_machine(monkeypatch, isolated_runtime):
    links = []
    programs = []
    backups = []
    started = threading.Event()
    release = threading.Event()
    release.set()
    config.CONFIG["machine"]["port"] = "COM17"

    class Link:
        def __init__(self, port, baud, **kwargs):
            self.port, self.baud = port, baud
            self.is_open = False
            self.backed_up = False
            links.append(self)

        def __enter__(self):
            self.is_open = True
            return self

        def __exit__(self, *args):
            self.is_open = False

        def query(self, command, **kwargs):
            return ["[VER:1.1:test]"]

        def status(self):
            return {"state": "Idle", "MPos": f"{len(programs)},0,0"}

        def mark_backed_up(self):
            self.backed_up = True

        def backup(self, reason):
            backups.append(reason)
            path = grbl.BACKUP_DIR / "grbl-settings-latest.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")
            self.mark_backed_up()
            return path

        def run_program(self, program):
            assert self.backed_up
            programs.append(program)
            started.set()
            assert release.wait(5), "test command not released"

        def wait_idle(self, **kwargs):
            return self.status()

        def send(self, command):
            assert self.backed_up
            programs.append([command])

    monkeypatch.setattr(grbl, "GrblLink", Link)
    return links, programs, backups, started, release


def result(socket, identifier):
    while True:
        message = socket.receive_json()
        if message.get("type") == "result" and message.get("id") == identifier:
            return message


def test_socket_orders_rapid_directions_reuses_serial_and_backup(client, fake_machine):
    links, programs, backups, _, _ = fake_machine
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        assert socket.receive_json()["type"] == "connected"
        socket.send_json({"id": 1, "type": "inspect"})
        assert result(socket, 1)["port"] == "COM17"
        for identifier, axis, dist in [(2, "x", 1), (3, "y", -1), (4, "x", -1)]:
            socket.send_json(
                {
                    "id": identifier,
                    "type": "jog",
                    "payload": {"action": axis, "dist": dist},
                }
            )
        for identifier in (2, 3, 4):
            assert result(socket, identifier)["ok"]
        assert [program[1] for program in programs] == [
            "G1 X1 F3000",
            "G1 Y-1 F3000",
            "G1 X-1 F3000",
        ]
        assert len(links) == len(backups) == 1
        assert links[0].is_open
    assert not links[0].is_open
    assert not client.app.state.services.machine.manual_control_lock.locked()


def test_socket_limits_pending_moves(client, fake_machine):
    _, programs, _, started, release = fake_machine
    release.clear()
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json(
            {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        )
        assert started.wait(2)
        for identifier in (2, 3, 4, 5):
            socket.send_json(
                {"id": identifier, "type": "jog", "payload": {"action": "y", "dist": 1}}
            )
        assert "已满" in result(socket, 5)["error"]
        release.set()
        for identifier in (1, 2, 3, 4):
            assert result(socket, identifier)["ok"]
    assert len(programs) == 4


def test_socket_cancels_unsent_moves(client, fake_machine):
    _, programs, _, started, release = fake_machine
    release.clear()
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json(
            {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        )
        assert started.wait(2)
        socket.send_json(
            {"id": 2, "type": "jog", "payload": {"action": "y", "dist": 1}}
        )
        socket.send_json({"id": 3, "type": "cancel_pending"})
        assert result(socket, 2)["cancelled"]
        assert result(socket, 3)["ok"]
        release.set()
        assert result(socket, 1)["ok"]
    assert len(programs) == 1


def test_socket_disconnect_releases_port_before_maintenance(client, fake_machine):
    links, _, _, _, _ = fake_machine
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json({"id": 1, "type": "inspect"})
        assert result(socket, 1)["ok"]
        assert (
            client.post("/api/admin/serial-port", json={"port": "COM18"}).status_code
            == 409
        )
        socket.send_json({"id": 2, "type": "disconnect"})
        assert result(socket, 2)["ok"]
        assert not links[0].is_open
        assert (
            client.post("/api/admin/serial-port", json={"port": "COM18"}).status_code
            == 200
        )


def test_socket_rejects_second_controller(client, fake_machine):
    with client.websocket_connect("/api/admin/machine/ws") as first:
        assert first.receive_json()["type"] == "connected"
        with client.websocket_connect("/api/admin/machine/ws") as second:
            assert "另一个页面" in second.receive_json()["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "x", "dist": "NaN"},
        {"action": "x", "dist": 100},
        [],
        {"action": "G1X100"},
    ],
)
def test_socket_validates_before_opening_machine(client, fake_machine, payload):
    links, programs, _, _, _ = fake_machine
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json({"id": 1, "type": "jog", "payload": payload})
        assert not result(socket, 1)["ok"]
    assert links == programs == []


def test_socket_refuses_motion_while_printing(client, fake_machine):
    links, programs, _, _, _ = fake_machine
    service = client.app.state.services.printer
    service._current = object()
    try:
        with client.websocket_connect("/api/admin/machine/ws") as socket:
            socket.receive_json()
            socket.send_json(
                {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
            )
            assert not result(socket, 1)["ok"]
        assert links == programs == []
    finally:
        service._current = None


def test_serial_failure_closes_link_and_drops_pending(
    client, fake_machine, monkeypatch
):
    links, programs, _, started, release = fake_machine
    release.clear()
    constructor = grbl.GrblLink

    def failing(*args, **kwargs):
        link = constructor(*args, **kwargs)

        def fail(program):
            started.set()
            assert release.wait(5)
            raise grbl.GrblError("设备已断线")

        link.run_program = fail
        return link

    monkeypatch.setattr(grbl, "GrblLink", failing)
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json(
            {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        )
        assert started.wait(2)
        socket.send_json(
            {"id": 2, "type": "jog", "payload": {"action": "y", "dist": 1}}
        )
        release.set()
        assert "断线" in result(socket, 1)["error"]
        assert result(socket, 2)["cancelled"]
        assert not links[0].is_open
    assert programs == []


def test_print_connection_releases_manual_port_before_opening(client, fake_machine):
    links, _, _, _, _ = fake_machine
    service = client.app.state.services.printer
    with service.controller.device_connection(config.CONFIG["machine"], reuse=True):
        pass
    with service.controller.device_connection(config.CONFIG["machine"]):
        assert not links[0].is_open and links[1].is_open
    assert not links[1].is_open


def test_socket_pushes_status_without_another_read_command(client, fake_machine):
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json({"id": 1, "type": "inspect"})
        assert result(socket, 1)["ok"]
        message = socket.receive_json()
        assert message["type"] == "state" and message["state"]["state"] == "Idle"


def test_socket_rejects_replayed_motion_id(client, fake_machine):
    _, programs, _, _, _ = fake_machine
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        command = {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        socket.send_json(command)
        assert result(socket, 1)["ok"]
        socket.send_json(command)
        while True:
            message = socket.receive_json()
            if message["type"] == "error":
                assert "重复执行" in message["error"]
                break
    assert len(programs) == 1


def test_socket_disconnect_drops_queued_motion(client, fake_machine):
    links, programs, _, started, release = fake_machine
    release.clear()
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json(
            {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        )
        assert started.wait(2)
        socket.send_json(
            {"id": 2, "type": "jog", "payload": {"action": "y", "dist": 1}}
        )
        while True:
            message = socket.receive_json()
            if message["type"] == "queued" and message["id"] == 2:
                break
        timer = threading.Timer(0.1, release.set)
        timer.start()
    timer.join()
    assert len(programs) == 1 and not links[0].is_open


def test_socket_backup_failure_prevents_motion(client, fake_machine, monkeypatch):
    links, programs, _, _, _ = fake_machine

    def fail_backup(*args, **kwargs):
        raise OSError("备份写入失败")

    monkeypatch.setattr(grbl, "ensure_backup_today", fail_backup)
    with client.websocket_connect("/api/admin/machine/ws") as socket:
        socket.receive_json()
        socket.send_json(
            {"id": 1, "type": "jog", "payload": {"action": "x", "dist": 1}}
        )
        assert "备份写入失败" in result(socket, 1)["error"]
        assert not links[0].is_open
    assert programs == []
