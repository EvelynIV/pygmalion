import { mkdir, copyFile, readdir, access, rm } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";

async function findFont(directory: string): Promise<string | undefined> {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort(
    (a, b) => a.name.localeCompare(b.name),
  )) {
    if (entry.name.startsWith(".")) continue;
    const file = path.join(directory, entry.name);
    if (entry.isFile() && entry.name === "train.parquet") return file;
    if (entry.isDirectory()) {
      const found = await findFont(file);
      if (found) return found;
    }
  }
}
export default async function prepareRuntime() {
  const expanded = path.resolve(
    "../../model-bin/cjk-medians-plus/train.parquet",
  );
  const source = await access(expanded).then(
    () => expanded,
    () => findFont(path.resolve("../../model-bin")),
  );
  if (!source) throw new Error("浏览器验证需要 model-bin 下的字体数据集");
  const fonts = path.resolve("../../.cache/e2e/model-bin/cjk-medians-plus");
  await rm(fonts, { recursive: true, force: true });
  if (source === expanded) {
    await mkdir(fonts, { recursive: true });
    await copyFile(source, path.join(fonts, "train.parquet"));
  } else {
    // 只有原始字库时，在隔离测试目录生成补字，避免依赖本机未跟踪的产物。
    await promisify(execFile)(
      "poetry",
      [
        "run",
        "python",
        "-m",
        "pygmalion.commands.app",
        "build-font",
        "--base",
        source,
        "--output",
        fonts,
      ],
      { cwd: path.resolve("../..") },
    );
  }
  await mkdir(path.resolve("../../.cache/e2e"), { recursive: true });
}
