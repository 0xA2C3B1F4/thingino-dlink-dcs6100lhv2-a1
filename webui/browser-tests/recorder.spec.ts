import { expect, test } from "@playwright/test";

test("SD formatting requires confirmation and reports queued, completed and uncertain results", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let status = "idle";
  let supported = true;
  let output = "";
  const writes: unknown[] = [];
  await page.route("**/api/v1/storage/sd", async (route) => {
    if (route.request().method() === "POST") {
      writes.push(route.request().postDataJSON());
      status = "queued";
      await route.fulfill({ json: { status, filesystem: "fat32" } });
      return;
    }
    const response = await route.fetch();
    const body = await response.json();
    body.data.format = {
      supported,
      options: supported ? [{ id: "fat32", label: "FAT32", description: "Camera recordings" }] : [],
      status,
      last_output_b64: Buffer.from(output).toString("base64"),
    };
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/storage");
  const erase = page.getByRole("button", { name: "Erase and format as FAT32", exact: true });
  await expect(erase).toBeEnabled();
  page.once("dialog", (dialog) => dialog.dismiss());
  await erase.click();
  expect(writes).toEqual([]);
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("/dev/mmcblk0p1");
    expect(dialog.message()).toContain("cannot be undone");
    await dialog.accept();
  });
  await erase.click();
  await expect(page.locator("main")).toContainText("Formatting queued");
  expect(writes).toEqual([{ action: "format", filesystem: "fat32", confirm: "erase" }]);
  await expect(erase).toBeDisabled();
  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  status = "running";
  await refresh.click();
  await expect(erase).toBeDisabled();
  status = "succeeded";
  output = "formatted-fat32";
  await refresh.click();
  await expect(page.locator("main")).toContainText("formatted-fat32");
  await expect(erase).toBeEnabled();
  status = "failed";
  supported = false;
  output = "Worker completion is unconfirmed. Storage remains paused.";
  await refresh.click();
  await expect(page.locator("main")).toContainText(output);
  await expect(erase).toHaveCount(0);
  expect(writes).toHaveLength(1);
});

test("HA actions follow saved runtime, not the draft enable switch", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let enabled = false;
  let connected = false;
  const actions: string[] = [];
  const settings: Record<string, unknown>[] = [];
  await page.route("**/api/v1/config/ha", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      settings.push(body);
      enabled = body.enabled;
      if (!enabled) connected = false;
      await route.continue();
    } else {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), enabled } });
    }
  });
  await page.route("**/api/v1/runtime/ha", async (route) => {
    await route.fulfill({ json: { enabled, connected, state: !enabled ? "disabled" : connected ? "online" : "backoff", last_connect_unix: null, last_disconnect_unix: null, last_error: null, reconnect_in_ms: null, queue_depth: 0, queue_high_water_mark: 0, published_messages: 0, received_commands: 0, rejected_commands: 0, dropped_messages: 0 } });
  });
  await page.route("**/api/v1/actions/ha", async (route) => {
    actions.push(route.request().postDataJSON().action);
    connected = true;
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/home-assistant");
  const toggle = page.getByLabel("Enable Home Assistant integration", { exact: true });
  const reconnect = page.getByRole("button", { name: "Reconnect", exact: true });
  const publish = page.getByRole("button", { name: "Publish state", exact: true });
  const discovery = page.getByRole("button", { name: "Republish discovery", exact: true });
  const stateInterval = page.getByLabel("State interval (seconds)", { exact: true });
  const discoveryInterval = page.getByLabel("Discovery interval (seconds)", { exact: true });
  const cameraInterval = page.getByLabel("Camera preview interval (seconds)", { exact: true });
  await expect(stateInterval).toHaveAttribute("min", "5");
  await expect(discoveryInterval).toHaveAttribute("min", "60");
  await expect(cameraInterval).toHaveAttribute("min", "5");
  await expect(page.getByLabel("OTA check interval (seconds)", { exact: true })).not.toBeEditable();
  await expect(page.locator("main")).toContainText("It has no effect because OTA release checks are not implemented.");
  await expect(page.locator("main")).toContainText("Enable it and save settings to connect");
  for (const action of [reconnect, publish, discovery]) await expect(action).toBeDisabled();
  await toggle.check();
  await stateInterval.fill("8");
  await discoveryInterval.fill("120");
  await cameraInterval.fill("12");
  await expect(reconnect).toBeDisabled();
  expect(actions).toEqual([]);
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(reconnect).toBeEnabled();
  expect(settings[0]?.state_interval).toBe(8);
  expect(settings[0]?.discovery_interval).toBe(120);
  expect(settings[0]?.camera_interval).toBe(12);
  expect(settings[0]).not.toHaveProperty("ota_check_interval");
  await expect(publish).toBeDisabled();
  await reconnect.click();
  await expect(publish).toBeEnabled();
  await toggle.uncheck();
  await expect(reconnect).toBeEnabled();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  for (const action of [reconnect, publish, discovery]) await expect(action).toBeDisabled();
  expect(actions).toEqual(["reconnect"]);
  for (const key of ["state_interval", "discovery_interval", "camera_interval", "ota_check_interval"]) {
    expect(settings[1]).not.toHaveProperty(key);
  }
  await page.goto("/#/status");
  await expect(page.locator("main")).not.toContainText("Prudynt");
  await expect(page.getByRole("link", { name: "Prudynt", exact: true })).toHaveCount(0);
});

