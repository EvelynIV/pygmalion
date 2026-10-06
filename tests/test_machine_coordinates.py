import pytest

from pygmalion import config
from pygmalion.machine.coordinates import PageToMachine
from pygmalion.machine.gcode import strokes_to_gcode
from pygmalion.writing.models import Stroke
from pygmalion.writing.prepare import build_configs


@pytest.mark.parametrize("paper_offset", [(0, 0), (-20, -17), (10, 15)])
def test_default_machine_directions_and_paper_offset(isolated_runtime, paper_offset):
    config.CONFIG["page"].update(
        paper_offset_x_mm=paper_offset[0], paper_offset_y_mm=paper_offset[1]
    )
    cfg = build_configs()
    transform = PageToMachine(cfg["machine"], cfg["page"])
    # 已记录的机器方向：纸面向右是 Y+，向下是 X-。
    start = transform(30, 40)
    right = transform(40, 40)
    down = transform(30, 50)
    assert start == pytest.approx((-(40 + paper_offset[1]), 30 + paper_offset[0]))
    assert (right[0] - start[0], right[1] - start[1]) == pytest.approx((0, 10))
    assert (down[0] - start[0], down[1] - start[1]) == pytest.approx((-10, 0))


def test_gcode_preserves_asymmetric_stroke_direction(isolated_runtime):
    config.CONFIG["page"].update(paper_offset_x_mm=0, paper_offset_y_mm=0)
    cfg = build_configs()
    strokes = [Stroke(points=[(30, 40), (30, 50), (40, 50)])]
    lines = strokes_to_gcode(strokes, cfg["page"], cfg["machine"])
    moves = [line for line in lines if line.startswith(("G0 X", "G1 X"))]
    assert moves == [
        "G0 X-40 Y30",
        "G1 X-50 Y30 F3000",
        "G1 X-50 Y40 F3000",
        "G0 X0 Y0",
    ]
