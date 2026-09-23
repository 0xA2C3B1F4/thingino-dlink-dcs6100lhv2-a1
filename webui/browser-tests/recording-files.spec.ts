import { expect, test } from "@playwright/test";

test("recording folders keep stream-specific links read-only and report incomplete listings", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  const root = "/mnt/mmcblk0p1/raptor/stream1";
  const file = `${root}/2026-09-11/12-00-00.mp4`;
  const entry = (name: string, path: string, is_dir: boolean) => ({ name, path, is_dir, size: is_dir ? "-" : "16", perm: "0640", time: "0", is_link: false, link_target: "", deletable: false });
  await page.route("**/api/v1/files?cd=*", async (route) => {
    const path = new URL(route.request().url()).searchParams.get("cd");
    const entries = path === "/" ? [entry("Sub recordings", root, true)] : path === root ? [entry("2026-09-11", `${root}/2026-09-11`, true)] : [entry("12-00-00.mp4", file, false)];
    await route.fulfill({ json: { directory: path, parent: "/", breadcrumbs: [{ label: "Home", path: "/" }], entries, truncated: path === `${root}/2026-09-11` } });
  });
  await page.route("**/api/v1/storage/sd", async (route) => {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: { ok: true, data: { has_sdcard: true, device: { name: "mmcblk0p1", node: "/dev/mmcblk0p1", vendor: "", model: "", size_bytes: null }, reports: { partitions_b64: "", mounts_b64: "" }, format: { supported: false, options: [], status: "idle", last_output_b64: "" }, filesystems: [{ device: "/dev/mmcblk0p1", mountpoint: "/mnt/mmcblk0p1", filesystem: "vfat", writable: false, total_kib: null, used_kib: null, free_kib: null }], messages: { format_warning: "Raptor writer quiescence is required.", not_present: "No card" }, debug: { detection: "mount-table" } } } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/files");
  await page.getByRole("button", { name: "Sub recordings", exact: true }).click();
  await page.getByRole("button", { name: "2026-09-11", exact: true }).click();
  const play = page.getByRole("link", { name: "Play", exact: true });
  await expect(play).toBeVisible();
  const playUrl = new URL((await play.getAttribute("href"))!, "https://fixture");
  expect(playUrl.searchParams.get("path")).toBe(file);
  expect(playUrl.searchParams.get("play")).toBe("1");
  await expect(page.getByRole("button", { name: "Delete", exact: true })).toHaveCount(0);
  await expect(page.locator("main")).toContainText("folder listing is incomplete");
  await page.goto("/#/storage");
  await expect(page.locator("main")).toContainText("Read only");
  await expect(page.locator("main")).toContainText("Usage unavailable");
  await expect(page.getByRole("button", { name: "Erase and format as FAT32", exact: true })).toHaveCount(0);
});

test("recording pagination replaces each page, continues empty scan pages and explains stale cursors", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  const root = "/mnt/mmcblk0p1/raptor/timelapse";
  const entry = (i: number) => ({ name: `100-${i}.jpg`, path: `${root}/100-${i}.jpg`, is_dir: false, size: "4", perm: "0600", time: "0", is_link: false, link_target: "", deletable: false });
  let stale = false;
  await page.route("**/api/v1/files?cd=*", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const cursor = params.get("cursor");
    if (stale && cursor) { await route.fulfill({ status: 400, json: { error: { code: "invalid_request", message: "Cursor expired" } } }); return; }
    const entries = cursor === "abcd-1" ? [] : cursor === "abcd-2" ? Array.from({ length: 108 }, (_, i) => entry(i + 512)) : Array.from({ length: 512 }, (_, i) => entry(i));
    const next_cursor = cursor === "abcd-2" ? null : cursor === "abcd-1" ? "abcd-2" : "abcd-1";
    await route.fulfill({ json: { directory: root, parent: "/", breadcrumbs: [{ label: "Timelapse", path: root }], entries, truncated: next_cursor !== null, next_cursor } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/files");
  await expect(page.locator("main").getByRole("link", { name: "Preview", exact: true })).toHaveCount(512);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator("main").getByRole("link", { name: "Preview", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator("main").getByRole("link", { name: "Preview", exact: true })).toHaveCount(108);
  await expect(page.getByText("100-619.jpg", { exact: true })).toBeVisible();
  await expect(page.getByText("100-0.jpg", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Next page", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Open folder", exact: true }).click();
  await expect(page.locator("main").getByRole("link", { name: "Preview", exact: true })).toHaveCount(512);
  stale = true;
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator("main")).toContainText("Use Open folder to restart the listing");
});