test("Recorder keeps unsaved edits with their owner and saves only the selected stream", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  const durations = [300, 900];
  const posts: unknown[] = [];
  await page.route("**/api/v1/recorder*", async (route) => {
    const channel = Number(new URL(route.request().url()).searchParams.get("channel") ?? "0");
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.video.channel).toBe(channel);
      posts.push(body);
      durations[channel] = body.video.duration;
      await route.fulfill({ json: { status: "accepted", persistent: true } });
      return;
    }
    const video = { autostart: false, cleanup_enabled: false, duration: durations[channel], limit: 15, min_free_mb: 1, check_interval: 60, channel, mount: "/mnt/mmcblk0p1", device_path: `raptor/stream${channel}`, filename: "%Y-%m-%d/%H-%M-%S" };
    await route.fulfill({ json: { ok: true, data: { source: "raptor", persistent: true, video, saved_video: video, matches_saved: true, runtime: null, storage_available: false, free_mb: null, boot_pending: false, cleanup_ok: true, timelapse: null, timelapse_supported: false, mounts: [video.mount] } } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/recorder");
  const stream = page.getByLabel("Recorder stream", { exact: true });
  const duration = page.getByLabel("Clip duration in seconds", { exact: true });
  await expect(duration).toHaveValue("300");
  await duration.fill("600");
  await stream.selectOption("1");
  await expect(stream).toHaveValue("0");
  await expect(duration).toHaveValue("600");
  await expect(page.locator("main")).toContainText("Save or reload this stream before switching");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("main")).toContainText("Saved settings verified");
  await stream.selectOption("1");
  await expect(duration).toHaveValue("900");
  await expect(page.getByLabel("Device path", { exact: true })).toHaveValue("raptor/stream1");
  await expect(page.getByLabel("Device path", { exact: true })).toHaveAttribute("readonly", "");
  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({ video: { channel: 0, duration: 600 } });
  await page.goto("/#/timelapse");
  await expect(page.locator("main")).toContainText("Timelapse is not supported by this Raptor build");
  await expect(page.getByRole("button", { name: "Save settings", exact: true })).toHaveCount(0);
});

test("Services navigation connects HA actions and capture pages without overlapping HA polls", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await page.setViewportSize({ width: 390, height: 844 });
  let release!: () => void;
  const firstRead = new Promise<void>((resolve) => { release = resolve; });
  let reads = 0;
  const actions: string[] = [];
  await page.route("**/api/v1/runtime/ha", async (route) => {
    reads += 1;
    if (reads === 1) await firstRead;
    await route.fulfill({ json: { enabled: true, state: "online", connected: true, last_connect_unix: null, last_disconnect_unix: null, last_error: null, reconnect_in_ms: null, queue_depth: 0, queue_high_water_mark: 0, published_messages: reads, received_commands: 0, rejected_commands: 0, dropped_messages: 0 } });
  });
  await page.route("**/api/v1/actions/ha", async (route) => {
    actions.push(route.request().postDataJSON().action);
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/home-assistant");
  await expect(page.getByLabel("Services page")).toBeVisible();
  await expect(page.getByLabel("Services page").locator("option")).toHaveText(["Home Assistant", "Recorder", "Timelapse"]);
  await expect.poll(() => reads).toBe(1);
  await page.getByLabel("Device name", { exact: true }).fill("Services integration fixture");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("main")).toContainText("Settings saved");
  await expect(page.getByLabel("Device name", { exact: true })).toHaveValue("Services integration fixture");
  await expect(page.getByRole("button", { name: "Reconnect", exact: true })).toBeDisabled();
  expect(reads).toBe(1);
  release();
  await expect.poll(() => reads).toBe(2);
  await expect(page.locator("main")).toContainText("Online");
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect.poll(() => actions).toEqual(["reconnect"]);
  await expect(page.getByRole("button", { name: "Reconnect", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Republish discovery", exact: true }).click();
  await expect.poll(() => actions).toEqual(["reconnect", "republish_discovery"]);
  await expect(page.getByRole("button", { name: "Publish state", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Publish state", exact: true }).click();
  await expect.poll(() => actions).toEqual(["reconnect", "republish_discovery", "publish_state"]);
  await expect(page.getByRole("button", { name: "Publish state", exact: true })).toBeEnabled();
  await page.getByLabel("Services page").selectOption("recorder");
  await expect(page.getByRole("heading", { name: "Recorder", exact: true })).toBeVisible();
  const stopped = reads;
  await page.getByLabel("Services page").selectOption("timelapse");
  await expect(page.getByLabel("Interval in minutes", { exact: true })).toBeVisible();
  await page.waitForTimeout(5_200);
  expect(reads).toBe(stopped);
});
