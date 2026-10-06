import threading
import time

import pytest
from fastapi.testclient import TestClient

from pygmalion import config
from pygmalion.submissions import repository as submissions
from pygmalion.jobs import repository as jobs, service as printer
from pygmalion.machine import grbl
from pygmalion.web.app import create_app


def submit(client, **values):
    response = client.post(
        "/api/submit",
        json={
            "author": "陈雨",
            "title": "山居秋暝",
            "body": "空山新雨后，天气晚来秋。",
            **values,
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_submission_preview_filter_delete_without_auth(client):
    sub_id = submit(client)
    data = client.get("/api/admin/submissions").json()
    assert data["stats"]["pending"] == 1
    preview = client.get(
        f"/api/admin/preview/{sub_id}?font_size_mm=12&direction=vertical"
    ).json()
    assert preview["can_print"] and "<svg" in preview["svg"] and preview["cells"] > 0
    assert preview["pending_gfont"] is False
    assert not preview["missing"]
    assert (
        client.get(f"/api/admin/submission/{sub_id}").json()["item"]["title"]
        == "山居秋暝"
    )
    assert client.get("/api/admin/submissions?status=done").json()["items"] == []
    assert client.post(f"/api/admin/delete/{sub_id}", json={}).status_code == 200
    assert client.get("/api/admin/submissions").json()["stats"]["total"] == 0
    assert client.get(f"/api/my/{sub_id}").status_code == 404
    assert client.post("/api/admin/login", json={}).status_code == 404
    assert client.post("/api/admin/logout", json={}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"author": "", "body": "诗"},
        {"author": 8, "body": "诗"},
        {"author": "甲", "body": []},
        {"author": "甲", "body": "诗" * 2001},
    ],
)
def test_invalid_submissions_do_not_persist(client, payload):
    assert client.post("/api/submit", json=payload).status_code == 400
    assert submissions.stats()["total"] == 0


def test_calibration_bounds_and_atomic_settings(client, isolated_runtime):
    before = client.get("/api/admin/grid-calibration").json()
    assert before["fits"]
    assert (
        client.post(
            "/api/admin/grid-calibration", json={"values": {"paper_offset_x_mm": 0}}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/admin/grid-calibration", json={"values": {"paper_offset_x_mm": "NaN"}}
        ).status_code
        == 400
    )
    assert not (isolated_runtime / "config.json").exists()
    response = client.post(
        "/api/admin/grid-calibration", json={"values": {"paper_offset_x_mm": -21}}
    )
    assert response.status_code == 200
    assert config.CONFIG["page"]["paper_offset_x_mm"] == -21
    result = client.post(
        "/api/admin/config",
        json={"values": {"z_on": 500, "feed_rate": 1000, "unknown": 8}},
    ).json()
    assert result["saved"]["z_on"] == 30
    assert result["clamped"] and result["rejected"] == ["unknown"]
    assert (
        client.post("/api/admin/config", json={"values": {"z_on": "NaN"}}).status_code
        == 400
    )
    assert not (isolated_runtime / "config.json.tmp").exists()


def test_queue_freezes_program_machine_settings_and_survives_submission_delete(client):
    sub_id = submit(client)
    service = client.app.state.printer
    job_id = service.submit(sub_id, {"font_size_mm": 10, "handwriting": False})
    frozen = list(service._prepared[job_id].gcode)
    config.CONFIG["machine"]["feed_rate"] = 100
    config.CONFIG["page"]["paper_offset_x_mm"] = -40
    submissions.delete_submission(sub_id)
    service._simulate = lambda progress: setattr(progress, "sent", progress.total)
    service._run_job(job_id)
    job = client.get("/api/admin/jobs").json()["jobs"][0]
    assert job["status"] == "done" and job["title"] == "山居秋暝"
    assert job["overrides"]["font_size_mm"] == 10
    assert (printer.OUTPUT_DIR / f"job{job_id}.gcode").read_text(
        encoding="utf-8"
    ).splitlines() == frozen
    assert job["sent"] == job["total"]


def test_cancel_during_simulation_remains_cancelled(client):
    sub_id = submit(client)
    service = client.app.state.printer
    job_id = service.submit(sub_id)
    worker = threading.Thread(target=service._run_job, args=(job_id,))
    worker.start()
    deadline = time.monotonic() + 2
    while service.get(job_id).status != "printing" and time.monotonic() < deadline:
        time.sleep(0.01)
    service.cancel(job_id)
    worker.join(4)
    assert not worker.is_alive()
    assert service.get(job_id).status == "cancelled"
    assert submissions.get_submission(sub_id)["status"] == "pending"


def test_cancel_queued_job_never_sends(client):
    sub_id = submit(client)
    service = client.app.state.printer
    job_id = service.submit(sub_id)
    assert client.post(f"/api/admin/cancel/{job_id}", json={}).status_code == 200
    service._run_job(job_id)
    assert service.get(job_id).status == "cancelled"
    assert not (printer.OUTPUT_DIR / f"job{job_id}.gcode").exists()


