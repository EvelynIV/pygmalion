import { expect, test, type WebSocketRoute } from "@playwright/test";

const machine = {
  port: "COM17",
  baud: 115200,
  state: { state: "Idle", MPos: "0,0,0" },
  firmware: ["[VER:1.1:test]"],
  backup: "test-backup.txt",
};

test("连续换方向全部走同一个 WebSocket，坐标直接更新", async ({ page }) => {
  const moves: { id: number; payload: { action: string; dist: number } }[] = [];
  let peer: WebSocketRoute;
  let connections = 0;
  let reads = 0;
  const httpControls: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/admin\/(jog|machine)$/.test(request.url()))
      httpControls.push(request.url());
  });
  await page.routeWebSocket("**/api/admin/machine/ws", (socket) => {
    connections++;
    peer = socket;
    socket.send(JSON.stringify({ type: "connected" }));
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "inspect") {
        reads++;
        socket.send(
          JSON.stringify({
            ...machine,
            type: "result",
            id: message.id,
            ok: true,
          }),
        );
      } else if (message.type === "jog") moves.push(message);
    });
  });
  await page.goto("/admin/machine");
  await page.getByRole("button", { name: "读取设备状态" }).click();
  await page.getByLabel("单次移动").selectOption("1");
  await page.getByRole("button", { name: "X 正向移动 1 毫米" }).click();
  await page.getByRole("button", { name: "Y 正向移动 1 毫米" }).click();
  await page.getByRole("button", { name: "X 负向移动 1 毫米" }).click();
  await expect.poll(() => moves.length).toBe(3);
  expect(moves.map((move) => move.payload)).toEqual([
    { action: "x", dist: 1 },
    { action: "y", dist: 1 },
    { action: "x", dist: -1 },
  ]);
  await expect(
    page.getByRole("button", { name: "读取设备状态" }),
  ).toBeDisabled();
  for (const move of moves)
    peer!.send(
      JSON.stringify({
        type: "result",
        id: move.id,
        ok: true,
        state: { state: "Idle", MPos: "0,1,0" },
        backup: "test-backup.txt",
      }),
    );
  await expect(
    page.getByText("可连续点击方向，最多等待 3 次点动。"),
  ).toBeVisible();
  await expect(page.locator(".coordinates strong").nth(1)).toHaveText("1");
  peer!.send(
    JSON.stringify({ type: "state", state: { state: "Idle", MPos: "2,3,0" } }),
  );
  await expect(page.locator(".coordinates strong").first()).toHaveText("2");
  expect(connections).toBe(1);
  expect(reads).toBe(1);
  expect(httpControls).toEqual([]);
  await page.screenshot({ path: "test-results/machine-websocket.png" });
});

test("断线取消待确认动作，重新连接不会重发点动", async ({ page }) => {
  let peer: WebSocketRoute;
  let connections = 0;
  let moves = 0;
  await page.routeWebSocket("**/api/admin/machine/ws", (socket) => {
    connections++;
    peer = socket;
    socket.send(JSON.stringify({ type: "connected" }));
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "inspect")
        socket.send(
          JSON.stringify({
            ...machine,
            type: "result",
            id: message.id,
            ok: true,
          }),
        );
      else if (message.type === "jog") moves++;
    });
  });
  await page.goto("/admin/machine");
  await page.getByRole("button", { name: "读取设备状态" }).click();
  await page.getByRole("button", { name: "X 正向移动 5 毫米" }).click();
  await expect.poll(() => moves).toBe(1);
  peer!.close();
  await expect(
    page.getByRole("button", { name: "X 正向移动 5 毫米" }),
  ).toBeDisabled();
  await expect(page.getByText("设备不可用", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "读取设备状态" }).click();
  await expect(
    page.getByRole("button", { name: "X 正向移动 5 毫米" }),
  ).toBeEnabled();
  expect(connections).toBe(2);
  expect(moves).toBe(1);
});

test("扫描前通过 WebSocket 释放控制连接", async ({ page }) => {
  const messages: string[] = [];
  await page.routeWebSocket("**/api/admin/machine/ws", (socket) => {
    socket.send(JSON.stringify({ type: "connected" }));
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      messages.push(message.type);
      socket.send(
        JSON.stringify({
          ...(message.type === "inspect" ? machine : {}),
          type: "result",
          id: message.id,
          ok: true,
        }),
      );
    });
  });
  await page.route("**/api/admin/serial-ports/detect", (route) => {
    expect(messages).toEqual(["inspect", "disconnect"]);
    return route.fulfill({ json: { ok: true, port: "COM17" } });
  });
  await page.goto("/admin/machine");
  await page.getByRole("button", { name: "读取设备状态" }).click();
  await expect(
    page.getByRole("button", { name: "断开连接", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "检测串口" }).click();
  await expect(page.getByText("设备已检测：COM17")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "断开连接", exact: true }),
  ).toBeDisabled();
});
