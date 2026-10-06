import { mkdir, copyFile } from "node:fs/promises";
import path from "node:path";

export default async function prepareRuntime() {
  const fonts = path.resolve("../../.cache/e2e/data/fonts");
  await mkdir(fonts, { recursive: true });
  await copyFile(
    path.resolve("../../data/fonts/cjk-medians.json"),
    path.join(fonts, "cjk-medians.json"),
  );
}
