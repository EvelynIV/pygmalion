import { test, expect, type Page } from "@playwright/test";

async function setupTable(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "添加表格", exact: true }).click();
  for (const [col, text] of [
    [1, "甲"],
    [2, "乙"],
  ] as const) {
    await page
      .getByRole("gridcell", { name: new RegExp(`^单元格 1行${col}列`) })
      .click();
    const editor = page.getByRole("textbox", {
      name: `单元格 1行${col}列内容`,
      exact: true,
    });
    await editor.fill(text);
    await editor.press("Control+Enter");
  }
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  const image = page.getByRole("img", {
    name: "纸面真实笔画预览",
    exact: true,
  });
  await expect(image).toBeVisible();
  const url = (await image.getAttribute("src"))!;
  return { image, url };
}
async function stored(page: Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("pygmalion-document-v2")!),
  );
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("连续输入不清空其他单元格，慢响应完成后才替换笔迹", async ({ page }) => {
  const { image, url } = await setupTable(page);
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByRole("textbox", {
    name: "单元格 1行1列内容",
    exact: true,
  });
  const other = page.getByRole("gridcell", { name: /^单元格 1行2列/ });
  const before = await other.screenshot();
  const gate = deferred();
  const responses = new Map<string, string>();
  await page.route("**/api/workbench/preview", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    responses.set(
      route.request().postDataJSON().elements[0].cells[0].text,
      data.svg,
    );
    await gate.promise;
    await route.fulfill({ response, json: data }).catch(() => {});
  });
  await page.evaluate(() => {
    const view = window as unknown as { removedPreviews: number };
    view.removedPreviews = 0;
    new MutationObserver((changes) => {
      for (const change of changes)
        for (const node of change.removedNodes) {
          if (
            node instanceof HTMLImageElement &&
            node.alt === "纸面真实笔画预览"
          )
            view.removedPreviews++;
        }
    }).observe(document.querySelector(".canvas-paper")!, { childList: true });
  });
  try {
    for (const text of ["甲乙", "甲乙丙", "甲乙丙甲"]) {
      await editor.fill(text);
      await expect(image).toHaveAttribute("src", url);
      expect(
        await image.evaluate(
          (node: HTMLImageElement) => node.complete && node.naturalWidth > 0,
        ),
      ).toBe(true);
      expect(
        (await stored(page)).elements[0].cells
          .slice(0, 2)
          .map((cell: { text: string }) => cell.text),
      ).toEqual([text, "乙"]);
    }
    await expect.poll(() => responses.has("甲乙丙甲")).toBe(true);
    await expect(page.locator(".inline-preview-status")).toContainText(
      "正在更新笔画预览",
    );
    await expect(
      page.getByRole("button", { name: "开始书写", exact: true }),
    ).toBeDisabled();
    expect((await other.screenshot()).equals(before)).toBe(true);
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { removedPreviews: number }).removedPreviews,
      ),
    ).toBe(0);
  } finally {
    gate.resolve();
  }
  await expect(image).not.toHaveAttribute("src", url);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  expect(
    await image.evaluate(async (node: HTMLImageElement) =>
      (await fetch(node.src)).text(),
    ),
  ).toBe(responses.get("甲乙丙甲"));
});

