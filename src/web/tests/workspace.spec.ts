import { test, expect } from "@playwright/test";

test("投稿、筛选、双主题、预览入队和删除形成真实流程", async ({
  page,
  request,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/");
  await page.getByLabel("姓名", { exact: true }).fill("浏览器校验");
  await page.getByLabel("诗题").fill("静夜思");
  await page
    .getByLabel("正文")
    .fill("床前明月光，疑是地上霜。\n举头望明月，低头思故乡。");
  await page.getByRole("button", { name: "提交诗词" }).click();
  await expect(page.getByRole("heading", { name: "投稿成功" })).toBeVisible();
  await expect(page.locator(".poem-paper")).toBeVisible();
  await page.getByRole("link", { name: "管理工作区" }).click();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "208px");
  await expect(
    page.locator(".submission-row").filter({ hasText: "浏览器校验" }).first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: /^未写/ }).click();
  await page
    .locator(".submission-row")
    .filter({ hasText: "浏览器校验" })
    .first()
    .getByRole("link", { name: "排版预览" })
    .click();
  await expect(page.getByRole("heading", { name: "排版工作台" })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByLabel("字号", { exact: false }).fill("11");
  await expect(page.getByRole("button", { name: "开始书写" })).toBeEnabled();
  await page.getByRole("button", { name: "开始书写" }).click();
  await expect(page.getByRole("button", { name: "已加入队列" })).toBeVisible();
  await page.getByRole("link", { name: "查看书写队列" }).click();
  await expect(page.locator(".nav-item.selected")).toHaveText("书写队列");
  await expect(page.locator(".job-row").first()).toContainText("静夜思");
  await page.getByRole("button", { name: "切换为夜间主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await page.getByRole("link", { name: "投稿管理", exact: true }).click();
  const row = page
    .locator(".submission-row")
    .filter({ hasText: "浏览器校验" })
    .first();
  await row.getByRole("button", { name: "删除" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(row.getByRole("button", { name: "删除" })).toBeFocused();
  await row.getByRole("button", { name: "删除" }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(row).toHaveCount(0);
  expect(failures).toEqual([]);
  expect((await request.get("/api/admin/login")).status()).toBe(404);
});

test("每页共用侧栏，超宽和短窗口不遮挡列表与操作", async ({
  page,
  request,
}) => {
  await request.post("/api/submit", {
    data: {
      author: "长中文姓名用于检验截断",
      title: "一首用于验证长中文标题与操作区域不重叠的诗词作品".repeat(3),
      body: "空山新雨后，天气晚来秋。",
    },
  });
  for (const [route, label] of [
    ["queue", "书写队列"],
    ["machine", "机器控制"],
    ["calibration", "纸张校准"],
    ["settings", "参数设置"],
  ]) {
    await page.goto(`/admin/${route}`);
    await expect(page.locator(".nav-item.selected")).toHaveText(label);
    await expect(
      page.getByRole("link", { name: "返回投稿", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".task-bar")).toBeVisible();
    expect(
      await page
        .locator(".sidebar")
        .evaluate((element) => element.getBoundingClientRect().width),
    ).toBe(208);
  }
  for (const [width, height] of [
    [1024, 768],
    [1440, 1024],
    [1920, 1024],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto("/admin");
    await expect(page.locator(".submission-row").first()).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const row = await page.locator(".submission-row").first().boundingBox();
    const footer = await page.locator(".task-bar").boundingBox();
    expect(row!.y + row!.height).toBeLessThan(footer!.y);
    await page.screenshot({ path: `test-results/submissions-${width}.png` });
  }
});

test("校准越界阻止保存，手机仍能精确操作", async ({ page }) => {
  await page.goto("/admin/calibration");
  await page.getByLabel("纸张偏移 X", { exact: false }).fill("10");
  await expect(
    page.getByRole("button", { name: "保存纸张位置" }),
  ).toBeDisabled();
  await expect(page.getByText("校准越界：")).toBeVisible();
  await page.getByLabel("纸张偏移 X", { exact: false }).fill("-21");
  await expect(
    page.getByRole("button", { name: "保存纸张位置" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "保存纸张位置" }).click();
  await expect(page.getByText("纸张位置已保存")).toBeVisible();
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await expect(page.getByLabel("纸张偏移 X", { exact: false })).toBeVisible();
  }
});

test("软件参数保存与离线 GRBL 预览都可审阅", async ({ page }) => {
  await page.goto("/admin/settings");
  await page.getByLabel("书写速度", { exact: true }).fill("1200");
  await page.getByRole("button", { name: "保存软件参数" }).click();
  await expect(page.getByText("软件参数已保存")).toBeVisible();
  await page.getByRole("button", { name: "预览写入" }).click();
  await expect(page.getByRole("dialog")).toContainText("$110=4000");
  await page.getByRole("button", { name: "取消", exact: true }).click();
});

test("设备不可用如实显示，不产生成功反馈", async ({ page }) => {
  // Stub only the hardware read; application API/data flows remain real.
  await page.route("**/api/admin/machine", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: "测试设备未连接" }),
    }),
  );
  await page.goto("/admin/machine");
  await page.getByRole("button", { name: "读取设备状态" }).click();
  await expect(page.getByText("测试设备未连接")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "落笔", exact: true }),
  ).toBeDisabled();
});
