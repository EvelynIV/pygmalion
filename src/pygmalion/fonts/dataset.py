"""从本地 Hugging Face Parquet 字库还原单位化笔画。"""

from pathlib import Path

from datasets import load_dataset

COORDINATE_SCALE = 10_000


def load_parquet_font(
    path: Path, *, preserve_origins: set[str] | None = None
) -> dict[str, list[list[list[float]]]]:
    dataset = load_dataset(
        "parquet", data_files={"train": str(path.resolve())}, split="train"
    )
    result = {}
    for row in dataset:
        lengths, xs, ys = row["lengths"], row["x"], row["y"]
        if len(xs) != len(ys) or sum(lengths) != len(xs):
            raise ValueError(f"字形 {row['char']} 的笔画边界与坐标数量不一致")
        strokes = []
        offset = 0
        for length in lengths:
            strokes.append(
                [
                    [xs[i] / COORDINATE_SCALE, ys[i] / COORDINATE_SCALE]
                    for i in range(offset, offset + length)
                ]
            )
            offset += length
        if row["char"] in result:
            raise ValueError(f"重复字形：{row['char']}")
        result[row["char"]] = strokes
        if preserve_origins is not None and row.get("preserve_origin", False):
            preserve_origins.add(row["char"])
    return result
