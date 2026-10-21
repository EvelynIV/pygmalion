import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

const source = `<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="20mm" viewBox="0 0 40 20"><rect width="40" height="20" fill="none" stroke="black"/><path d="M5 10 Q20 0 35 10" fill="none" stroke="black"/></svg>`;
const snapshot = async (page: import("@playwright/test").Page) =>
  page.evaluate(() =>
    JSON.parse(localStorage.getItem("pygmalion-document-v2")!),
  );
async function upload(page: import("@playwright/test").Page, svg = source) {
  await page.getByLabel("SVG 文件", { exact: true }).setInputFiles({
    name: "图案.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from(svg),
  });
}

test("SVG 导入、等比尺寸、即时保存、撤销重做及重新打开", async ({ page }) => {
  await page.goto("/");
  await upload(page);
  const target = page.getByRole("button", {
    name: "矢量图：图案",
    exact: true,
  });
  await expect(target).toBeVisible();
  await expect(page.getByLabel("锁定宽高比")).toBeChecked();
  await expect(page.getByLabel("字体", { exact: true })).toHaveCount(0);
  const vector = (await snapshot(page)).elements[0];
  expect(vector.width_mm).toBeCloseTo(40, 3);
  expect(vector.height_mm).toBeCloseTo(20, 3);
  const width = page.getByLabel("宽度", { exact: true });
  await width.fill("80");
  await width.press("Tab");
  expect((await snapshot(page)).elements[0].height_mm).toBeCloseTo(40, 3);
  await page.getByRole("button", { name: /撤销/ }).click();
  await expect(width).toHaveValue("40");
  await page.getByRole("button", { name: /重做/ }).click();
  await expect(width).toHaveValue("80");
  await page.getByLabel("锁定宽高比").uncheck();
  await width.fill("60");
  await width.press("Tab");
  expect((await snapshot(page)).elements[0].height_mm).toBeCloseTo(40, 3);
  await page
    .getByRole("button", { name: "移动矢量图", exact: true })
    .press("ArrowRight");
  expect((await snapshot(page)).elements[0].x_mm).toBeCloseTo(20.1);
  await page.reload();
  await expect(target).toBeVisible();
  await target.click();
  await expect(width).toHaveValue("60");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const download = await downloadPromise;
  const exported = await download.path();
  expect(exported).toBeTruthy();
  await page.getByRole("button", { name: "删除元素", exact: true }).click();
  await expect(target).toHaveCount(0);
  await page.getByRole("button", { name: /撤销/ }).click();
  await expect(target).toBeVisible();
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "恢复.pygmalion.json",
    mimeType: "application/json",
    buffer: await readFile(exported!),
  });
  await target.click();
  await expect(width).toHaveValue("60");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await page.screenshot({ path: "../../.cache/vector-desktop.png" });
  await page.getByRole("button", { name: "切换为夜间主题" }).click();
  await page.screenshot({ path: "../../.cache/vector-dark.png" });
});

test("错误导入保留文档，混排与窄屏入口可用", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  const before = await snapshot(page);
  await upload(
    page,
    '<svg xmlns="http://www.w3.org/2000/svg"><text>hello</text></svg>',
  );
  await expect(page.getByText(/暂不支持 SVG 元素 text/)).toBeVisible();
  expect(await snapshot(page)).toEqual(before);
  await upload(page);
  await expect(
    page.getByRole("button", { name: "矢量图：图案", exact: true }),
  ).toBeVisible();
  expect(
    (await snapshot(page)).elements.map((e: { type: string }) => e.type),
  ).toEqual(["table", "vector"]);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "../../.cache/vector-mobile.png" });
});

test("导入返回前文档变化时丢弃结果", async ({ page }) => {
  await page.goto("/");
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/workbench/import-svg", async (route) => {
    const response = await route.fetch();
    await held;
    await route.fulfill({ response });
  });
  await upload(page);
  await expect(page.getByRole("button", { name: "导入中…" })).toBeDisabled();
  await page.getByRole("button", { name: "添加文本框", exact: true }).click();
  release();
  await expect(page.getByText("文档已变化，请重新导入 SVG")).toBeVisible();
  expect(
    (await snapshot(page)).elements.map((e: { type: string }) => e.type),
  ).toEqual(["text"]);
});

test("非法矢量文档不会覆盖本机草稿", async ({ page }) => {
  await page.goto("/");
  await upload(page);
  await expect(
    page.getByRole("button", { name: "矢量图：图案", exact: true }),
  ).toBeVisible();
  const before = await snapshot(page);
  const invalid = structuredClone(before);
  invalid.elements[0].paths = ["M0 0 L2 1"];
  invalid.elements[0].fill_rules = [null];
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(invalid)),
  });
  await expect(page.getByText("请求失败（422）")).toBeVisible();
  expect(await snapshot(page)).toEqual(before);
});

test("大尺寸线稿等比适配纸面，细长外框仍可绘制", async ({ page }) => {
  await page.goto("/");
  await upload(
    page,
    '<svg xmlns="http://www.w3.org/2000/svg" width="10000mm" height="1mm"><line x2="10000mm" stroke="black"/></svg>',
  );
  await expect(
    page.getByRole("button", { name: "矢量图：图案", exact: true }),
  ).toBeVisible();
  const vector = (await snapshot(page)).elements[0];
  expect(vector.width_mm).toBeCloseTo(170, 3);
  expect(vector.height_mm).toBeGreaterThanOrEqual(0.001);
  expect(vector.height_mm).toBeLessThan(0.5);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
});

test("实心填充设置即时保存、空值校验、撤销和重新打开", async ({ page }) => {
  await page.goto("/");
  await upload(
    page,
    '<svg width="20mm" height="20mm" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M0 0H20V20H0Z M5 5H15V15H5Z"/></svg>',
  );
  const mode = page.getByLabel("绘制模式", { exact: true });
  await expect(mode).toHaveValue("solid");
  const spacing = page.getByLabel("填充间距", { exact: true });
  await expect(spacing).toHaveValue("0.24");
  await spacing.fill("");
  await expect(spacing).toHaveValue("");
  expect((await snapshot(page)).elements[0].fill_spacing_mm).toBe(0.24);
  await spacing.fill("0.18");
  expect((await snapshot(page)).elements[0].fill_spacing_mm).toBe(0.18);
  await spacing.press("Tab");
  await page.getByRole("button", { name: /撤销/ }).click();
  await expect(spacing).toHaveValue("0.24");
  await page.getByRole("button", { name: /重做/ }).click();
  await expect(spacing).toHaveValue("0.18");
  await mode.selectOption("outline");
  await expect(spacing).toHaveCount(0);
  await mode.selectOption("solid");
  await expect(spacing).toHaveValue("0.18");
  await page.reload();
  await page.getByRole("button", { name: "矢量图：图案", exact: true }).click();
  await expect(mode).toHaveValue("solid");
  await expect(spacing).toHaveValue("0.18");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await page.screenshot({ path: "../../.cache/vector-fill.png" });
});
