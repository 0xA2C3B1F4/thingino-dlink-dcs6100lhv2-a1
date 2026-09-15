import { expect, test } from "@playwright/test";

test("Timelapse saves checked policy and displays restoration conflicts", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let policy = { enabled: false, mount: "/mnt/mmcblk0p1", filepath: "raptor/timelapse", filename: "unix-seconds-sequence.jpg", interval: 1, keep_days: 7, preset_enabled: false, presets: { ircut: false, ir850: false, color: false } };
  const posts: unknown[] = [];
  await page.route("**/api/v1/recorder?domain=timelapse", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body); policy = body.timelapse;
      await route.fulfill({ json: { status: "accepted", persistent: true } }); return;
    }
    await route.fulfill({ json: { ok: true, data: { source: "raptor", domain: "timelapse", persistent: true, available: true, timelapse: policy, saved_timelapse: policy, matches_saved: true, mounts: [policy.mount], runtime: { phase: "error", last_error: "preset_restore_failed", preset_restore: "conflict", successes: 2, last_success: 1700000000, next_due: null, cleanup_blocked: false } } } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/timelapse");
  await expect(page.getByLabel("Folder", { exact: true })).toHaveValue("raptor/timelapse");
  await expect(page.getByLabel("Folder", { exact: true })).toHaveAttribute("readonly", "");
  await expect(page.locator("main")).toContainText("Captures are paused");
  await page.getByLabel("Enable timelapse", { exact: true }).check();
  await page.getByLabel("Interval in minutes", { exact: true }).fill("5");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ timelapse: { enabled: true, interval: 5, filepath: "raptor/timelapse" } });
  await expect(page.locator("main")).toContainText("Saved settings verified");
});

test("Timelapse polls asynchronous failures without replacing a dirty form and stops on navigation", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  const policy = { enabled: false, mount: "/mnt/mmcblk0p1", filepath: "raptor/timelapse", filename: "unix-seconds-sequence.jpg", interval: 1, keep_days: 7, preset_enabled: false, presets: { ircut: false, ir850: false, color: false } };
  let failure: string | null = null;
  let gets = 0;
  await page.route("**/api/v1/recorder?domain=timelapse", async (route) => {
    gets++;
    await route.fulfill({ json: { ok: true, data: { source: "raptor", domain: "timelapse", persistent: true, available: true, timelapse: policy, saved_timelapse: policy, matches_saved: true, mounts: [policy.mount], runtime: { phase: failure ? "error" : "idle", last_error: failure, preset_restore: failure === "preset_restore_failed" ? "conflict" : "not_used", successes: 0, last_success: null, next_due: null, cleanup_blocked: failure === "cleanup_failed" } } } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.clock.install();
  await page.goto("/#/timelapse");
  const interval = page.getByLabel("Interval in minutes", { exact: true });
  await expect(interval).toHaveValue("1");
  await interval.fill("17");
  await page.getByLabel("Enable timelapse", { exact: true }).check();
  failure = "preset_restore_failed";
  await page.clock.fastForward(2100);
  await expect(page.locator("main")).toContainText("Captures are paused");
  await expect(interval).toHaveValue("17");
  await expect(page.getByLabel("Enable timelapse", { exact: true })).toBeChecked();
  failure = "cleanup_failed";
  await page.clock.fastForward(2100);
  await expect(page.locator("main")).toContainText("Could not remove this attempt's file");
  await expect(interval).toHaveValue("17");
  await page.goto("/#/about");
  await expect(interval).toHaveCount(0);
  const stoppedAt = gets;
  await page.clock.fastForward(10000);
  expect(gets).toBe(stoppedAt);
});
