import { defineConfig } from "@playwright/test";
import path from "node:path";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  globalSetup: "./tests/prepare-runtime.ts",
  use: {
    baseURL: "http://127.0.0.1:8097",
    viewport: { width: 1440, height: 1024 },
    launchOptions: { channel: "chrome", headless: true },
  },
  webServer: {
    command:
      "poetry run python -m pygmalion.commands.app serve --port 8097 --dry-run > .cache/e2e/server.log 2>&1",
    cwd: "../..",
    url: "http://127.0.0.1:8097/api/health",
    reuseExistingServer: true,
    env: { PYGMALION_HOME: path.resolve("../../.cache/e2e"), PYTHONUTF8: "1" },
  },
});
