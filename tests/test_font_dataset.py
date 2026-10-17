import pytest
from pathlib import Path

from datasets import load_dataset

from pygmalion.fonts import registry as fonts
from pygmalion.fonts.dataset import load_parquet_font
from pygmalion.fonts.sources import CJKSource


def test_parquet_font_discovery_and_stroke_boundaries(tmp_path):
    directory = tmp_path / "model-bin" / "cjk-medians"
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
    paths = fonts.resolve_chain(tmp_path, "cjk-medians")
    assert paths == [str(directory / "train.parquet")]
    source = CJKSource(Path(paths[0]))
    assert source.strokes("十") == [[tuple(p) for p in s] for s in expected]


def test_only_datasets_are_discovered_and_no_generated_fallback(tmp_path):
    import json
    import pytest
    from pygmalion.fonts.sources import source_from_dir

    legacy = tmp_path / "data" / "fonts"
    legacy.mkdir(parents=True)
    path = legacy / "cjk-old.json"
    path.write_text(json.dumps({"甲": [[[0, 0], [1, 1]]]}))
    (legacy / "private.gfont").write_bytes(b"legacy")
    assert fonts.discover(tmp_path) == []
    source = source_from_dir(tmp_path)
    assert source.missing("甲A1，。") == list("甲A1，。")
    with pytest.raises(ValueError, match="Parquet"):
        CJKSource(path)


def test_dataset_selection_and_fallback_include_punctuation(tmp_path, monkeypatch):
    from datasets import Dataset
    from pygmalion import config
    from pygmalion.fonts.sources import source_from_dir

    for name, char in [("custom", "A"), ("symbols", "，")]:
        directory = tmp_path / "model-bin" / name
        directory.mkdir(parents=True)
        Dataset.from_list(
            [{"char": char, "lengths": [2], "x": [-3000, 3000], "y": [0, 0]}]
        ).to_parquet(directory / "train.parquet")
    monkeypatch.setitem(config.CONFIG, "fonts", {"default": "symbols"})
    assert fonts.default_id(tmp_path) == "symbols"
    paths = fonts.resolve_chain(tmp_path, "custom")
    assert Path(paths[0]).parent.name == "custom"
    source = source_from_dir(tmp_path, "custom")
    assert source.supports("A") and source.supports("，")
    assert source.missing("A，1。") == ["1", "。"]


@pytest.mark.parametrize("directory", ["shared-fonts", "absolute", "", None])
def test_configured_font_directory(tmp_path, monkeypatch, directory):
    from datasets import Dataset
    from pygmalion.fonts.registry import dataset_dir
    from pygmalion.fonts.sources import source_from_dir

    root = tmp_path / "runtime"
    configured = (
        str(tmp_path / "external-fonts") if directory == "absolute" else directory
    )
    if configured is None:
        monkeypatch.delenv("PYGMALION_FONTS_DIR", raising=False)
    else:
        monkeypatch.setenv("PYGMALION_FONTS_DIR", configured)
    expected = (
        Path(configured)
        if directory == "absolute"
        else root / (configured or "model-bin")
    )
    assert dataset_dir(root) == expected
    font = expected / "test-font"
    font.mkdir(parents=True)
    Dataset.from_list(
        [{"char": "字", "lengths": [2], "x": [-4000, 4000], "y": [0, 0]}]
    ).to_parquet(font / "train.parquet")
    assert [item.id for item in fonts.discover(root)] == ["test-font"]
    assert fonts.resolve_chain(root, "test-font") == [str(font / "train.parquet")]
    assert source_from_dir(root, "test-font").supports("字")


@pytest.mark.parametrize("duplicate", [False, True])
def test_recursive_discovery_resolves_real_dataset_paths(tmp_path, duplicate):
    from datasets import Dataset
    from pygmalion.fonts.sources import source_from_dir

    base = tmp_path / "model-bin"
    directories = [base / "arp244" / "cjk-medians"]
    if duplicate:
        directories.append(base / "other" / "deep" / "cjk-medians")
    for index, directory in enumerate(directories):
        directory.mkdir(parents=True)
        Dataset.from_list(
            [{"char": "甲乙"[index], "lengths": [2], "x": [-4000, 4000], "y": [0, 0]}]
        ).to_parquet(directory / "train.parquet")
    available = fonts.discover(tmp_path)
    assert len(available) == len(directories)
    assert len({font.id for font in available}) == len(directories)
    for font in available:
        paths = fonts.resolve_chain(tmp_path, font.id)
        assert paths[0] == str(font.dataset_path)
        assert all(Path(path).is_file() for path in paths)
        assert source_from_dir(tmp_path, font.id).supports("甲")
        assert "dataset_path" not in font.to_dict()
    if not duplicate:
        assert available[0].id == "cjk-medians"
