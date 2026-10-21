import { test, expect } from "@playwright/test";

const seed = {
  version: 2,
  name: "混排文档",
  paper: "A4",
  margin_mm: 20,
  elements: [
    {
      id: "text",
      type: "text",
      x_mm: 20,
      y_mm: 20,
      width_mm: 80,
      height_mm: 30,
      font: "",
      font_size_mm: 4,
      char_gap_mm: 0,
      line_gap_mm: 3,
      text: "甲乙",
      align: "left",
    },
    {
      id: "table",
      type: "table",
      x_mm: 20,
      y_mm: 60,
      width_mm: 80,
      height_mm: 30,
      font: "",
      font_size_mm: 4,
      char_gap_mm: 0,
      line_gap_mm: 3,
      column_widths: [80],
      row_heights: [30],
      cells: [
        {
          row: 0,
          col: 0,
          rowspan: 1,
          colspan: 1,
          text: "甲",
          align: "left",
          valign: "center",
        },
      ],
      padding_mm: 1.5,
      draw_borders: true,
    },
    {
      id: "vector",
      type: "vector",
      name: "线稿",
      x_mm: 20,
      y_mm: 100,
      width_mm: 40,
      height_mm: 20,
      paths: ["M0 0 L1 1"],
      lock_aspect: true,
    },
  ],
};
async function saved(page: import("@playwright/test").Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("pygmalion-document-v2")!),
  );
}
test.beforeEach(async ({ page }) => {
  await page.addInitScript(
    (document) =>
      localStorage.setItem("pygmalion-document-v2", JSON.stringify(document)),
    seed,
  );
  await page.goto("/");
});
for (const key of ["Backspace", "Delete"]) {
  test(`Ctrl+A 全选混排元素，${key} 一次删除并可撤销重做`, async ({ page }) => {
    await page.keyboard.press("Control+a");
    await expect(page.locator(".paper-element.selected")).toHaveCount(3);
    await expect(
      page.getByRole("heading", { name: "已选 3 个元素" }),
    ).toBeVisible();
    await page.keyboard.press(key);
    await expect(page.locator(".paper-element")).toHaveCount(0);
    expect((await saved(page)).elements).toEqual([]);
    await page.keyboard.press("Control+z");
    await expect(page.locator(".paper-element")).toHaveCount(3);
    expect(await saved(page)).toEqual(seed);
    await page.keyboard.press("Control+y");
    await expect(page.locator(".paper-element")).toHaveCount(0);
  });
}
test("Esc 取消全选，单选替换全选，文字输入保留原生全选删除", async ({
  page,
}) => {
  await page.keyboard.press("Control+a");
  await page.keyboard.press("Escape");
  await expect(page.locator(".paper-element.selected")).toHaveCount(0);
  await page.keyboard.press("Delete");
  await expect(page.locator(".paper-element")).toHaveCount(3);
  await page.keyboard.press("Control+a");
  await page.getByRole("button", { name: "矢量图：线稿" }).click();
  await expect(page.locator(".paper-element.selected")).toHaveCount(1);
  await page.getByRole("button", { name: "文本框：甲乙" }).click();
  const editor = page.getByLabel("文本框内容", { exact: true });
  await editor.press("Control+a");
  await editor.press("Backspace");
  await expect(editor).toHaveValue("");
  await expect(page.locator(".paper-element")).toHaveCount(3);
  expect((await saved(page)).elements[0].text).toBe("");
  const name = page.getByLabel("文档名称", { exact: true });
  await name.fill("文档");
  await name.press("Control+a");
  await name.press("Delete");
  await expect(name).toHaveValue("");
  await expect(page.locator(".paper-element")).toHaveCount(3);
});
test("帮助对话框内快捷键不影响纸面", async ({ page }) => {
  await page.keyboard.press("Control+a");
  await page.getByRole("button", { name: "快捷键与帮助", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Control+a");
  await page.keyboard.press("Delete");
  expect(await saved(page)).toEqual(seed);
});
