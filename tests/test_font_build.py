import pytest
from datasets import Dataset, load_dataset

from pygmalion.fonts import registry
from pygmalion.fonts.build import TARGET_CHARS, build_font
from pygmalion.fonts.sources import CJKSource
from pygmalion.schemas.canvas import CanvasDocument, TextBox
from pygmalion.writing import canvas, prepare


def test_extended_dataset_preserves_base_and_writes_mixed_text(tmp_path, monkeypatch):
    base = tmp_path / "base" / "train.parquet"
    base.parent.mkdir()
    original = {"char": "甲", "lengths": [2], "x": [-4000, 4000], "y": [-2000, 2000]}
    Dataset.from_list([original]).to_parquet(base)
    output = tmp_path / "model-bin" / "cjk-medians-plus"
    report = build_font(base, output)
    assert report["missing"] == [] and report["added_chars"] == len(TARGET_CHARS)
    dataset = load_dataset(str(output), split="train")
    assert {key: dataset[0][key] for key in original} == original
    source = CJKSource(output / "train.parquet")
    assert all(source.supports(ch) for ch in TARGET_CHARS + "甲")
    assert source.strokes("甲") == CJKSource(base).strokes("甲")
    for ch in TARGET_CHARS:
        assert all(len(poly) >= 2 for poly in source.strokes(ch))
        assert all(
            abs(x) <= 0.5 and abs(y) <= 0.5
            for poly in source.strokes(ch)
            for x, y in poly
        )

    def ys(ch):
        return [y for poly in source.strokes(ch) for x, y in poly]

    assert min(ys("a")) > min(ys("A"))
    assert min(ys(".")) > 0.2
    assert max(ys("g")) > max(ys("a"))
    assert source.strokes("0")[0][0] == source.strokes("0")[0][-1]
    question = source.strokes("?")
    assert all(source.strokes(ch) != question for ch in TARGET_CHARS[:94] if ch != "?")
    assert (output / "glyphs-4mm.png").is_file() and (
        output / "glyphs-8mm.png"
    ).is_file()
    assert (output / "ARPHICPL.TXT").read_text().startswith("ARPHIC PUBLIC LICENSE")
    monkeypatch.setattr(canvas, "ROOT", tmp_path)
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    prepared, overflow = canvas.prepare_canvas(
        CanvasDocument(
            elements=[
                TextBox(
                    id="mixed",
                    text="甲Abc0123，。±℃￥",
                    font="cjk-medians-plus",
                    font_size_mm=4,
                )
            ]
        )
    )
    assert not overflow and prepared.can_print and not prepared.missing
    assert prepared.strokes and prepared.gcode
    with pytest.raises(ValueError, match="不能覆盖"):
        build_font(base, output)
    assert {
        key: load_dataset(str(output), split="train")[0][key] for key in original
    } == original


def test_failed_base_never_publishes_a_partial_font(tmp_path):
    base = tmp_path / "bad.parquet"
    Dataset.from_list(
        [{"char": "甲", "lengths": [3], "x": [1, 2], "y": [1, 2]}]
    ).to_parquet(base)
    with pytest.raises(ValueError, match="笔画边界"):
        build_font(base, tmp_path / "font")
    assert not (tmp_path / "font").exists()


def test_font_under_construction_is_not_discovered(tmp_path):
    pending = tmp_path / "model-bin" / "custom.building"
    pending.mkdir(parents=True)
    Dataset.from_list(
        [{"char": "甲", "lengths": [2], "x": [0, 1000], "y": [0, 1000]}]
    ).to_parquet(pending / "train.parquet")
    assert registry.discover(tmp_path) == []
    pending.rename(pending.with_name("custom"))
    assert [font.id for font in registry.discover(tmp_path)] == ["custom"]
