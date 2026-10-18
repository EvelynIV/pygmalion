import { test, expect, type Page } from "@playwright/test";

async function saved(page: Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("pygmalion-document-v2")!),
  );
}

test("数值允许清空和重填，非法值提示并阻止操作，支持撤销重做", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  const size = page.getByLabel("字号", { exact: true });
  await size.fill("");
  await expect(size).toHaveValue("");
  expect((await saved(page)).elements[0].font_size_mm).toBe(8);
  await expect(
    page.getByRole("button", { name: "保存", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "自适应", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeDisabled();
  await size.press("Tab");
  await expect(page.locator(".number-field-error")).toContainText(
    "完整的有效数值",
  );
  await size.fill("abc");
  await expect(size).toHaveAttribute("aria-invalid", "true");
  await size.fill("3");
  await expect(page.locator(".number-field-error")).toContainText("4–40 mm");
  await size.fill("4.5");
  expect((await saved(page)).elements[0].font_size_mm).toBe(4.5);
  await expect(page.locator(".number-field-error")).toHaveCount(0);
  await size.press("Control+z");
  await expect(size).toHaveValue("8");
  await expect(size).toBeFocused();
  await size.press("Control+y");
  await expect(size).toHaveValue("4.5");
  await size.fill("999");
  await size.press("Escape");
  await expect(size).toHaveValue("4.5");
  await expect(size).toHaveAttribute("aria-invalid", "false");
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const row = page.getByLabel("当前行高", { exact: true });
  await row.fill("40");
  await expect(page.locator(".number-field-error")).toContainText(
    "0.25–39.75 mm",
  );
  expect((await saved(page)).elements[0].row_heights).toEqual([20, 20, 20]);
  await row.fill("39.75");
  expect((await saved(page)).elements[0].row_heights).toEqual([
    39.75, 0.25, 20,
  ]);
  await row.fill("");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(row).toHaveValue("20");
  await expect(
    page.getByRole("button", { name: "保存", exact: true }),
  ).toBeEnabled();
  expect(errors).toEqual([]);
});

test("自适应保留 4mm 和已有行列，扩展数据集可预览中英文数字与标点", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await page.getByLabel("字号", { exact: true }).fill("4");
  for (const [index, text] of ["甲乙", "甲乙", "甲乙丙丁"].entries()) {
    await page
      .getByRole("gridcell", { name: new RegExp(`^单元格 1行${index + 1}列`) })
      .click();
    const editor = page.getByLabel(`单元格 1行${index + 1}列内容`, {
      exact: true,
    });
    await editor.fill(text);
    await editor.press("Control+Enter");
  }
  const before = (await saved(page)).elements[0];
  await page.getByRole("button", { name: "自适应", exact: true }).click();
  await expect(page.getByText("表格已自适应，外框尺寸保持不变")).toBeVisible();
  expect((await saved(page)).elements[0]).toEqual(before);
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByLabel("单元格 1行1列内容", { exact: true });
  await editor.fill("甲Abc0123，。±℃￥");
  await editor.press("Control+Enter");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".inline-preview-status")).not.toContainText(
    "缺字",
  );
});
