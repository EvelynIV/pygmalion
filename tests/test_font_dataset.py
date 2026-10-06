from pathlib import Path

from datasets import load_dataset

from pygmalion.fonts import registry as fonts
from pygmalion.fonts.dataset import load_parquet_font
from pygmalion.fonts.sources import CJKSource


def test_parquet_font_discovery_and_stroke_boundaries(tmp_path):
    directory = tmp_path / "data-bin" / "cjk-medians"
    directory.mkdir(parents=True)
    from datasets import Dataset, Features, List, Value

    features = Features(
        {
            "char": Value("string"),
            "lengths": List(Value("uint16")),
            "x": List(Value("int16")),
            "y": List(Value("int16")),
        }
    )
    Dataset.from_list(
        [
            {
                "char": "十",
                "lengths": [2, 2],
                "x": [-4000, 4000, 0, 0],
                "y": [0, 0, -4000, 4000],
            },
        ],
        features=features,
    ).to_parquet(directory / "train.parquet")
    expected = [[[-0.4, 0.0], [0.4, 0.0]], [[0.0, -0.4], [0.0, 0.4]]]
    assert load_parquet_font(directory / "train.parquet")["十"] == expected
    assert len(load_dataset(str(directory), split="train")) == 1
    available = fonts.discover(tmp_path)
    assert available[0].id == "cjk-medians"
    assert available[0].chars == 1 and available[0].points == 4
    paths = fonts.resolve_chain(tmp_path, "cjk-medians.json")
    assert paths == [str(directory / "train.parquet")]
    source = CJKSource(Path(paths[0]))
    assert source.strokes("十") == [[tuple(p) for p in s] for s in expected]
