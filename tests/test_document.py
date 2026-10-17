import pytest

from pygmalion import config
from pygmalion.jobs import repository as jobs
from pygmalion.submissions import repository as submissions
from pygmalion.schemas.document import DocumentRequest
from pygmalion.writing.document import layout_document, prepare_document


def test_document_preserves_blank_lines_indent_and_name_is_not_printed():
    doc = DocumentRequest(
        name="不可打印的文件名", body="  甲\n\n乙\n", font_size_mm=8, line_gap_mm=3
    )
    placed, overflow = layout_document(doc)
    assert not overflow
    assert "".join(g.char for g in placed) == "甲乙"
    assert placed[0].cx == 32  # 20mm 页边距 + 两个半角空格 + 字格半宽
    assert placed[0].cy == 24
    assert placed[1].cy == 46  # 空行保留一整行高度
    a, _ = prepare_document(doc)
    b, _ = prepare_document(doc.model_copy(update={"name": "另一个名称"}))
    assert a.svg == b.svg and a.gcode == b.gcode
    assert a.poem.title == "" and a.poem.author == ""


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_document_wraps_without_shrinking_and_overflow_blocks_write(client, direction):
    values = {"body": "甲" * 1000, "direction": direction, "font_size_mm": 8}
    preview = client.post("/api/workbench/preview", json=values).json()
    assert preview["overflow"] and not preview["can_print"]
    assert preview["font_size_mm"] == 8 and not preview["shrunk"]
    assert client.post("/api/workbench/write", json=values).status_code == 400
    assert submissions.stats()["total"] == 0


def test_horizontal_wrapping_and_vertical_blank_columns():
    d = DocumentRequest(body="甲" * 22)
    placed, overflow = layout_document(d)
    assert not overflow
    assert placed[20].cy == 24 and placed[21].cy == 35
    placed, _ = layout_document(DocumentRequest(body="甲\n\n乙", direction="vertical"))
    assert placed[0].cx == 186 and placed[1].cx == 164
    assert placed[0].cy == placed[1].cy == 24


@pytest.mark.parametrize(
    "payload",
    [
        {"font_size_mm": -1},
        {"margin_mm": 100},
        {"body": []},
        {"paper": "A0"},
        {"title": "自动标题"},
        {"char_gap_mm": None},
    ],
)
def test_invalid_document_never_enqueues(client, payload):
    assert (
        client.post("/api/workbench/write", json={"body": "甲", **payload}).status_code
        == 422
    )
    assert submissions.stats()["total"] == 0


def test_missing_and_empty_body_are_blocked(client):
    for body in (" \n  ", "甲\U0010ffff"):
        preview = client.post("/api/workbench/preview", json={"body": body}).json()
        assert not preview["can_print"]
        assert (
            client.post("/api/workbench/write", json={"body": body}).status_code == 400
        )
    assert submissions.stats()["total"] == 0


def test_single_active_task_freezes_preview_and_machine_configuration(client):
    values = {
        "name": "设备联调 01",
        "body": "  甲\n\n乙",
        "font_size_mm": 8,
        "align": "right",
    }
    expected, _ = prepare_document(DocumentRequest(**values))
    response = client.post("/api/workbench/write", json=values)
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    service = client.app.state.services.printer
    frozen = service._prepared[job_id]
    assert frozen.gcode == expected.gcode and frozen.svg == expected.svg
    assert frozen.poem.body == values["body"]
    config.CONFIG["machine"]["feed_rate"] = 900
    assert frozen.machine_snapshot["feed_rate"] != 900
    assert client.post("/api/workbench/write", json={"body": "丙"}).status_code == 409
    assert submissions.stats()["total"] == 1
    assert jobs.all_job_records()[0]["title"] == values["name"]
    assert client.post("/api/admin/cancel/" + str(job_id), json={}).status_code == 200
    assert client.post("/api/workbench/write", json={"body": "丙"}).status_code == 200


def test_page_and_preview_guide_never_enter_gcode():
    a, _ = prepare_document(DocumentRequest(body="甲"))
    assert "stroke-dasharray" in a.svg
    assert a.strokes > 0 and a.can_print
    assert all("rect" not in line and "操作前检查" not in line for line in a.gcode)


def test_non_dataset_characters_block_writing(client):
    values = {"body": "甲A1!"}
    preview = client.post("/api/workbench/preview", json=values).json()
    assert preview["missing"] == ["A", "1", "!"]
    assert preview["strokes"] > 0 and not preview["can_print"]
    assert client.post("/api/workbench/write", json=values).status_code == 400
    assert submissions.stats()["total"] == 0
