import { test, expect } from "@playwright/test";

test("顶栏文档名称逐字保存并支持撤销重做，属性对齐写入真实单元格", async ({
  page,
}) => {
  await page.goto("/");
  const name = page.getByLabel("文档名称", { exact: true });
  await expect(page.locator(".header").getByLabel("文档名称")).toBeVisible();
  await name.fill("");
  await name.press("h");
  await expect(name).toHaveValue("h");
  expect(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("pygmalion-document-v2")!).name,
    ),
  ).toBe("h");
  await name.press("Control+z");
  await expect(name).toHaveValue("未命名文档");
  await expect(name).toBeFocused();
  await name.press("Control+y");
  await expect(name).toHaveValue("h");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await expect(page.getByLabel("实际书写表格线")).toBeChecked();
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await page.getByLabel("单元格 1行1列内容", { exact: true }).press("Escape");
  await page.getByRole("radio", { name: "水平居中", exact: true }).click();
  await page.getByRole("radio", { name: "底部对齐", exact: true }).click();
  await page
    .getByRole("radio", { name: "底部对齐", exact: true })
    .press("ArrowUp");
  await expect(
    page.getByRole("radio", { name: "垂直居中", exact: true }),
  ).toBeFocused();
  await page
    .getByRole("radio", { name: "垂直居中", exact: true })
    .press("ArrowDown");
  const cell = await page.evaluate(
    () =>
      JSON.parse(localStorage.getItem("pygmalion-document-v2")!).elements[0]
        .cells[0],
  );
  expect(cell).toMatchObject({ align: "center", valign: "bottom" });
  const size = page.getByLabel("字号", { exact: true });
  await size.fill("4");
  await size.press("ArrowUp");
  await expect(size).toHaveValue("4.1");
  await size.press("ArrowDown");
  await expect(size).toHaveValue("4");
  const beforeRules = await page.evaluate(() =>
    localStorage.getItem("pygmalion-document-v2"),
  );
  const rules = page.getByRole("button", { name: "规则 ›", exact: true });
  await rules.click();
  await expect(rules).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#fit-rules")).toContainText(
    "优先保留原布局和字号",
  );
  expect(
    await page.evaluate(() => localStorage.getItem("pygmalion-document-v2")),
  ).toBe(beforeRules);
  await rules.click();
  await page.getByRole("button", { name: "属性", exact: true }).click();
  await expect(
    page.getByRole("complementary", { name: "纸面与元素设置" }),
  ).toBeHidden();
  await page.getByRole("button", { name: "属性", exact: true }).click();
  await expect(
    page.getByRole("radio", { name: "水平居中", exact: true }),
  ).toHaveAttribute("aria-checked", "true");
});

test("窄屏属性按需打开，不遮挡单击输入，帮助可关闭并返回焦点", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await expect(page.locator("#paper-inspector")).toBeHidden();
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByLabel("单元格 1行1列内容", { exact: true });
  await editor.fill("甲");
  await editor.press("Escape");
  await page.getByRole("button", { name: "属性", exact: true }).click();
  await expect(page.getByLabel("字号", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "属性", exact: true }).click();
  const help = page.getByRole("button", { name: "快捷键与帮助" });
  await help.click();
  await expect(page.getByRole("dialog")).toContainText("输入即保存");
  await page.keyboard.press("h");
  await expect(
    page.getByRole("button", { name: "小手工具 (H)" }),
  ).toHaveAttribute("aria-pressed", "false");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(help).toBeFocused();
});
