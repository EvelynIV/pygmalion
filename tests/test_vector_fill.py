"""填充区域、真实尺度、空走与旧文档兼容。"""

import math

import pytest
from pydantic import ValidationError

from pygmalion.drawing.fill import hatch_strokes
from pygmalion.drawing.svg import import_svg, vector_strokes
from pygmalion.schemas.canvas import Vector
from pygmalion.writing.models import PageConfig, Stroke
from pygmalion.writing.preview import strokes_to_svg


def contour(points):
    return Stroke(points)


OUTER = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
INNER = [(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]


@pytest.mark.parametrize(
    "rule,reverse,hole",
    [("evenodd", False, True), ("nonzero", True, True), ("nonzero", False, False)],
)
def test_holes_and_winding(rule, reverse, hole):
    strokes = hatch_strokes(
        [contour(OUTER), contour(INNER[::-1] if reverse else INNER)], rule, 1, 0
    )
    middle = [s for s in strokes if 3 < s.points[0][1] < 7]
    assert len(middle) == (8 if hole else 4)
    for s in middle:
        xs = sorted(p[0] for p in s.points)
        if hole:
            assert xs[1] <= 3 or xs[0] >= 7
        else:
            assert xs == [0, 10]


def test_fill_rectangle_spacing_and_alternation():
    strokes = hatch_strokes([contour(OUTER)], "nonzero", 0.24, 0)
    assert len(strokes) == 42
    assert strokes[0].points[0][0] == 0
    assert strokes[1].points[0][0] == 10
    assert strokes[1].points[0][1] - strokes[0].points[0][1] <= 0.24


@pytest.mark.parametrize("angle", [45, 90, -30])
def test_rotated_fill_stays_inside(angle):
    for stroke in hatch_strokes([contour(OUTER)], "nonzero", 0.3, angle):
        for x, y in stroke.points:
            assert -1e-8 <= x <= 10 + 1e-8
            assert -1e-8 <= y <= 10 + 1e-8


def test_import_inherited_rule_and_open_fill():
    data = import_svg(
        '<svg><g fill-rule="evenodd"><path d="M0 0L10 0L10 10L0 10 M3 3L7 3L7 7L3 7"/></g><path fill="none" stroke="black" d="M0 0L10 10"/></svg>'
    )
    assert data["fill_rules"] == ["evenodd", None]
    assert data["draw_mode"] == "solid"
    v = Vector(id="v", **data)
    assert len(vector_strokes(v)) > 3


def test_old_document_and_invalid_metadata():
    old = Vector(id="v", paths=["M0 0L1 0L1 1Z"])
    assert old.draw_mode == "outline"
    assert len(vector_strokes(old)) == 1
    for changes in [
        {"draw_mode": "solid"},
        {"fill_rules": ["nonzero", None]},
        {"fill_spacing_mm": 0},
        {"fill_angle_deg": math.nan},
    ]:
        with pytest.raises(ValidationError):
            Vector(**{**old.model_dump(), **changes})


def test_resize_recomputes_density_and_preview_width():
    data = import_svg('<svg><rect width="10" height="10"/></svg>')
    v = Vector(id="v", **{**data, "width_mm": 10, "height_mm": 10})
    first = vector_strokes(v)
    second = vector_strokes(v.model_copy(update={"height_mm": 20}))
    assert len(second) > len(first) * 1.9
    assert all(s.width_mm == 0.3 for s in first)
    assert 'stroke-width="0.3"' in strokes_to_svg(first, PageConfig())


def test_complexity_fails_instead_of_truncating():
    with pytest.raises(ValueError, match="复杂|超过"):
        hatch_strokes(
            [contour([(0, 0), (1, 0), (1, 10000), (0, 10000)])], "nonzero", 0.05, 0
        )


def test_fill_preview_and_gcode_use_same_strokes():
    from pygmalion.schemas.canvas import CanvasDocument
    from pygmalion.writing.canvas import prepare_canvas

    v = Vector(
        id="ring",
        **import_svg(
            '<svg><path fill-rule="evenodd" d="M0 0H40V40H0Z M10 10H30V30H10Z"/></svg>'
        ),
    )
    strokes = vector_strokes(v)
    prepared, overflow = prepare_canvas(CanvasDocument(elements=[v]))
    assert prepared.can_print and not overflow
    assert prepared.strokes == len(strokes)
    assert prepared.svg.count("<path ") == len(strokes)
    assert sum(line.startswith("G1 Z") for line in prepared.gcode) == len(strokes)
