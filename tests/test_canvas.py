import copy
from itertools import pairwise

import pytest
from pydantic import ValidationError

from pygmalion.schemas.canvas import CanvasDocument, Table, TableCell, TextBox
from pygmalion.writing.canvas import (
    fit_table,
    prepare_canvas,
    table_geometry,
    table_lines,
    text_layout,
)


def table(**overrides):
    values = {
        "id": "table",
        "width_mm": 90,
        "height_mm": 60,
        "column_widths": [30, 30, 30],
        "row_heights": [20, 20, 20],
        "cells": [TableCell(row=r, col=c) for r in range(3) for c in range(3)],
    }
    values.update(overrides)
    return Table(**values)


def test_canvas_text_is_positioned_and_wraps_with_preserved_blank_lines():
    element = TextBox(
        id="text", x_mm=30, y_mm=40, width_mm=16, height_mm=60, text="甲乙丙\n\n甲"
    )
    glyphs, lines, overflow = text_layout(element.text, 30, 40, 16, 60, element)
    assert not overflow
    assert [g.char for g in glyphs] == list("甲乙丙甲")
    assert [line["text"] for line in lines] == ["甲乙", "丙", "", "甲"]
    assert glyphs[0].cx == 34 and glyphs[0].cy == 44
    assert glyphs[-1].cy == 77
    doc = CanvasDocument(elements=[element])
    prepared, overflow = prepare_canvas(doc)
    assert not overflow and prepared.can_print
    assert prepared.layout[0]["regions"][0]["lines"] == lines
    assert (
        prepared.gcode
        == prepare_canvas(doc.model_copy(update={"name": "另一个名字"}))[0].gcode
    )


def test_table_grid_deduplicates_shared_edges_and_removes_merged_interior():
    grid = table()
    assert len(table_lines(grid, 297)) == 8  # 四条水平线、四条竖线，各只画一次
    merged = table(cells=[TableCell(row=0, col=0, rowspan=3, colspan=3)])
    assert len(table_lines(merged, 297)) == 4
    assert all(len(s.points) == 2 and s.role == "table" for s in table_lines(merged))
    doc = CanvasDocument(elements=[grid])
    p, overflow = prepare_canvas(doc)
    assert not overflow and p.can_print and p.strokes == 120  # 只有表格线也能执行
    hidden = prepare_canvas(
        CanvasDocument(elements=[grid.model_copy(update={"draw_borders": False})])
    )[0]
    assert hidden.strokes == 0 and not hidden.can_print
    assert not p.missing


@pytest.mark.parametrize(
    "changes",
    [
        {"column_widths": [20, 20, 20]},
        {"row_heights": [float("nan"), 20, 20]},
        {"cells": [TableCell(row=0, col=0, rowspan=4)]},
        {"cells": [TableCell(row=0, col=0), TableCell(row=0, col=0)]},
        {"cells": []},
    ],
)
def test_invalid_table_rejected(changes):
    with pytest.raises(ValidationError):
        table(**changes)


def test_fit_keeps_outer_geometry_and_is_deterministic_with_merged_cells():
    grid = table(
        cells=[
            TableCell(row=0, col=0, colspan=3, text="甲乙丙甲乙丙"),
            *[
                TableCell(row=r, col=c, text="甲" * (c + 1))
                for r in (1, 2)
                for c in range(3)
            ],
        ]
    )
    before = grid.model_dump()
    fitted, errors = fit_table(grid)
    assert not errors and fit_table(grid)[0] == fitted
    assert grid.model_dump() == before
    assert sum(fitted.column_widths) == pytest.approx(grid.width_mm)
    assert sum(fitted.row_heights) == pytest.approx(grid.height_mm)
    for key in ("x_mm", "y_mm", "width_mm", "height_mm", "cells"):
        assert getattr(fitted, key) == getattr(grid, key)
    assert 4 <= fitted.font_size_mm <= 40
    for c, x, y, w, h in table_geometry(fitted):
        assert not text_layout(
            c.text, x + 1.5, y + 1.5, w - 3, h - 3, fitted, c.align, c.valign
        )[2]


def test_fit_redistributes_columns_and_respects_row_spans():
    grid = table(
        cells=[
            TableCell(row=0, col=0, rowspan=3, text="甲" * 50),
            *[TableCell(row=r, col=c, text="乙") for r in range(3) for c in (1, 2)],
        ]
    )
    fitted, errors = fit_table(grid)
    assert not errors
    assert fitted.column_widths[0] > fitted.column_widths[1]
    assert sum(fitted.row_heights) == pytest.approx(60)
    assert prepare_canvas(CanvasDocument(elements=[fitted]))[0].can_print


def test_impossible_fit_preserves_original_and_empty_table_unchanged():
    grid = table()
    assert fit_table(grid) == (grid, [])
    grid.cells[0].text = "甲" * 2000
    fitted, errors = fit_table(grid)
    assert fitted == grid and errors
    assert any(e.get("row") == 0 and e.get("col") == 0 for e in errors)


@pytest.mark.parametrize("size", [4, 4.05, 8])
def test_fit_does_not_enlarge_or_rearrange_content_that_already_fits(size):
    grid = table(width_mm=120, column_widths=[40] * 3, font_size_mm=size)
    for cell, text in zip(grid.cells, ["甲乙", "甲乙", "甲乙丙丁"]):
        cell.text = text
    assert fit_table(grid) == (grid, [])


