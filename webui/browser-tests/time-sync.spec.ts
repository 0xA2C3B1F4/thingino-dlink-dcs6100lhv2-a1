import { expect, test } from "@playwright/test";
import { cameraLocalTime } from "../src/pages/config/network-time";
import { timeFixture } from "../tests/support/fixtures";

for (const outcome of ["success", "sync-failure", "readback-failure"] as const) {
  test(`Time sync preserves drafts and reports ${outcome}`, async ({ page, request }) => {
    await request.post("/__fixture__/reset");
    const state = {
      ...timeFixture,
      source: "raptor",
      timezone_reload_supported: true,
      timezone_applied: true,
    };
    const originalClock = cameraLocalTime(state);
    let synchronized = false;
    let clockReads = 0;
    await page.route("**/api/v1/config/time", route => {
      expect(route.request().method()).toBe("GET");
      clockReads += 1;
      if (synchronized && outcome === "readback-failure") {
        return route.fulfill({ status: 503, json: { error: "service_unavailable" } });
      }
      return route.fulfill({ json: state });
    });
    await page.route("**/api/v1/actions/time/sync", route => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postData()).toBeNull();
      if (outcome === "sync-failure") {
        return route.fulfill({ status: 504, json: { error: "backend_timeout" } });
      }
      synchronized = true;
      state.current_unix_time += 3600;
      return route.fulfill({ json: { status: "ok", message: "Time synchronized" } });
    });
    await page.goto("/");
    await page.getByLabel("Password", { exact: true }).fill("thingino");
    await page.getByRole("button", { name: "Log in", exact: true }).click();
    await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
    await page.goto("/#/time");
    const clock = page.getByLabel("Camera local time", { exact: true });
    await expect(clock).toHaveValue(originalClock);
    await page.getByLabel("Timezone", { exact: true }).fill("Europe/Berlin");
    await page.getByLabel("NTP server 1", { exact: true }).fill("ntp.example.test");
    const sync = page.getByRole("button", { name: "Sync time now", exact: true });
    await sync.click();

    if (outcome === "success") {
      await expect(clock).toHaveValue(cameraLocalTime(state));
      await expect(page.getByText("Time synchronized.", { exact: true })).toBeVisible();
      expect(clockReads).toBe(2);
    } else if (outcome === "readback-failure") {
      await expect(page.getByText("Time synchronized, but the camera clock could not be read back. Reload to check the time.", { exact: true })).toBeVisible();
      await expect(clock).toHaveValue("");
      expect(clockReads).toBe(2);
    } else {
      await expect(page.locator('.message[data-variant="error"]')).toBeVisible();
      await expect(clock).toHaveValue(originalClock);
      expect(clockReads).toBe(1);
    }
    await expect(sync).toBeEnabled();
    await expect(page.getByLabel("Timezone", { exact: true })).toHaveValue("Europe/Berlin");
    await expect(page.getByLabel("NTP server 1", { exact: true })).toHaveValue("ntp.example.test");
  });
}
