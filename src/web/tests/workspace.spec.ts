import { test, expect } from "@playwright/test";

const sample =
  "操作前检查\n\n1. 放置纸张，确认纸面平整。\n2. 抬笔后点动，设置书写原点。\n3. 检查预览，再开始书写。\n\n如遇异常，请中止当前任务。";

test("通用单页：编辑、真实笔画预览、保存打开、演练与中止", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "44px");
  await page.getByLabel("文档名称").fill("设备联调 01");
  await page.getByRole("textbox", { name: "正文", exact: true }).fill(sample);
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled({
    timeout: 20000,
  });
  await expect(page.locator(".single-paper img")).toBeVisible();
  const svg = await page
    .locator(".single-paper img")
    .evaluate(async (img: HTMLImageElement) => (await fetch(img.src)).text());
  expect(svg).toContain("<path");
  expect(svg).toContain("stroke-dasharray");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const exported = await download;
  await exported.saveAs("test-results/document.pygmalion.json");
  await expect(page.locator(".document-save-status")).toHaveText("已保存");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "正文", exact: true }),
  ).toHaveValue("");
  await page
    .locator('input[type="file"]')
    .setInputFiles("test-results/document.pygmalion.json");
  await expect(
    page.getByRole("textbox", { name: "正文", exact: true }),
  ).toHaveValue(sample);
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "开始书写" }).click();
  await expect(
    page.getByRole("textbox", { name: "正文", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".task-bar")).toContainText("演练");
  await page.getByRole("button", { name: "中止", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("缓冲区");
  await page.getByRole("button", { name: "确认中止" }).click();
  await expect(page.locator(".task-bar")).toContainText("已中止");
  await expect(
    page.getByRole("textbox", { name: "正文", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("textbox", { name: "正文", exact: true })
    .fill("  新内容\n\n  保留缩进");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("尚未保存");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "新建", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByRole("textbox", { name: "正文", exact: true }),
  ).toHaveValue("  新内容\n\n  保留缩进");
  expect((await request.get("/api/admin/login")).status()).toBe(404);
  expect(errors).toEqual([]);
});

test("溢出、缺字和预览失败阻止书写，修改立即使旧预览失效", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "正文", exact: true }).fill("甲");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page
    .getByRole("textbox", { name: "正文", exact: true })
    .fill("甲\n".repeat(30));
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await expect(page.getByText("正文超出单页范围，请调整排版")).toBeVisible();
  await expect(page.getByLabel("字号", { exact: true })).toHaveValue("8");
  await page
    .getByRole("textbox", { name: "正文", exact: true })
    .fill("甲\u{10ffff}");
  await expect(page.locator(".inline-preview-status")).toContainText("缺字");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeDisabled();
  await page.route("**/api/workbench/preview", (route) =>
    route.fulfill({ status: 503, json: { error: "测试预览不可用" } }),
  );
  await page
    .getByRole("textbox", { name: "正文", exact: true })
    .fill("恢复路径");
  await expect(page.getByText("预览失败：测试预览不可用")).toBeVisible();
  await page.unroute("**/api/workbench/preview");
  await page.getByRole("button", { name: "重新预览" }).click();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "竖排", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "竖排", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
});

test("双主题、伸缩导航和不同视口保持纸张及固定操作可见", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("文档名称").fill("设备联调 01");
  await page.getByRole("textbox", { name: "正文", exact: true }).fill(sample);
  await expect(page.locator(".single-paper img")).toBeVisible();
  await page.getByRole("button", { name: "展开导航" }).click();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "168px");
  await expect(page.getByRole("link", { name: "书写工作台" })).toBeVisible();
  await expect(page.locator(".sidebar")).not.toContainText("投稿");
  await expect(page.locator(".sidebar")).not.toContainText("校准");
  await page.getByRole("button", { name: "收起导航" }).click();
  for (const night of [false, true]) {
    if (night)
      await page.getByRole("button", { name: "切换为夜间主题" }).click();
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme",
      night ? "abyssus" : "vallum",
    );
    await expect(page.locator("body")).toHaveCSS(
      "background-color",
      night ? "rgb(8, 10, 13)" : "rgb(250, 250, 248)",
    );
    for (const [width, height] of [
      [1440, 1024],
      [1024, 768],
      [1920, 1024],
    ]) {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => document.fonts.ready);
      await expect
        .poll(async () => {
          const paper = await page.locator(".single-paper").boundingBox();
          const footer = await page.locator(".task-bar").boundingBox();
          return paper!.y + paper!.height < footer!.y;
        })
        .toBe(true);
      const paper = await page.locator(".single-paper").boundingBox();
      const footer = await page.locator(".task-bar").boundingBox();
      expect(paper!.y + paper!.height).toBeLessThan(footer!.y);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path:
          "test-results/workbench-" + width + (night ? "-night" : "") + ".png",
      });
      const broken = await page
        .locator(".sidebar img:visible")
        .evaluateAll((images) =>
          images.some((img) => !(img as HTMLImageElement).naturalWidth),
        );
      expect(broken).toBe(false);
    }
  }
});

test("软件参数保存与离线 GRBL 预览可审阅", async ({ page }) => {
  await page.goto("/admin/settings");
  await page.getByLabel("书写速度", { exact: true }).fill("1200");
  await page.getByRole("button", { name: "保存软件参数" }).click();
  await expect(page.getByText("软件参数已保存")).toBeVisible();
  await page.getByRole("button", { name: "预览写入" }).click();
  await expect(page.getByRole("dialog")).toContainText("$110=4000");
  await page.getByRole("button", { name: "取消", exact: true }).click();
});