test("失败和重试保留上次笔迹，撤销重做也不会清屏或放行旧校验", async ({
  page,
}) => {
  const { image, url } = await setupTable(page);
  let failure: "http" | "image" | undefined = "http";
  const gate = deferred();
  let retryReceived = false;
  await page.route("**/api/workbench/preview", async (route) => {
    if (failure === "http") {
      await route.fulfill({ status: 503, json: { error: "预览暂时不可用" } });
      return;
    }
    retryReceived = true;
    const response = await route.fetch();
    await gate.promise;
    if (failure === "image") {
      await route
        .fulfill({
          response,
          json: { ...(await response.json()), svg: "invalid-svg" },
        })
        .catch(() => {});
    } else await route.fulfill({ response }).catch(() => {});
  });
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByRole("textbox", {
    name: "单元格 1行1列内容",
    exact: true,
  });
  await editor.fill("甲丙");
  await expect(page.locator(".inline-preview-status")).toContainText(
    "当前显示上次成功预览",
  );
  await expect(image).toHaveAttribute("src", url);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeDisabled();
  await editor.press("Control+z");
  await expect(image).toHaveAttribute("src", url);
  await editor.press("Control+y");
  await expect(editor).toHaveValue("甲丙");
  await expect(image).toHaveAttribute("src", url);
  failure = undefined;
  try {
    await page.getByRole("button", { name: "重新预览", exact: true }).click();
    await expect.poll(() => retryReceived).toBe(true);
    await expect(image).toHaveAttribute("src", url);
    await expect(
      page.getByRole("button", { name: "开始书写", exact: true }),
    ).toBeDisabled();
  } finally {
    gate.resolve();
  }
  await expect(image).not.toHaveAttribute("src", url);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  expect((await stored(page)).elements[0].cells[0].text).toBe("甲丙");
  const recovered = await image.getAttribute("src");
  failure = "image";
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  await editor.fill("甲乙丙");
  await expect(page.locator(".inline-preview-status")).toContainText(
    "预览图片加载失败",
  );
  await expect(image).toHaveAttribute("src", recovered!);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeDisabled();
  failure = undefined;
  await page.getByRole("button", { name: "重新预览", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await expect(image).not.toHaveAttribute("src", recovered!);
});

test("图片解码前保留旧图，过期解码不能覆盖新图，URL及时释放", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const view = window as unknown as {
      holdDecode: boolean;
      decodeWaiters: (() => void)[];
      previewUrls: Set<string>;
    };
    view.holdDecode = false;
    view.decodeWaiters = [];
    view.previewUrls = new Set();
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob) => {
      const url = create(blob);
      if (blob instanceof Blob && blob.type === "image/svg+xml")
        view.previewUrls.add(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      view.previewUrls.delete(url);
      revoke(url);
    };
    const decode = HTMLImageElement.prototype.decode;
    HTMLImageElement.prototype.decode = async function () {
      await decode.call(this);
      if (view.holdDecode && this.src.startsWith("blob:"))
        await new Promise<void>((resolve) => view.decodeWaiters.push(resolve));
    };
  });
  const { image, url } = await setupTable(page);
  await page.evaluate(() => {
    (window as unknown as { holdDecode: boolean }).holdDecode = true;
  });
  await page.getByRole("gridcell", { name: /^单元格 1行1列/ }).click();
  const editor = page.getByRole("textbox", {
    name: "单元格 1行1列内容",
    exact: true,
  });
  await editor.fill("甲乙");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { decodeWaiters: unknown[] }).decodeWaiters
            .length,
      ),
    )
    .toBe(1);
  await expect(image).toHaveAttribute("src", url);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeDisabled();
  await editor.fill("甲丙");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { decodeWaiters: unknown[] }).decodeWaiters
            .length,
      ),
    )
    .toBe(2);
  await page.evaluate(() =>
    (window as unknown as { decodeWaiters: (() => void)[] }).decodeWaiters[1](),
  );
  await expect(image).not.toHaveAttribute("src", url);
  const current = await image.getAttribute("src");
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await page.evaluate(async () => {
    (window as unknown as { decodeWaiters: (() => void)[] }).decodeWaiters[0]();
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
  await expect(image).toHaveAttribute("src", current!);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { previewUrls: Set<string> }).previewUrls.size,
      ),
    )
    .toBe(1);
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByRole("button", { name: "继续新建", exact: true }).click();
  await expect(image).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { previewUrls: Set<string> }).previewUrls.size,
      ),
    )
    .toBe(0);
});

test("布局、打开文件及清空文档不会沿用不兼容的旧图", async ({ page }) => {
  const { image, url } = await setupTable(page);
  let gate = deferred();
  let requests = 0;
  await page.route("**/api/workbench/preview", async (route) => {
    const wait = gate.promise;
    const response = await route.fetch();
    requests++;
    await wait;
    await route.fulfill({ response }).catch(() => {});
  });
  await page.getByLabel("横向位置", { exact: true }).fill("25");
  await expect(image).toHaveCount(0);
  await expect.poll(() => requests).toBeGreaterThan(0);
  await page.keyboard.press("Control+z");
  await expect(image).toHaveCount(0);
  gate.resolve();
  await expect(image).toBeVisible();
  await expect(image).not.toHaveAttribute("src", url);
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  const next = await stored(page);
  next.name = "另一个文档";
  next.elements[0].cells[0].text = "丙";
  gate = deferred();
  await page.getByLabel("文档文件", { exact: true }).setInputFiles({
    name: "next.pygmalion.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(next)),
  });
  await expect(page.getByLabel("文档名称", { exact: true })).toHaveValue(
    "另一个文档",
  );
  await expect(image).toHaveCount(0);
  gate.resolve();
  await expect(image).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始书写", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await expect(image).toHaveCount(0);
  expect((await stored(page)).elements).toEqual([]);
});
