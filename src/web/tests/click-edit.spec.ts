import { test, expect } from "@playwright/test";

test("文本框单击即输入，空白处结束编辑，小手模式保留平移行为", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加文本框", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "文本框内容", exact: true });
  await editor.fill("甲乙");
  await editor.press("Escape");
  const text = page.getByRole("button", { name: "文本框：甲乙", exact: true });
  await text.click();
  await expect(editor).toBeFocused();
  await editor.press("Control+a");
  await page.keyboard.insertText("甲乙丙");
  await page.locator(".canvas-paper").click({ position: { x: 8, y: 8 } });
  await expect(editor).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("pygmalion-document-v2")!).elements[0]
          .text,
    ),
  ).toBe("甲乙丙");

  await page.keyboard.press("h");
  const textBox = await page
    .getByRole("button", { name: "文本框：甲乙丙", exact: true })
    .boundingBox();
  // 小手模式会关闭元素的命中区域；用真实鼠标坐标点击纸面。
  await page.mouse.click(
    textBox!.x + textBox!.width / 2,
    textBox!.y + textBox!.height / 2,
  );
  await expect(editor).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "文本框：甲乙丙", exact: true })
    .click();
  await expect(editor).toBeFocused();
  await editor.press("Control+z");
  await expect(editor).toHaveValue("甲乙");
  await editor.press("Control+y");
  await expect(editor).toHaveValue("甲乙丙");
});

test("单元格单击输入、切换编辑和Shift范围选择互不冲突", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  const first = page.getByRole("gridcell", { name: /^单元格 1行1列/ });
  const second = page.getByRole("gridcell", { name: /^单元格 1行2列/ });
  await first.click();
  const firstEditor = page.getByRole("textbox", {
    name: "单元格 1行1列内容",
    exact: true,
  });
  await expect(firstEditor).toBeFocused();
  await page.keyboard.insertText("甲");
  await second.click();
  const secondEditor = page.getByRole("textbox", {
    name: "单元格 1行2列内容",
    exact: true,
  });
  await expect(firstEditor).toHaveCount(0);
  await expect(secondEditor).toBeFocused();
  await page.keyboard.insertText("乙");
  await first.click({ modifiers: ["Shift"] });
  await expect(secondEditor).toHaveCount(0);
  await expect(page.getByRole("gridcell", { selected: true })).toHaveCount(2);
  await expect(page.getByRole("gridcell")).toHaveCount(9);

  // 编辑时的方向键仍操作文字；Esc 后的方向键操作单元格。
  await first.click();
  await expect(firstEditor).toBeFocused();
  await firstEditor.press("ArrowLeft");
  await expect(firstEditor).toBeFocused();
  await firstEditor.press("Escape");
  await expect(first).toBeFocused();
  await page.keyboard.press("Shift+ArrowRight");
  await expect(page.getByRole("gridcell", { selected: true })).toHaveCount(2);

  // 触屏的扩展选区按钮同样只选择单元格，不打开输入框。
  await page
    .getByRole("button", { name: "扩展单元格选区", exact: true })
    .click();
  await page.getByRole("gridcell", { name: /^单元格 2行2列/ }).click();
  await expect(
    page.getByRole("textbox", { name: /^单元格 .*内容$/ }),
  ).toHaveCount(0);
  await expect(page.getByRole("gridcell", { selected: true })).toHaveCount(4);
});

test.describe("触屏输入", () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test("文本框和单元格轻点一次即可输入", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "添加文本框", exact: true }).tap();
    const editor = page.getByRole("textbox", {
      name: "文本框内容",
      exact: true,
    });
    await editor.press("Escape");
    await page.getByRole("button", { name: "文本框：空白", exact: true }).tap();
    await expect(editor).toBeFocused();
    await page.keyboard.insertText("甲");
    await editor.press("Escape");
    await page.getByRole("button", { name: "添加表格", exact: true }).tap();
    await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).tap();
    await expect(
      page.getByRole("textbox", { name: "单元格 1行1列内容", exact: true }),
    ).toBeFocused();
    await page.keyboard.insertText("乙");
    const saved = await page.evaluate(() =>
      JSON.parse(localStorage.getItem("pygmalion-document-v2")!),
    );
    expect(saved.elements[0].text).toBe("甲");
    expect(saved.elements[1].cells[0].text).toBe("乙");
  });
});
