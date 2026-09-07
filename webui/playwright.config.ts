import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./browser-tests",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4173",
    colorScheme: "light",
    locale: "en-US",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run build && npm run serve",
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [{
    name: "chromium",
    use: {
      browserName: "chromium",
      ...(process.env.WEBUI_BROWSER_CHANNEL === "chrome" ? { channel: "chrome" as const } : {}),
    },
  }],
});
