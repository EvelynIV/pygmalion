import { test, expect, type Page, type Locator } from "@playwright/test";

const storageKey = "pygmalion-document-v2";
async function stored(page: Page) {
  return page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)!),
    storageKey,
  );
}
async function addText(page: Page) {
  await page.getByRole("button", { name: "添加文本框", exact: true }).click();
  return page.getByRole("textbox", { name: "文本框内容", exact: true });
}
async function inputAndRead(editor: Locator, value: string) {
  // 在同一次 JS 调用内触发真实 React input handler 并读取存储；没有等候或轮询。
  return editor.evaluate((element: HTMLTextAreaElement, text) => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(element, text);
    element.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: text,
      }),
    );
    return JSON.parse(localStorage.getItem("pygmalion-document-v2")!).elements;
  }, value);
}

test("逐字同步保存，预览失败和未失焦都不阻止刷新恢复", async ({ page }) => {
  await page.route("**/api/workbench/preview", (route) =>
    route.fulfill({ status: 503, json: { error: "预览暂不可用" } }),
  );
  await page.goto("/");
  const editor = await addText(page);
  for (const value of ["甲", "甲乙", "甲乙丙", "甲乙", "甲乙\n丙"]) {
    expect((await inputAndRead(editor, value))[0].text).toBe(value);
    await expect(editor).toBeFocused();
  }
  await expect(page.locator(".document-save-status")).toHaveText("已自动保存");
  let dialogs = 0;
  page.on("dialog", async (dialog) => {
    dialogs++;
    await dialog.dismiss();
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: "文本框：甲乙\n丙", exact: true }),
  ).toBeVisible();
  expect(dialogs).toBe(0);
  const name = page.getByLabel("文档名称", { exact: true });
  await name.fill("逐字保存的文档");
  expect((await stored(page)).name).toBe("逐字保存的文档");
  await page.reload();
  await expect(name).toHaveValue("逐字保存的文档");
});

test("整次编辑撤销重做，保持输入焦点，重复快捷键和新分支不会丢字", async ({
  page,
}) => {
  await page.goto("/");
  const editor = await addText(page);
  for (const text of ["甲", "甲乙", "甲乙丙"]) await inputAndRead(editor, text);
  await editor.press("Control+z");
  await expect(editor).toHaveValue("");
  await expect(editor).toBeFocused();
  expect((await stored(page)).elements[0].text).toBe("");
  await editor.press("Control+y");
  await expect(editor).toHaveValue("甲乙丙");
  expect((await stored(page)).elements[0].text).toBe("甲乙丙");
  await editor.press("Control+Enter");

  // 无修改的再进入/退出，不应产生额外的历史步骤。
  await page.getByRole("button", { name: /^文本框：/ }).click();
  await editor.press("Escape");
  await page.keyboard.press("Control+z");
  expect((await stored(page)).elements[0].text).toBe("");
  await page.keyboard.press("Control+Shift+z");
  expect((await stored(page)).elements[0].text).toBe("甲乙丙");

  await page.getByRole("button", { name: /^文本框：/ }).click();
  await editor.fill("甲乙丙丁");
  await editor.press("Escape");
  expect((await stored(page)).elements[0].text).toBe("甲乙丙丁");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  expect((await stored(page)).elements[0].text).toBe("甲乙丙");
  await page.getByRole("button", { name: "重做", exact: true }).click();
  expect((await stored(page)).elements[0].text).toBe("甲乙丙丁");

  await page.getByRole("button", { name: /^文本框：/ }).click();
  await editor.press("Control+z");
  await expect(editor).toHaveValue("甲乙丙");
  await editor.fill("新内容");
  await expect(
    page.getByRole("button", { name: "重做", exact: true }),
  ).toBeDisabled();
  await editor.press("Control+y");
  await expect(editor).toHaveValue("新内容");
  await editor.press("Control+z");
  await expect(editor).toHaveValue("甲乙丙");
  await editor.press("Control+z");
  await expect(editor).toHaveValue("");
  await editor.press("Control+z");
  await expect(page.locator(".paper-element")).toHaveCount(0);
  expect((await stored(page)).elements).toEqual([]);
  await page.keyboard.press("Control+y");
  await expect(page.locator(".paper-element")).toHaveCount(1);
});