def test_fit_only_shrinks_when_track_redistribution_is_insufficient():
    grid = table(font_size_mm=16)
    for cell in grid.cells:
        cell.text = "甲" * 12
    fitted, errors = fit_table(grid)
    assert not errors and 4 <= fitted.font_size_mm < grid.font_size_mm
    for cell, x, y, w, h in table_geometry(fitted):
        assert not text_layout(cell.text, x + 1.5, y + 1.5, w - 3, h - 3, fitted)[2]


def test_preview_diagnostics_and_write_revalidate_bounds_missing_and_overflow(client):
    for element, code in [
        (TextBox(id="t", x_mm=0, text="甲"), "bounds"),
        (TextBox(id="t", text="甲" * 100, width_mm=10, height_mm=10), "overflow"),
        (TextBox(id="t", text="A"), "missing"),
    ]:
        doc = CanvasDocument(elements=[element]).model_dump()
        preview = client.post("/api/workbench/preview", json=doc).json()
        assert not preview["can_print"]
        assert any(d["code"] == code for d in preview["diagnostics"])
        assert client.post("/api/workbench/write", json=doc).status_code == 400


def test_fit_endpoint_freezes_geometry_and_new_document_job(client):
    grid = table()
    grid.cells[0].text = "甲乙"
    doc = CanvasDocument(elements=[grid]).model_dump()
    response = client.post(
        "/api/workbench/table-fit", json={"document": doc, "element_id": "table"}
    )
    data = response.json()
    assert response.status_code == 200 and data["success"]
    assert data["table"]["width_mm"] == 90 and data["table"]["height_mm"] == 60
    assert (
        client.post(
            "/api/workbench/table-fit", json={"document": doc, "element_id": "unknown"}
        ).status_code
        == 400
    )
    preview = client.post("/api/workbench/preview", json=doc).json()
    assert preview["can_print"]
    result = client.post("/api/workbench/write", json=doc)
    assert result.status_code == 200
    job_id = result.json()["job_id"]
    service = client.app.state.services.printer
    frozen = service._prepared[job_id]
    assert frozen.svg == preview["svg"]
    assert frozen.gcode == prepare_canvas(CanvasDocument.model_validate(doc))[0].gcode
    saved = copy.deepcopy(service.get(job_id).overrides)
    doc["elements"][0]["cells"][0]["text"] = "丙"
    assert service.get(job_id).overrides == saved


def test_canvas_schema_rejects_duplicate_ids_vertical_and_nonfinite_coordinates(client):
    valid = CanvasDocument(elements=[TextBox(id="text", text="甲")]).model_dump()
    for document in [
        {**valid, "elements": valid["elements"] * 2},
        {**valid, "elements": [{**valid["elements"][0], "direction": "vertical"}]},
    ]:
        assert client.post("/api/workbench/preview", json=document).status_code == 422
    with pytest.raises(ValidationError):
        TextBox(id="bad", x_mm=float("inf"))
    long = CanvasDocument(
        elements=[TextBox(id="a", text="甲" * 20000), TextBox(id="b")]
    )
    assert len(long.elements[0].text) == 20000  # 元素间的存储换行不占文字额度


def test_fit_unknown_font_returns_actionable_error(client):
    grid = table(font="not-installed")
    grid.cells[0].text = "甲"
    response = client.post(
        "/api/workbench/table-fit",
        json={
            "document": CanvasDocument(elements=[grid]).model_dump(),
            "element_id": grid.id,
        },
    )
    assert response.status_code == 400
    assert "字库不可用" in response.json()["error"]


@pytest.mark.parametrize("limit", [5, 2, 7.5])
def test_table_segments_cover_lines_without_gaps_or_duplicate_edges(limit):
    import math

    strokes = table_lines(table(), limit)
    lengths = [math.dist(s.points[0], s.points[1]) for s in strokes]
    assert all(0 < length <= limit + 1e-9 for length in lengths)
    assert sum(lengths) == pytest.approx(4 * 90 + 4 * 60)
    assert len({tuple(s.points) for s in strokes}) == len(strokes)
    edges = table_lines(table(), 297)
    for edge in edges:
        start, end = edge.points
        segments = (
            [
                s
                for s in strokes
                if (s.points[0][1] == s.points[1][1] == start[1] == end[1])
            ]
            if start[1] == end[1]
            else [
                s
                for s in strokes
                if s.points[0][0] == s.points[1][0] == start[0] == end[0]
            ]
        )
        assert segments[0].points[0] == start and segments[-1].points[-1] == end
        assert all(a.points[-1] == b.points[0] for a, b in pairwise(segments))


def test_table_segments_lift_pen_and_use_software_configuration(client):
    from pygmalion import config

    doc = CanvasDocument(elements=[table()])
    original = prepare_canvas(doc)[0]
    config.CONFIG["machine"]["table_segment_mm"] = 2
    fine = prepare_canvas(doc)[0]
    assert fine.strokes > original.strokes
    assert fine.machine_snapshot["table_segment_mm"] == 2
    assert len([line for line in fine.gcode if line.startswith("G1 Z")]) == fine.strokes
    moves = [i for i, line in enumerate(fine.gcode) if line.startswith("G1 X")]
    assert all(fine.gcode[i + 1].startswith("G0 Z") for i in moves)
    values = client.get("/api/admin/config").json()
    assert (
        next(f for f in values["fields"] if f["key"] == "table_segment_mm")["value"]
        == 2
    )
    response = client.post(
        "/api/admin/config", json={"values": {"table_segment_mm": 3}}
    )
    assert (
        response.status_code == 200
        and config.CONFIG["machine"]["table_segment_mm"] == 3
    )


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
def test_invalid_table_segment_limit_rejected(limit):
    with pytest.raises(ValueError):
        table_lines(table(), limit)
