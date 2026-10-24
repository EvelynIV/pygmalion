import { test, expect, type Page } from "@playwright/test";

async function editText(page: Page, text: string) {
  await page.getByRole("button", { name: "添加文本框", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "文本框内容", exact: true });
  await editor.fill(text);
  await editor.press("Control+Enter");
}
async function editCell(page: Page, row: number, col: number, text: string) {
  await page
    .getByRole("gridcell", { name: new RegExp(`^单元格 ${row}行${col}列`) })
    .click();
  const editor = page.getByRole("textbox", {
    name: `单元格 ${row}行${col}列内容`,
    exact: true,
  });
  await editor.fill(text);
  await editor.press("Control+Enter");
}
async function exportDocument(page: Page) {
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const file = await pending;
  const location = await file.path();
  const fs = await import("node:fs/promises");
  return JSON.parse(await fs.readFile(location!, "utf8"));
}

test("纸面编辑：文本框、拖放、缩放、撤销重做、保存和演练冻结", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator(".body-editor")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await editText(page, "甲乙\n\n丙");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "竖排", exact: true }),
  ).toHaveCount(0);
  const before = await exportDocument(page);
  const grip = page.getByRole("button", { name: "移动文本框", exact: true });
  const box = await grip.boundingBox();
  await page.mouse.move(box!.x + 10, box!.y + 10);
  await page.mouse.down();
  await page.mouse.move(box!.x + 28, box!.y + 28, { steps: 5 });
  await page.mouse.up();
  const moved = await exportDocument(page);
  expect(moved.elements[0].x_mm).toBeGreaterThan(before.elements[0].x_mm);
  const resize = await page
    .getByRole("button", { name: "调整元素大小", exact: true })
    .boundingBox();
  await page.mouse.move(resize!.x + 4, resize!.y + 4);
  await page.mouse.down();
  await page.mouse.move(resize!.x - 12, resize!.y - 12, { steps: 5 });
  await page.mouse.up();
  const resized = await exportDocument(page);
  expect(resized.elements[0].width_mm).toBeLessThan(moved.elements[0].width_mm);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  expect((await exportDocument(page)).elements[0].width_mm).toBeCloseTo(
    moved.elements[0].width_mm,
  );
  await page.getByRole("button", { name: "重做", exact: true }).click();
  expect((await exportDocument(page)).elements[0].width_mm).toBeCloseTo(
    resized.elements[0].width_mm,
  );
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "开始书写" }).click();
  await expect(page.getByRole("button", { name: "添加表格" })).toBeDisabled();
  const frozenDraft = await page.evaluate(() =>
    localStorage.getItem("pygmalion-document-v2"),
  );
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+y");
  expect(
    await page.evaluate(() => localStorage.getItem("pygmalion-document-v2")),
  ).toBe(frozenDraft);
  await page.getByRole("button", { name: "中止", exact: true }).click();
  await page.getByRole("button", { name: "确认中止" }).click();
  await expect(page.getByRole("button", { name: "添加表格" })).toBeEnabled();
  const saved = await exportDocument(page);
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await expect(page.locator(".paper-element")).toHaveCount(0);
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "saved.pygmalion.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(saved)),
  });
  await expect(
    page.getByRole("button", { name: "文本框：甲乙\n\n丙", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("表格：直接编辑、合并拆分、行列操作、固定外框自适应和表格线开关", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await editCell(page, 1, 1, "甲乙丙甲乙丙甲乙丙");
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await page
    .getByRole("gridcell", { name: /^单元格 1行2列/ })
    .click({ modifiers: ["Shift"] });
  await page.getByRole("button", { name: "合并单元格", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(8);
  await page.getByRole("button", { name: "拆分单元格", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(9);
  await editCell(page, 1, 2, "乙");
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await page
    .getByRole("gridcell", { name: /^单元格 1行2列/ })
    .click({ modifiers: ["Shift"] });
  await page.getByRole("button", { name: "合并单元格", exact: true }).click();
  await expect(
    page.getByText("只能合并最多一个单元格有文字的区域"),
  ).toBeVisible();
  await expect(page.getByRole("gridcell")).toHaveCount(9);
  const before = await exportDocument(page);
  await page.getByRole("button", { name: "自适应", exact: true }).click();
  await expect(page.getByText("表格已自适应，外框尺寸保持不变")).toBeVisible({
    timeout: 20000,
  });
  const after = await exportDocument(page);
  expect(after.elements[0].width_mm).toBe(before.elements[0].width_mm);
  expect(after.elements[0].height_mm).toBe(before.elements[0].height_mm);
  expect(after.elements[0].font_size_mm).toBeGreaterThanOrEqual(4);
  expect(
    after.elements[0].column_widths.reduce((a: number, b: number) => a + b, 0),
  ).toBeCloseTo(before.elements[0].width_mm);
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByLabel("实际书写表格线").uncheck();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "插入行", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(12);
  await page.getByRole("button", { name: "插入列", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(16);
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await page.getByRole("button", { name: "删除行", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(12);
  await page.getByRole("button", { name: "删除列", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(9);
});

test("缺字、越界、内容溢出及预览失败阻止执行，支持重试", async ({ page }) => {
  await page.goto("/");
  await editText(page, "甲\u{10ffff}");
  await expect(page.locator(".inline-preview-status")).toContainText("缺字");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await page.getByRole("button", { name: /^文本框：/ }).click();
  await page.getByLabel("文本框内容", { exact: true }).fill("甲\n".repeat(20));
  await page.getByLabel("文本框内容", { exact: true }).press("Control+Enter");
  await expect(page.locator(".inline-preview-status")).toContainText(
    "文本框内容溢出",
  );
  await page.getByLabel("横向位置", { exact: true }).fill("100");
  await expect(page.locator(".inline-preview-status")).toContainText(
    "超出纸面",
  );
  await page.route("**/api/workbench/preview", (route) =>
    route.fulfill({ status: 503, json: { error: "测试预览不可用" } }),
  );
  await page.getByLabel("横向位置", { exact: true }).fill("20");
  await expect(page.locator(".inline-preview-status")).toContainText(
    "测试预览不可用",
  );
  await page.unroute("**/api/workbench/preview");
  await page.getByRole("button", { name: "重新预览", exact: true }).click();
  await expect(page.locator(".inline-preview-status")).toContainText(
    "文本框内容溢出",
  );
});

test("旧文档迁移横排及键盘编辑和合并", async ({ page }) => {
  await page.goto("/");
  const old = {
    name: "旧文档",
    body: "甲乙",
    font: "",
    paper: "A4",
    direction: "vertical",
    align: "left",
    font_size_mm: 8,
    char_gap_mm: 0,
    line_gap_mm: 3,
    margin_mm: 20,
  };
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "old.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(old)),
  });
  await expect(
    page.getByText("文档已打开；旧竖排内容已转换为横排"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "文本框：甲乙", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("文本框内容", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "文本框：甲乙", exact: true }),
  ).toBeFocused();
  const doc = await exportDocument(page);
  expect(doc.version).toBe(2);
  expect(doc.elements[0].text).toBe("甲乙");
});

test("双主题与不同视口保持纸面及操作可见", async ({ page }) => {
  await page.goto("/");
  await editText(page, "甲乙丙");
  await expect(page.locator(".single-paper img")).toBeVisible();
  for (const night of [false, true]) {
    if (night)
      await page.getByRole("button", { name: "切换为夜间主题" }).click();
    for (const [width, height] of [
      [1024, 768],
      [1440, 1024],
      [1920, 1024],
      [390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
      await expect(
        page.getByRole("button", { name: "添加文本框", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "开始书写", exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: `test-results/canvas-${width}-${night ? "night" : "day"}.png`,
      });
    }
  }
});

test("软件参数保存与离线 GRBL 预览可审阅", async ({ page }) => {
  await page.goto("/admin/settings");
  await page.getByLabel("书写速度", { exact: true }).fill("1200");
  await page.getByRole("button", { name: "保存软件参数" }).click();
  await expect(page.getByText("软件参数已保存")).toBeVisible();
  await page.getByText("高级 GRBL 参数", { exact: true }).click();
  await page.getByRole("button", { name: "预览写入" }).click();
  await expect(page.getByRole("dialog")).toContainText("$110=4000");
  await page.getByRole("button", { name: "取消", exact: true }).click();
});

test("自适应旧响应不覆盖编辑，失败时保留原表格", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await editCell(page, 1, 1, "甲");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/workbench/table-fit", async (route) => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "自适应", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "正在计算…", exact: true }),
  ).toBeVisible();
  await page.getByLabel("字号", { exact: true }).fill("7");
  release();
  await expect(page.getByText("文档已变化，请重新自适应")).toBeVisible();
  await expect(page.getByLabel("字号", { exact: true })).toHaveValue("7");
  await page.unroute("**/api/workbench/table-fit");
  await editCell(page, 1, 1, "甲".repeat(2000));
  const before = await exportDocument(page);
  await page.getByRole("button", { name: "自适应", exact: true }).click();
  await expect(page.getByText(/4 毫米字号仍无法容纳/)).toBeVisible({
    timeout: 20000,
  });
  expect((await exportDocument(page)).elements[0]).toEqual(before.elements[0]);
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
});

test("键盘选区合并拆分、删除确认和表格线单独书写", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByLabel("实际书写表格线").uncheck();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await page.getByLabel("实际书写表格线").check();
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("gridcell", { name: /^单元格 1行1列/ }),
  ).toBeFocused();
  await page.keyboard.press("Shift+ArrowRight");
  await page.getByRole("button", { name: "合并单元格", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(8);
  await page.getByRole("button", { name: "拆分单元格", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(9);
  await editCell(page, 1, 1, "甲乙");
  await page.getByRole("button", { name: "删除行", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("所选行列包含文字");
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(6);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(
    page.getByRole("gridcell", { name: /^单元格 1行1列：甲乙/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await page.screenshot({ path: "test-results/canvas-table-day.png" });
  await page.getByRole("button", { name: "切换为夜间主题" }).click();
  await page.screenshot({ path: "test-results/canvas-table-night.png" });
});

test("Ctrl滚轮只缩放纸面，毫米刻度随滚动对齐且文档尺寸不变", async ({
  page,
}) => {
  await page.goto("/");
  await editText(page, "甲乙丙");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  const savedDocument = await exportDocument(page);
  const zoom = page.getByLabel("纸面缩放", { exact: true });
  await zoom.selectOption("1");
  const paper = page.locator(".canvas-paper"),
    stage = page.locator(".canvas-stage");
  await expect
    .poll(async () => (await paper.boundingBox())!.width)
    .toBeCloseTo((210 * 96) / 25.4, 0);
  const header = await page.locator(".header").boundingBox();
  const browser = await page.evaluate(() => ({
    width: innerWidth,
    ratio: devicePixelRatio,
  }));
  const before = (await paper.boundingBox())!;
  const area = (await stage.boundingBox())!;
  await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2);
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -240);
  await page.keyboard.up("Control");
  await expect
    .poll(async () => (await paper.boundingBox())!.width)
    .toBeGreaterThan(before.width * 1.3);
  expect(
    await page.evaluate(() => ({ width: innerWidth, ratio: devicePixelRatio })),
  ).toEqual(browser);
  expect((await page.locator(".header").boundingBox())!.height).toBe(
    header!.height,
  );
  const after = (await paper.boundingBox())!;
  const anchorX = area.x + area.width / 2,
    anchorY = area.y + area.height / 2;
  expect(
    Math.abs(
      (anchorX - after.x) / (after.width / 210) -
        (anchorX - before.x) / (before.width / 210),
    ),
  ).toBeLessThan(0.1);
  expect(
    Math.abs(
      (anchorY - after.y) / (after.height / 297) -
        (anchorY - before.y) / (before.height / 297),
    ),
  ).toBeLessThan(0.1);
  const outsidePrevented = await page.locator(".header").evaluate(
    (el) =>
      !el.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          ctrlKey: true,
          deltaY: -20,
        }),
      ),
  );
  expect(outsidePrevented).toBe(false);

  expect(await exportDocument(page)).toEqual(savedDocument);
  await zoom.selectOption("1");
  await expect
    .poll(async () => (await paper.boundingBox())!.width)
    .toBeCloseTo((210 * 96) / 25.4, 0);
  await stage.evaluate((el) => {
    el.scrollLeft = 123;
    el.scrollTop = 180;
  });
  await expect
    .poll(async () => {
      return page.evaluate(() => {
        const paper = document
          .querySelector(".canvas-paper")!
          .getBoundingClientRect();
        const horizontal = document
          .querySelector(".paper-ruler.horizontal")!
          .getBoundingClientRect();
        const vertical = document
          .querySelector(".paper-ruler.vertical")!
          .getBoundingClientRect();
        const x = document
          .querySelector('.paper-ruler.horizontal [data-mm="50"] line')
          ?.getAttribute("x1");
        const y = document
          .querySelector('.paper-ruler.vertical [data-mm="100"] line')
          ?.getAttribute("y1");
        if (x === undefined || x === null || y === undefined || y === null)
          return 1000;
        return Math.max(
          Math.abs(
            horizontal.left + Number(x) - (paper.left + (50 * 96) / 25.4),
          ),
          Math.abs(vertical.top + Number(y) - (paper.top + (100 * 96) / 25.4)),
        );
      });
    })
    .toBeLessThan(0.6);
  const rect = (await paper.boundingBox())!;
  const px = rect.x + (50 * 96) / 25.4,
    py = rect.y + (100 * 96) / 25.4;
  await page.mouse.move(px, py);
  await expect(page.getByLabel("纸面坐标", { exact: true })).toContainText(
    "50.00, 100.00 mm",
  );
  const prevented = await stage.evaluate(
    (el) =>
      !el.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          ctrlKey: true,
          deltaY: -20,
          clientX: el.getBoundingClientRect().x + 50,
          clientY: el.getBoundingClientRect().y + 50,
        }),
      ),
  );
  expect(prevented).toBe(true);
  await page.screenshot({ path: "test-results/canvas-rulers-zoom.png" });
});

test("H 小手自由平移，Esc 返回选择，输入与元素坐标不受影响", async ({
  page,
}) => {
  await page.goto("/");
  await editText(page, "甲乙丙");
  const saved = await exportDocument(page);
  await page.getByRole("button", { name: /^文本框：/ }).focus();
  await page.keyboard.press("h");
  const hand = page.getByRole("button", { name: "小手工具 (H)", exact: true });
  const stage = page.locator(".canvas-stage"),
    paper = page.locator(".canvas-paper");
  await expect(hand).toHaveAttribute("aria-pressed", "true");
  await expect(stage).toHaveCSS("cursor", "grab");
  const before = (await paper.boundingBox())!;
  const area = (await stage.boundingBox())!;
  const x = area.x + area.width / 2,
    y = area.y + area.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await expect(stage).toHaveCSS("cursor", "grabbing");
  await page.mouse.move(x + 65, y + 45, { steps: 6 });
  await page.mouse.up();
  await expect(stage).toHaveCSS("cursor", "grab");
  const after = (await paper.boundingBox())!;
  expect(after.x - before.x).toBeCloseTo(65, 0);
  expect(after.y - before.y).toBeCloseTo(45, 0);
  expect(after.width).toBe(before.width);
  expect(after.height).toBe(before.height);
  expect(await exportDocument(page)).toEqual(saved);
  await expect
    .poll(async () => {
      return page.evaluate(() => {
        const tick = document.querySelector(
          '.paper-ruler.horizontal [data-mm="0"] line',
        );
        if (!tick) return 1000;
        return Math.abs(
          document
            .querySelector(".paper-ruler.horizontal")!
            .getBoundingClientRect().left +
            Number(tick.getAttribute("x1")) -
            document.querySelector(".canvas-paper")!.getBoundingClientRect()
              .left,
        );
      });
    })
    .toBeLessThan(0.6);
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 15, y + 10);
  const stopped = (await paper.boundingBox())!;
  await page.keyboard.press("Escape");
  await page.mouse.move(x + 40, y + 30);
  await page.mouse.up();
  expect((await paper.boundingBox())!.x).toBeCloseTo(stopped.x, 0);

  await expect(hand).toHaveAttribute("aria-pressed", "false");
  await expect(stage).not.toHaveCSS("cursor", "grab");
  // 回到普通模式后，元素可继续直接编辑，H 是普通输入字符。
  await page.getByRole("button", { name: /^文本框：/ }).click();
  const editor = page.getByRole("textbox", { name: "文本框内容", exact: true });
  await editor.fill("");
  await editor.press("h");
  await expect(editor).toHaveValue("h");
  await expect(hand).toHaveAttribute("aria-pressed", "false");
  await editor.press("Escape");
  await page.getByLabel("文档名称", { exact: true }).fill("");
  await page.getByLabel("文档名称", { exact: true }).press("h");
  await expect(page.getByLabel("文档名称", { exact: true })).toHaveValue("h");
  await expect(hand).toHaveAttribute("aria-pressed", "false");
  // 按钮提供触屏入口；Esc 在右侧输入框获得焦点时也能退出小手。
  await hand.click();
  await expect(hand).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("横向位置", { exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(hand).toHaveAttribute("aria-pressed", "false");
});
