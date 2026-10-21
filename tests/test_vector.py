"""SVG 导入、连续笔迹与混合纸面的回归验证。"""

import math
from itertools import pairwise

import pytest
from pydantic import ValidationError
from svgelements import Matrix, Path

from pygmalion.drawing.svg import import_svg, vector_strokes
from pygmalion.schemas.canvas import CanvasDocument, TextBox, Vector
from pygmalion.writing.canvas import prepare_canvas


def svg(body, attrs='width="100mm" height="50mm" viewBox="0 0 100 50"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>'


def vector(body, **kwargs):
    data = import_svg(svg(body))
    data.update(kwargs)
    return Vector(id="vector", **data)


def test_units_transforms_and_shapes():
    v = vector(
        '<g transform="translate(20,10) scale(2)"><rect width="20" height="10"/></g>'
    )
    assert v.width_mm == pytest.approx(40, rel=1e-5)
    assert v.height_mm == pytest.approx(20, rel=1e-5)
    points = vector_strokes(v)[0].points
    assert points[0] == points[-1]
    assert min(x for x, y in points) == pytest.approx(20)
    assert max(x for x, y in points) == pytest.approx(60, rel=1e-5)
    for shape in [
        '<circle cx="10" cy="10" r="5"/>',
        '<ellipse cx="10" cy="10" rx="5" ry="3"/>',
        '<line x2="30" stroke="black"/>',
        '<polyline points="0,0 10,10 20,0"/>',
        '<polygon points="0,0 10,10 20,0"/>',
        '<rect width="20" height="10" rx="2"/>',
    ]:
        assert vector_strokes(vector(shape))


def test_continuous_subpaths_and_font_independence(monkeypatch):
    from pygmalion.writing import canvas

    monkeypatch.setattr(
        canvas, "_glyph_source", lambda *_: pytest.fail("矢量图不得加载字库")
    )
    v = vector(
        '<path fill="none" stroke="black" d="M0 0 L40 0 L40 20 Z M50 0 L70 20"/>'
    )
    strokes = vector_strokes(v)
    assert len(strokes) == 2
    assert len(strokes[0].points) == 4
    assert strokes[0].points[0] == strokes[0].points[-1]
    assert math.dist(*strokes[1].points) > 5
    prepared, overflow = prepare_canvas(CanvasDocument(elements=[v]))
    assert not overflow and prepared.can_print and not prepared.missing
    assert prepared.strokes == 2
    assert any("共 2 条笔画" in line for line in prepared.gcode)
    assert sum(line.startswith("G1 Z") for line in prepared.gcode) == 2
    assert prepared.cells == 0


def distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = (
        max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        if dx or dy
        else 0
    )
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


@pytest.mark.parametrize(
    "data", ["M0 0 C0 1 1 0 1 1", "M0 0 Q0 1 1 1", "M0 0.5 A0.5 0.5 0 1 1 1 0.5"]
)
def test_curve_error_at_final_size(data):
    v = Vector(id="v", paths=[data], width_mm=150, height_mm=200)
    points = vector_strokes(v)[0].points
    curve = Path(data)[1]
    for i in range(501):
        p = curve.point(i / 500)
        placed = (20 + p.x * 150, 20 + p.y * 200)
        assert min(distance(placed, a, b) for a, b in pairwise(points)) <= 0.050001
    small = vector_strokes(v.model_copy(update={"width_mm": 15, "height_mm": 20}))[
        0
    ].points
    assert len(points) > len(small)


@pytest.mark.parametrize(
    "body",
    [
        "<text>文字</text>",
        '<image href="file:///etc/passwd"/>',
        "<script/>",
        '<path d="M0 0 L10 10" onclick="x()"/>',
        '<rect width="20" height="10" clip-path="url(#clip)"/>',
        "<style>path {display:none}</style>",
        '<path d="M0 0 L10 10" stroke-dasharray="2 2"/>',
        '<path d="M0 0 Lbad"/>',
        '<path d="M0 0 L1e999 2"/>',
        '<use href="#a"/>',
    ],
)
def test_unsupported_svg_rejected(body):
    with pytest.raises(ValueError):
        import_svg(svg(body))


def test_empty_dtd_and_bounds():
    for source in [
        svg(""),
        '<!DOCTYPE svg [<!ENTITY x "abc">]><svg/>',
        svg('<rect width="3" height="3" display="none"/>'),
    ]:
        with pytest.raises(ValueError):
            import_svg(source)
    with pytest.raises(ValidationError):
        Vector(id="v", paths=["M0 0 L2 1"])
    v = vector('<rect width="20" height="10"/>', x_mm=195)
    prepared, overflow = prepare_canvas(CanvasDocument(elements=[v]))
    assert overflow and not prepared.can_print


def test_roundtrip_mixed_document():
    v = vector('<path fill="none" stroke="black" d="M0 0 L20 10"/>')
    doc = CanvasDocument(elements=[v, TextBox(id="t", text="甲", y_mm=60)])
    restored = CanvasDocument.model_validate_json(doc.model_dump_json())
    assert restored == doc and restored.body.strip() == "甲"
    result, _ = prepare_canvas(restored)
    assert result.can_print and result.cells == 1 and result.strokes >= 2


def test_import_preview_and_dry_run_write(client):
    result = client.post(
        "/api/workbench/import-svg",
        json={"source": svg('<rect width="40" height="20"/>'), "name": "样图"},
    )
    assert result.status_code == 200, result.text
    v = Vector(id="v", **result.json())
    payload = CanvasDocument(elements=[v]).model_dump()
    preview = client.post("/api/workbench/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_print"]
    written = client.post("/api/workbench/write", json=payload)
    assert written.status_code == 200 and written.json()["dry_run"]
    bad = client.post(
        "/api/workbench/import-svg", json={"source": svg("<text>no</text>")}
    )
    assert bad.status_code == 400


def test_invisible_paints_and_implicit_fill_closure():
    for body in [
        '<g opacity="0"><rect opacity="1" width="20" height="10"/></g>',
        '<rect width="20" height="10" fill-opacity="0"/>',
    ]:
        with pytest.raises(ValueError):
            import_svg(svg(body))
    v = vector('<path d="M0 0 L20 0 L20 20"/>')
    points = vector_strokes(v)[0].points
    assert len(points) == 4 and points[0] == points[-1]


@pytest.mark.parametrize(
    "transform",
    [
        "skewX(35)",
        "rotate(35) scale(2,1)",
        "matrix(2,1,0.4,1,0,0)",
        "scale(-2,1) rotate(20)",
    ],
)
def test_transformed_ellipse_keeps_geometry_after_normalization_and_resize(transform):
    source = f'<svg><circle cx="10" cy="10" r="5" transform="{transform}"/></svg>'
    data = import_svg(source)
    v = Vector(id="v", **{**data, "width_mm": 120, "height_mm": 200})
    points = vector_strokes(v)[0].points
    matrix = Matrix(transform)
    cx = matrix.a * 10 + matrix.c * 10 + matrix.e
    cy = matrix.b * 10 + matrix.d * 10 + matrix.f
    ex, ey = 5 * math.hypot(matrix.a, matrix.c), 5 * math.hypot(matrix.b, matrix.d)
    for i in range(501):
        theta = i * math.tau / 500
        x = 10 + 5 * math.cos(theta)
        y = 10 + 5 * math.sin(theta)
        px, py = (
            matrix.a * x + matrix.c * y + matrix.e,
            matrix.b * x + matrix.d * y + matrix.f,
        )
        expected = (
            20 + (px - cx + ex) / (2 * ex) * 120,
            20 + (py - cy + ey) / (2 * ey) * 200,
        )
        assert min(distance(expected, a, b) for a, b in pairwise(points)) <= 0.05001
