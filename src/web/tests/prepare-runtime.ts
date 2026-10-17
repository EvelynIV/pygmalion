import { mkdir, copyFile } from "node:fs/promises";
import path from "node:path";

export default async function prepareRuntime() {
  const fonts = path.resolve("../../.cache/e2e/model-bin/cjk-medians");
  await mkdir(fonts, { recursive: true });
  await copyFile(
    path.resolve("../../model-bin/cjk-medians/train.parquet"),
    path.join(fonts, "train.parquet"),
  );
}
