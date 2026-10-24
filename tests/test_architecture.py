"""约束包依赖方向、导入副作用和应用资源生命周期。"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pygmalion.api import app as application
from pygmalion.servicer.context import ServiceContext

PACKAGE = Path(application.__file__).resolve().parents[1]


def imports(path):
    module = "pygmalion." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
    package = module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package
                )
            else:
                base = node.module or ""
            yield base
            yield from (base + "." + item.name for item in node.names)


def test_package_dependency_boundaries():
    for path in PACKAGE.rglob("*.py"):
        group = path.relative_to(PACKAGE).parts[0]
        dependencies = set(imports(path))
        if group == "schemas":
            assert not any(
                name.startswith("pygmalion.")
                and not name.startswith("pygmalion.schemas")
                for name in dependencies
            ), path
        if group in {
            "servicer",
            "machine",
            "jobs",
            "writing",
            "fonts",
            "schemas",
            "drawing",
        }:
            assert not any(
                name.split(".")[0] in {"fastapi", "starlette", "uvicorn"}
                for name in dependencies
            ), path
            assert not any(
                name.startswith(("pygmalion.api", "pygmalion.commands"))
                for name in dependencies
            ), path
        if group in {"machine", "jobs", "writing", "fonts", "drawing"}:
            assert not any(
                name.startswith("pygmalion.servicer") for name in dependencies
            ), path
        if group == "servicer":
            assert not any(
                name.startswith(
                    ("serial", "pygmalion.machine.grbl", "pygmalion.machine.gcode")
                )
                for name in dependencies
            ), path
            forbidden = {
                "run_program",
                "send",
                "query",
                "wait_idle",
                "emergency_interrupt",
                "GrblLink",
            }
            assert not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden
                for node in ast.walk(ast.parse(path.read_text()))
            ), path
        if group == "api":
            assert not any(
                name.startswith(
                    (
                        "pygmalion.storage",
                        "pygmalion.machine",
                        "pygmalion.jobs",
                        "pygmalion.submissions",
                        "pygmalion.writing",
                        "pygmalion.drawing",
                        "pygmalion.fonts",
                    )
                )
                for name in dependencies
            ), path
        if any(
            name == "uvicorn" or name.startswith("uvicorn.") for name in dependencies
        ):
            assert path == PACKAGE / "commands" / "app.py"
    assert not (PACKAGE / "web").exists()


def test_import_and_factory_do_not_start_runtime(tmp_path):
    # 独立进程避免模块缓存掩盖导入时副作用；禁用真实设备、数据库和线程启动。
    script = """
import sqlite3
import threading
import serial

def forbidden(*args, **kwargs):
    raise AssertionError("import/factory must not start runtime")

sqlite3.connect = forbidden
threading.Thread.start = forbidden
serial.Serial = forbidden
from pygmalion.api.app import create_app
from pygmalion.servicer.context import ServiceContext
from pygmalion.schemas.canvas import CanvasDocument
from pygmalion.schemas.document import DocumentRequest
create_app()
ServiceContext(dry_run=True)
CanvasDocument()
DocumentRequest()
"""
    home = tmp_path / "runtime"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYGMALION_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not home.exists()


def test_missing_frontend_does_not_construct_runtime(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("缺少前端时不能初始化资源")

    monkeypatch.setattr(application, "ServiceContext", forbidden)
    with (
        pytest.raises(RuntimeError, match="pnpm run build"),
        TestClient(application.create_app(tmp_path / "missing")),
    ):
        pass


@pytest.mark.parametrize("fail_start", [False, True])
def test_lifespan_cleans_up_after_start_or_failure(tmp_path, monkeypatch, fail_start):
    (tmp_path / "index.html").write_text("frontend")
    events = []

    class Context:
        def __init__(self, *, dry_run):
            self.printer = type("Printer", (), {"dry_run": dry_run})()
            events.append("create")

        def start(self):
            events.append("start")
            if fail_start:
                raise RuntimeError("partial startup failed")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(application, "ServiceContext", Context)
    if fail_start:
        with (
            pytest.raises(RuntimeError, match="partial startup failed"),
            TestClient(application.create_app(tmp_path, dry_run=True)),
        ):
            pass
    else:
        with TestClient(application.create_app(tmp_path, dry_run=True)) as client:
            assert client.get("/api/health").json() == {"status": "ok"}
    assert events == ["create", "start", "stop"]


def test_context_cleans_controller_when_worker_stop_fails(monkeypatch):
    context = ServiceContext(dry_run=True)
    events = []

    def fail_stop():
        raise RuntimeError("worker cleanup failed")

    monkeypatch.setattr(context.printer, "stop", fail_stop)
    monkeypatch.setattr(
        context.machine, "stop", lambda: events.append("machine stopped")
    )
    with pytest.raises(RuntimeError, match="worker cleanup failed"):
        context.stop()
    assert events == ["machine stopped"]


def test_manual_session_token_cannot_be_bypassed(client):
    from pygmalion.servicer import machine
    from pygmalion.servicer.errors import ServiceError

    context = client.app.state.services
    session = machine.begin_session(context)
    assert session is not None
    try:
        for token in (None, object()):
            with pytest.raises(ServiceError) as error:
                machine.jog(context, {"action": "x", "dist": 1}, session=token)
            assert error.value.kind == "busy"
        assert (
            client.post("/api/admin/jog", json={"action": "x", "dist": 1}).status_code
            == 409
        )
    finally:
        machine.end_session(context, session)
    assert not context.machine.manual_control_lock.locked()


def test_debug_and_cli_use_commands_entry():
    config = json.loads((PACKAGE.parents[1] / ".vscode" / "launch.json").read_text())
    for entry in config["configurations"]:
        assert entry["module"] == "pygmalion.commands.app"
        assert entry["args"] == ["serve"]
        assert entry["envFile"] == "${workspaceFolder}/.env"
    backend = next(
        entry for entry in config["configurations"] if entry["name"] == "backend"
    )
    assert backend["preLaunchTask"] == "web: build"


def test_worker_start_failure_can_be_cleaned_up(isolated_runtime, monkeypatch):
    import threading

    context = ServiceContext(dry_run=True)

    def fail_start(thread):
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread start failed"):
        context.start()
    context.stop()
    assert context.printer._thread is None