test("表格单元格逐字保存，中文组合输入不触发撤销，确认后整次可回退", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByRole("textbox", {
    name: "单元格 1行1列内容",
    exact: true,
  });
  await editor.dispatchEvent("compositionstart", { data: "" });
  expect((await inputAndRead(editor, "zhong"))[0].cells[0].text).toBe("zhong");
  const prevented = await editor.evaluate((element) => {
    const event = new KeyboardEvent("keydown", {
      key: "z",
      ctrlKey: true,
      isComposing: true,
      bubbles: true,
      cancelable: true,
    });
    element.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(prevented).toBe(false);
  const confirmationPrevented = await editor.evaluate((element) => {
    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      isComposing: true,
      bubbles: true,
      cancelable: true,
    });
    element.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(confirmationPrevented).toBe(false);
  await expect(editor).toBeFocused();
  await expect(editor).toHaveValue("zhong");
  expect((await inputAndRead(editor, "中"))[0].cells[0].text).toBe("中");
  await editor.dispatchEvent("compositionend", { data: "中" });
  await editor.press("Control+Enter");
  await page.keyboard.press("Control+z");
  expect((await stored(page)).elements[0].cells[0].text).toBe("");
  await page.keyboard.press("Control+y");
  expect((await stored(page)).elements[0].cells[0].text).toBe("中");
  await page.reload();
  await expect(
    page.getByRole("gridcell", { name: "单元格 1行1列：中", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("gridcell", { name: "单元格 1行1列：中", exact: true })
    .click();
  const borders = page.getByLabel("实际书写表格线");
  await borders.uncheck();
  await borders.check();
  await page.keyboard.press("Control+z");
  await expect(borders).not.toBeChecked();
  await page.keyboard.press("Control+z");
  await expect(borders).toBeChecked();
});

test("保存失败保留文字，导出仍可用，手动重试和下次输入都能恢复", async ({
  page,
}) => {
  await page.goto("/");
  const editor = await addText(page);
  await editor.fill("甲");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Object.assign(window, {
      restoreDraftStorage: () => {
        Storage.prototype.setItem = original;
      },
    });
    Storage.prototype.setItem = function (key, value) {
      if (key === "pygmalion-document-v2")
        throw new DOMException("空间不足", "QuotaExceededError");
      return original.call(this, key, value);
    };
  });
  await editor.fill("甲乙");
  await expect(editor).toHaveValue("甲乙");
  await expect(page.locator(".document-save-status")).toContainText(
    "自动保存失败",
  );
  expect((await stored(page)).elements[0].text).toBe("甲");
  expect(
    await page.evaluate(
      () =>
        !window.dispatchEvent(new Event("beforeunload", { cancelable: true })),
    ),
  ).toBe(true);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const file = await download;
  const fs = await import("node:fs/promises");
  expect(
    JSON.parse(await fs.readFile((await file.path())!, "utf8")).elements[0]
      .text,
  ).toBe("甲乙");
  await page.evaluate(() =>
    (
      window as unknown as { restoreDraftStorage: () => void }
    ).restoreDraftStorage(),
  );
  await page.getByRole("button", { name: "重试保存", exact: true }).click();
  expect((await stored(page)).elements[0].text).toBe("甲乙");
  await expect(page.locator(".document-save-status")).toHaveText("已自动保存");
  expect(
    await page.evaluate(
      () =>
        !window.dispatchEvent(new Event("beforeunload", { cancelable: true })),
    ),
  ).toBe(false);

  await page.getByRole("button", { name: /^文本框：/ }).click();
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "pygmalion-document-v2") {
        Storage.prototype.setItem = original;
        throw new DOMException("暂时不可写", "QuotaExceededError");
      }
      return original.call(this, key, value);
    };
  });
  await editor.fill("甲乙丙");
  await expect(page.locator(".document-save-status")).toContainText(
    "自动保存失败",
  );
  expect((await stored(page)).elements[0].text).toBe("甲乙");
  await editor.fill("甲乙丙丁");
  expect((await stored(page)).elements[0].text).toBe("甲乙丙丁");
  await expect(page.locator(".document-save-status")).toHaveText("已自动保存");
});

test("新建确认不受自动保存影响，取消保留逐字草稿，确认新建也立即持久化", async ({
  page,
}) => {
  await page.goto("/");
  const editor = await addText(page);
  await editor.fill("甲乙");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("尚未导出文件");
  const before = await stored(page);
  await page.keyboard.press("Control+z");
  expect(await stored(page)).toEqual(before);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect((await stored(page)).elements[0].text).toBe("甲乙");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByRole("button", { name: "继续新建", exact: true }).click();
  expect((await stored(page)).elements).toEqual([]);
  await page.reload();
  await expect(page.locator(".paper-element")).toHaveCount(0);
});

test("属性编辑共享历史、保留50步上限，Mac快捷键同样可用", async ({ page }) => {
  await page.goto("/");
  const name = page.getByLabel("文档名称", { exact: true });
  for (let step = 1; step <= 55; step++) {
    await name.fill(`文档${step}`);
    await name.press("Tab");
  }
  for (let step = 0; step < 50; step++) await page.keyboard.press("Control+z");
  await expect(name).toHaveValue("文档5");
  await expect(
    page.getByRole("button", { name: "撤销", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Control+z");
  expect((await stored(page)).name).toBe("文档5");
  await page.keyboard.press("Meta+Shift+z");
  await expect(name).toHaveValue("文档6");
  await page.keyboard.press("Meta+z");
  await expect(name).toHaveValue("文档5");
});

test("超过既有文字上限时保留可恢复草稿，修正后自动保存恢复", async ({
  page,
}) => {
  await page.goto("/");
  const editor = await addText(page);
  await editor.fill("甲乙");
  await editor.fill("甲".repeat(20001));
  await expect(editor).toHaveValue("甲".repeat(20001));
  await expect(page.locator(".document-save-status")).toContainText(
    "自动保存失败",
  );
  expect((await stored(page)).elements[0].text).toBe("甲乙");
  await editor.fill("甲乙丙");
  await expect(page.locator(".document-save-status")).toHaveText("已自动保存");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "文本框：甲乙丙", exact: true }),
  ).toBeVisible();
});

test("直接从文字输入拖动元素时，文字编辑与拖动各算一步", async ({ page }) => {
  await page.goto("/");
  const editor = await addText(page);
  await editor.fill("甲乙");
  const before = (await stored(page)).elements[0];
  const handle = await page
    .getByRole("button", { name: "移动文本框", exact: true })
    .boundingBox();
  await page.mouse.move(handle!.x + 8, handle!.y + 8);
  await page.mouse.down();
  await page.mouse.move(handle!.x + 36, handle!.y + 36, { steps: 4 });
  await page.mouse.up();
  expect((await stored(page)).elements[0].x_mm).toBeGreaterThan(before.x_mm);
  await page.keyboard.press("Control+z");
  const restored = (await stored(page)).elements[0];
  expect(restored.x_mm).toBe(before.x_mm);
  expect(restored.text).toBe("甲乙");
  await page.keyboard.press("Control+z");
  expect((await stored(page)).elements[0].text).toBe("");
});