def test_grbl_preview_is_offline_and_rejects_command_injection(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("预览或无效动作不能打开串口")

    monkeypatch.setattr(grbl.GrblLink, "open", forbidden)
    monkeypatch.setattr(grbl, "resolve_port", forbidden)
    result = client.post(
        "/api/admin/settings", json={"changes": {"$110": "4000"}, "confirm": False}
    )
    assert result.status_code == 200 and result.json()["will_send"] == ["$110=4000"]
    for changes in [{"$110\nG1X100": 1}, {"$110": "4000\nG1X100"}, {"$110": "NaN"}]:
        assert (
            client.post("/api/admin/settings", json={"changes": changes}).status_code
            == 400
        )
    assert (
        client.post(
            "/api/admin/settings", json={"changes": {"$110": 1}, "confirm": "false"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/admin/jog", json={"action": "x", "dist": "NaN"}).status_code
        == 400
    )
    assert (
        client.post("/api/admin/jog", json={"action": "x", "dist": 61}).status_code
        == 400
    )


def test_real_job_waits_for_idle_before_done(client, monkeypatch):
    sub_id = submit(client)
    service = client.app.state.printer
    service.dry_run = False
    job_id = service.submit(sub_id)
    observed = []

    class FakeLink:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def recover_if_stuck(self, **kwargs):
            pass

        def run_program(self, program, on_progress, **kwargs):
            on_progress(len(program), len(program))

        def wait_idle(self, **kwargs):
            observed.append(service.get(job_id).status)
            return {"state": "Idle"}

    monkeypatch.setattr(grbl, "GrblLink", FakeLink)
    monkeypatch.setattr(grbl, "ensure_backup_today", lambda *args, **kwargs: None)
    service._run_job(job_id)
    assert observed == ["printing"]
    assert service.get(job_id).status == "done"


def test_static_spa_and_unknown_api_do_not_overlap(client):
    assert (
        client.get("/admin/calibration", headers={"Accept": "text/html"}).status_code
        == 200
    )
    assert (
        client.get("/design/submissions.svg")
        .headers["content-type"]
        .startswith("image/svg+xml")
    )
    for path in ["/api", "/api/unknown"]:
        for accept in ["text/html", "application/json"]:
            assert client.get(path, headers={"Accept": accept}).status_code == 404
        assert client.head(path, headers={"Accept": "text/html"}).status_code == 404
    assert client.get("/openapi.json").json()["info"]["title"].startswith("Pygmalion")


def test_missing_frontend_fails_with_clear_message(isolated_runtime, tmp_path):
    with pytest.raises(RuntimeError, match="pnpm run build"):
        with TestClient(create_app(tmp_path / "missing", dry_run=True)):
            pass


def test_emergency_uses_open_port_and_interrupts_ack_wait(isolated_runtime):
    class FakeSerial:
        is_open = True

        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    link = object.__new__(grbl.GrblLink)
    link._ser = FakeSerial()
    link._emergency = threading.Event()
    service = printer.PrinterService(dry_run=False)
    service._link = link
    assert service.emergency_stop() is True
    assert link._ser.written == [b"!\x18"]
    with pytest.raises(grbl.CancelledError):
        link._read_line()


def test_machine_backup_failure_prevents_parameter_writes(client, monkeypatch):
    written = []

    class FakeLink:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def backup(self, **kwargs):
            raise OSError("备份写入失败")

        def write_settings(self, *args, **kwargs):
            written.append(args)

    monkeypatch.setattr(grbl, "GrblLink", FakeLink)
    response = client.post(
        "/api/admin/settings", json={"changes": {"$110": 1000}, "confirm": True}
    )
    assert response.status_code == 503 and written == []


def test_restart_cancels_unfinished_jobs_without_resuming(client):
    sub_id = submit(client)
    job_id = client.app.state.printer.submit(sub_id)
    jobs.interrupt_unfinished_jobs()
    assert jobs.all_job_records()[0]["status"] == "cancelled"
    assert submissions.get_submission(sub_id)["status"] == "pending"
    assert client.app.state.printer.get(job_id).sent == 0


def test_rewriting_preserves_latest_submission_status(client):
    sub_id = submit(client)
    service = client.app.state.printer
    first = service.submit(sub_id)
    second = service.submit(sub_id)
    service._simulate = lambda progress: setattr(progress, "sent", progress.total)
    service._run_job(first)
    assert submissions.get_submission(sub_id)["status"] == "queued"
    service.cancel(second)
    assert submissions.get_submission(sub_id)["status"] == "pending"


def test_job_history_remains_addressable_after_service_restart(client):
    sub_id = submit(client)
    service = client.app.state.printer
    job_id = service.submit(sub_id)
    service.cancel(job_id)
    client.app.state.printer = printer.PrinterService(dry_run=True)
    response = client.get(f"/api/admin/job/{job_id}")
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "cancelled"
