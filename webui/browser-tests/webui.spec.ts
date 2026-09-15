import { expect, test, type Page } from "@playwright/test";
test("restored layout keeps camera status outside OSD and bounds small and large screens", async ({ page }) => {
  await login(page);
  for (const width of [320, 390, 600, 768, 1133, 1440, 3840]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/#/preview");
    await expect(page.locator(".live-title .badge")).toHaveText("Live");
    // Read one layout snapshot. A heartbeat can reflow the header between
    // separate boundingBox calls, especially at narrow viewport widths.
    const layout = await page.evaluate(() => {
      const rect = (selector: string) => {
        const element = document.querySelector(selector);
        if (!element) throw new Error(`Missing layout element: ${selector}`);
        const bounds = element.getBoundingClientRect();
        return {
          left: bounds.left,
          right: bounds.right,
          top: bounds.top,
          bottom: bounds.bottom,
          width: bounds.width,
        };
      };
      return {
        frame: rect(".preview-page .preview-frame"),
        badge: rect(".live-title .badge"),
        select: rect('select[aria-label="Preview stream"]'),
        header: rect(".live-header"),
        main: rect("main"),
      };
    });
    expect(layout.badge.bottom).toBeLessThanOrEqual(layout.frame.top);
    await expect(page.locator(".preview-frame .live-title")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    expect(layout.select.left).toBeGreaterThanOrEqual(layout.header.left);
    expect(layout.select.right).toBeLessThanOrEqual(layout.header.right);
    expect(layout.main.width).toBeLessThanOrEqual(1240);
    if (width === 1440 && process.env.WEBUI_QA_DIR) {
      await expect(page.locator(".live-title .badge")).toHaveText("Live");
      await page.screenshot({ path: `${process.env.WEBUI_QA_DIR}/restored-preview.png`, fullPage: true });
    }
    await page.goto("/#/network");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    if (width > 600) await expect(page.locator(".section-nav-group")).not.toHaveCount(0);
  }
});

test("empty sign-in feedback stays invisible until an error exists", async ({ page }) => {
  await page.goto("/");
  const feedback = page.locator(".login-form .message");
  expect((await feedback.boundingBox())?.height ?? 0).toBe(0);
  await page.getByLabel("Password").fill("wrong-fixture-password");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(feedback).toContainText("invalid username or password");
});

async function login(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();
}

async function setScenario(page: Page, kind: string): Promise<void> {
  await page.request.post("/__fixture__/scenario", { data: { kind } });
}

test.beforeEach(async ({ request }) => {
  await request.post("/__fixture__/reset");
});

test("Privacy clears an old action error only after confirmed runtime refresh", async ({ page }) => {
  await login(page);
  const privacy = page.getByRole("button", { name: "Privacy mode", exact: true });
  const message = page.locator(".preview-page > .preview-content > .message");
  await expect(privacy).toBeEnabled();
  await page.route("**/api/v1/actions/control", route => route.fulfill({
    status: 504, contentType: "application/json",
    body: JSON.stringify({ status: "error", error: { code: "backend_timeout", message: "backend timed out" } }),
  }));
  await privacy.click();
  await expect(message).toHaveText("backend timed out");
  await page.unroute("**/api/v1/actions/control");
  await privacy.click();
  await expect(privacy).toHaveAttribute("aria-pressed", "true");
  await expect(message).toHaveText("");
  await page.route("**/api/v1/runtime/heartbeat", route => route.fulfill({
    status: 503, contentType: "application/json",
    body: JSON.stringify({ status: "error", error: { code: "unavailable", message: "runtime refresh failed" } }),
  }));
  await privacy.click();
  await expect(message).toHaveText("runtime refresh failed");
});

test("Motion confirms delayed runtime state promptly in both directions", async ({ page }) => {
  await login(page);
  const motion = page.getByRole("button", { name: "Motion detection", exact: true });
  await expect(motion).toBeEnabled();
  for (const expected of [false, true]) {
    let staleReplies = 3;
    await page.route("**/api/v1/runtime/heartbeat", async route => {
      const response = await route.fetch();
      const body = await response.json();
      if (staleReplies-- > 0) body.motion_enabled = !expected;
      await route.fulfill({ response, json: body });
    });
    await motion.click();
    await expect(motion).toBeDisabled();
    await expect(motion).toHaveAttribute("aria-pressed", String(!expected));
    await expect(motion).toHaveAttribute("aria-pressed", String(expected), { timeout: 2500 });
    await expect(motion).toBeEnabled();
    await page.unroute("**/api/v1/runtime/heartbeat");
  }
});

test("Motion confirmation stops with an error when runtime remains stale", async ({ page }) => {
  await login(page);
  const motion = page.getByRole("button", { name: "Motion detection", exact: true });
  await expect(motion).toBeEnabled();
  let reads = 0;
  await page.route("**/api/v1/runtime/heartbeat", async route => {
    reads++;
    const response = await route.fetch();
    const body = await response.json();
    body.motion_enabled = true;
    await route.fulfill({ response, json: body });
  });
  await motion.click();
  await expect(page.locator(".preview-content > .message")).toContainText(
    "Motion state was not confirmed", { timeout: 6500 },
  );
  await expect(motion).toBeEnabled();
  await expect(motion).toHaveAttribute("aria-pressed", "true");
  expect(reads).toBeLessThanOrEqual(20);
});

test("login, session cookie, logout and auth content types follow the Control contract", async ({ page, request }) => {
  const wrongType = await request.post("/api/v1/auth/login", {
    headers: { "Content-Type": "text/plain" },
    data: '{"username":"root","password":"__SET_LOCALLY__"}',
  });
  expect(wrongType.status()).toBe(415);
  expect(await wrongType.json()).toMatchObject({ status: "error", error: { code: "unsupported_media_type" } });

  await login(page);
  await expect(page.getByText("Default password", { exact: true })).toBeHidden();
  await setScenario(page, "default-password");
  await page.reload();
  await expect.poll(async () => (await page.request.get("/api/v1/auth/session")).json()).toMatchObject({
    authenticated: true,
    username: "root",
    control_api: { name: "Thingino Control", version: 1 },
  });
  await expect(page.getByText("Default password", { exact: true })).toBeVisible();
  const badPasswordType = await page.request.post("/api/v1/auth/password", {
    headers: { "Content-Type": "text/plain" },
    data: '{"password":"__SET_LOCALLY__"}',
  });
  expect(badPasswordType.status()).toBe(415);
  await page.getByRole("banner").getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in to the camera" })).toBeVisible();
  await expect.poll(async () => (await page.request.get("/api/v1/auth/session")).json()).toMatchObject({ authenticated: false, username: null });
});

test("desktop preview, themes and lifecycle", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await expect(page.getByRole("dialog", { name: "Navigation" })).toBeHidden();
  await expect(page.locator(".preview-page .preview-frame img")).toBeVisible();
  await expect(page.getByText("Control now", { exact: true })).toHaveCount(0);
  const controlsBox = await page.locator(".control-card").boundingBox();
  const liveBox = await page.locator(".live-card").boundingBox();
  const previewHeadingBox = await page.locator(".preview-page .page-heading").boundingBox();
  expect(controlsBox).not.toBeNull();
  expect(liveBox).not.toBeNull();
  expect(previewHeadingBox).not.toBeNull();
  expect(controlsBox!.x).toBeLessThan(liveBox!.x);
  expect(Math.abs(controlsBox!.y - previewHeadingBox!.y)).toBeLessThanOrEqual(1);
  await expect(page.locator(".control-card")).toHaveCSS("position", "sticky");
  await expect(page.locator(".control-card")).toHaveCSS("overflow-y", "visible");
  await expect(page.locator(".control-card")).toHaveCSS("max-height", "none");
  const dayNightRow = page.locator(".daynight-row");
  const dayNightCopy = await dayNightRow.locator(":scope > div").first().boundingBox();
  const dayNightGroup = await dayNightRow.getByRole("group", { name: "Day and night mode" }).boundingBox();
  expect(dayNightCopy).not.toBeNull();
  expect(dayNightGroup).not.toBeNull();
  expect(dayNightGroup!.y).toBeGreaterThan(dayNightCopy!.y);
  const microphoneRow = page.locator(".control-row").filter({ hasText: "Microphone" });
  const microphoneLabel = await microphoneRow.getByText("Microphone", { exact: true }).boundingBox();
  const microphoneToggle = await microphoneRow.getByRole("button").boundingBox();
  expect(microphoneLabel).not.toBeNull();
  expect(microphoneToggle).not.toBeNull();
  expect(microphoneToggle!.x).toBeGreaterThan(microphoneLabel!.x);
  expect(Math.abs(microphoneToggle!.y - microphoneLabel!.y)).toBeLessThan(12);
  const microphoneDetail = page.getByText("Capture audio with the active stream", { exact: true });
  await expect(microphoneDetail).toBeHidden();
  await microphoneRow.hover();
  await expect(microphoneDetail).toBeVisible();
  await microphoneRow.getByRole("button").focus();
  await expect(microphoneDetail).toBeVisible();
  await expect(microphoneRow.getByRole("button")).toHaveAttribute("aria-describedby", "preview-control-detail-1");
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: 1 });
  await page.waitForTimeout(2_200);
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: 1 });
  await expect(page.locator(".preview-page .preview-frame img")).toHaveAttribute("src", /^blob:/);
  const snapshot = await page.request.get("/api/v1/actions/snapshot?stream_id=0");
  expect(snapshot.status()).toBe(200);
  expect(snapshot.headers()["content-type"]).toContain("image/jpeg");
  expect(snapshot.headers()["content-disposition"]).toBe('attachment; filename="snapshot-ch0.jpg"');
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByRole("button", { name: "Dark theme", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Network settings" })).toBeVisible();
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_closed: 1 });
  await expect(page.locator(".control-card")).toHaveCount(0);
});

test("every navigation destination renders without a page error or horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const destinations = [
    "preview", "status", "crontab", "onvif-info", "thingino-info", "kernel-log", "streamer-log", "system-log", "kernel-modules", "network-sockets", "os-release", "processes", "overlay", "network", "time", "audio", "access", "webui", "admin", "logging", "daynight", "gpio",
    "imaging", "streams", "osd", "motion-privacy", "sensor", "home-assistant", "recorder", "timelapse", "files", "storage", "network-probe", "reset", "help",
  ];
  for (const destination of destinations) {
    await page.goto(`/#/${destination}`);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.locator("main .message[data-variant=error]")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), destination).toBe(true);
  }
});

test("mobile disclosure navigation is keyboard usable and does not overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await expect(page.getByText("Capture audio with the active stream", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Menu" }).focus();
  await page.keyboard.press("Enter");
  const navigation = page.getByRole("dialog", { name: "Navigation" });
  await expect(navigation).toBeVisible();
  await navigation.getByRole("button", { name: "Close" }).click();
  await expect(navigation).toBeHidden();
  await page.getByRole("button", { name: "Menu" }).click();
  await expect(navigation).toBeVisible();
  const settings = navigation.getByText("Settings", { exact: true });
  await settings.focus();
  await page.keyboard.press("Enter");
  await navigation.getByRole("link", { name: "Network" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Network settings" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByLabel("Hostname").focus();
  expect(await page.getByLabel("Hostname").evaluate((node) => node.matches(":focus-visible"))).toBe(true);
});

test("day and night controls keep the accepted mode selected", async ({ page }) => {
  await login(page);
  const group = page.getByRole("group", { name: "Day and night mode" });
  const automatic = group.getByRole("button", { name: "Auto", exact: true });
  const day = group.getByRole("button", { name: "Day", exact: true });
  const night = group.getByRole("button", { name: "Night", exact: true });
  await expect(automatic).toHaveAttribute("aria-pressed", "true");

  await setScenario(page, "heartbeat_unavailable_once");
  await day.click();
  await expect(day).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".preview-content > .message")).toContainText("backend is unavailable");

  for (const [button, mode] of [[day, "day"], [night, "night"]] as const) {
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(automatic).toHaveAttribute("aria-pressed", "false");
    await expect.poll(async () => (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({
      daynight_enabled: false,
      daynight_mode: mode,
    });
  }

  await automatic.click();
  await expect(automatic).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await page.request.get("/api/v1/config/daynight")).json()).toMatchObject({
    enabled: true,
    force_mode: "",
  });
});
test("form load, validation, save and backend error", async ({ page }) => {
  await login(page);
  await page.goto("/#/network");
  await expect(page.getByLabel("Hostname")).toHaveValue("dcs6100-a1");
  await expect(page.getByText("Ethernet", { exact: true })).toHaveCount(0);
  await expect(page.getByText("USB networking", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Scan Wi-Fi networks" }).click();
  await expect(page.getByText("1 Wi-Fi network found", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Available Wi-Fi networks")).toContainText("Quiet Grid Lab — WPA2 · -47 dBm");
  await page.getByRole("button", { name: "Use selected network" }).click();
  await expect(page.getByLabel("Network name (SSID)")).toHaveValue("Quiet Grid Lab");
  await page.getByLabel("Hostname").fill("");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Check the highlighted fields");
  await page.getByLabel("Hostname").fill("quiet-grid-camera");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Settings saved");
  const stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation).toMatchObject({ method: "POST", path: "/api/v1/config/network" });
  expect(stats.last_mutation.body.interfaces.wlan0.gateway).toBe("192.0.2.1");
  expect(stats.last_mutation.body.interfaces.usb0).toHaveProperty("broadcast");
  expect(stats.last_mutation.body.interfaces.wlan0.ipv6).toBe(false);
  expect(stats.last_mutation.body.wifi_ap.enabled).toBe(false);
  expect(stats.last_mutation.body.wifi).not.toHaveProperty("password");
  await expect(page.getByRole("status")).toContainText("Restart the camera");
  await expect(page.getByRole("status")).toContainText("has not been reconfigured");
  await setScenario(page, "save_error_once");
  await page.getByLabel("Hostname").fill("rejected-camera");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("fixture rejected");
});

test("logging save explains deferred application and keeps the local log setting", async ({ page }) => {
  await login(page);
  await page.goto("/#/logging");
  await expect(page.locator("main")).toContainText("held in memory");
  const local = page.getByLabel("Keep local logs while forwarding", { exact: true });
  for (const enabled of [true, false]) {
    await local.setChecked(enabled);
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Restart the camera");
    await expect(page.getByRole("status")).toContainText("does not restart the logging service");
    const stats = await (await page.request.get("/__fixture__/stats")).json();
    expect(stats.last_mutation).toMatchObject({
      method: "POST", path: "/api/v1/config/rsyslog", body: { file: enabled },
    });
    await page.reload();
    await expect(local).toBeChecked({ checked: enabled });
  }
});

test("image save stops before persistence when the live ISP update fails", async ({ page }) => {
  const writes: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/imaging") {
      writes.push(path);
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({
        status: "error", error: { code: "unavailable", message: "Fixture ISP update failed" },
      }) });
    }
    if (path === "/api/v1/prudynt") writes.push(path);
    await route.continue();
  });
  await login(page);
  await page.goto("/#/imaging");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(save).toBeEnabled();
  await expect(page.getByLabel("Hue", { exact: true })).toBeEnabled();
  await page.getByRole("spinbutton", { name: "Brightness", exact: true }).fill("140");
  await save.click();
  await expect(page.getByText("Fixture ISP update failed", { exact: true })).toBeVisible();
  expect(writes).toEqual(["/api/v1/imaging"]);
  await expect(save).toBeEnabled();
});

test("audio and stream controls enforce enums, ranges and disabled-stream sentinels", async ({ page }) => {
  await login(page);
  await page.goto("/#/audio");
  await expect(page.getByLabel("Microphone codec")).toHaveValue("AAC");
  await expect(page.getByLabel("Microphone codec").locator("option")).toHaveCount(6);
  await page.getByLabel("Microphone volume").fill("121");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Check the highlighted fields");
  await page.getByLabel("Microphone volume").fill("71");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Settings saved");
  let stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation.body.audio).toMatchObject({ mic_vol: 71, buffer_cap_frames: 100, tap_enabled: false });

  await page.goto("/#/streams");
  await expect(page.getByRole("heading", { name: "Main stream · CH0" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sub stream · CH1" })).toBeVisible();
  await expect(page.locator(".stream-card").nth(0).locator(":scope > .form-section-card")).toHaveCount(4);
  await expect(page.locator(".stream-card").nth(1).locator(":scope > .form-section-card")).toHaveCount(4);
  await expect(page.getByText("1920 × 1080 · H.264 · 20 fps", { exact: true })).toBeVisible();
  await expect(page.getByText("640 × 360 · H.264 · Disabled", { exact: true })).toBeVisible();
  await expect(page.locator(".stream-card").nth(1).locator(".stream-card-state")).toHaveText("Disabled");
  await expect(page.getByLabel("Width", { exact: true }).nth(0)).toHaveValue("1920");
  await expect(page.getByLabel("Height", { exact: true }).nth(0)).toHaveValue("1080");
  await expect(page.getByLabel("Width", { exact: true }).nth(1)).toHaveValue("640");
  await expect(page.getByLabel("Height", { exact: true }).nth(1)).toHaveValue("360");
  await expect(page.getByLabel("Active width", { exact: true }).nth(0)).toBeHidden();
  await expect(page.getByLabel("Active width", { exact: true }).nth(1)).toBeHidden();

  await page.setViewportSize({ width: 1000, height: 1000 });
  let mainCard = await page.locator(".stream-card").nth(0).boundingBox();
  let subCard = await page.locator(".stream-card").nth(1).boundingBox();
  expect(mainCard).not.toBeNull();
  expect(subCard).not.toBeNull();
  expect(subCard!.y).toBeGreaterThan(mainCard!.y + 20);

  await page.setViewportSize({ width: 390, height: 844 });
  mainCard = await page.locator(".stream-card").nth(0).boundingBox();
  subCard = await page.locator(".stream-card").nth(1).boundingBox();
  expect(mainCard).not.toBeNull();
  expect(subCard).not.toBeNull();
  expect(subCard!.y).toBeGreaterThan(mainCard!.y + 20);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.getByLabel("Frames per second").nth(1)).toHaveValue("0");
  await expect(page.getByLabel("Buffers").nth(1)).toHaveValue("-1");
  await page.getByLabel("Bitrate mode").nth(0).selectOption("CAPPED_QUALITY");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Settings saved");
  stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation.body.stream1).toMatchObject({ fps: 0, buffers: -1, audio_enabled: false, allow_shared: true });
});

test("D-Link A1 GPIO page keeps fixed hardware read-only", async ({ page }) => {
  await login(page);
  await page.goto("/#/gpio");
  await expect(page.getByLabel("IR-cut GPIOs")).toHaveValue("GPIO49 / GPIO50");
  await expect(page.getByLabel("IR-cut GPIOs")).toHaveAttribute("readonly", "");
  await expect(page.getByLabel("850 nm IR LED GPIO")).toHaveValue("GPIO61");
  await expect(page.getByLabel("850 nm IR LED GPIO")).toHaveAttribute("readonly", "");
  await expect(page.getByLabel("Startup indicator").locator("option")).toHaveText(["Off", "Green", "Red"]);
  const map = page.getByRole("heading", { name: "Hardware I/O map" }).locator("..");
  for (const pin of [18, 49, 50, 52, 54, 57, 59, 60, 61, 63]) await expect(map.getByText(`GPIO${pin}`, { exact: true })).toBeVisible();
  await expect(map).toContainText("No microphone GPIO is listed");
  await page.getByLabel("Startup indicator").selectOption("green");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("Settings saved");
  const stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation).toEqual({ method: "POST", path: "/api/v1/config/gpio", body: { startup_indicator: "green" } });
});

test("OSD and bounded tools use canonical methods", async ({ page }) => {
  await login(page);
  await page.goto("/#/osd");
  await expect(page.getByLabel("Name").nth(0)).toHaveValue("clock");
  await expect(page.getByLabel("Type").nth(1)).toHaveValue("gain");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".message[role=status]")).toContainText("OSD settings saved");
  let stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation).toMatchObject({ method: "POST", path: "/api/v1/prudynt" });
  expect(Object.keys(stats.last_mutation.body.osd.sei.entries)).toEqual(["clock", "gain"]);

  await page.goto("/#/network-sockets");
  await expect(page.getByRole("heading", { name: "Network connections" })).toBeVisible();
  await expect(page.locator("pre.output")).toContainText("fixture netstat");

  await page.goto("/#/crontab");
  await expect(page.getByLabel("Root crontab")).toHaveValue(/Fixture schedule/);
  await page.getByLabel("Root crontab").fill("# browser fixture\n@reboot /bin/true\n");
  await page.getByRole("button", { name: "Save scheduled tasks" }).click();
  await expect(page.getByRole("status")).toContainText("Scheduled tasks saved");
  stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation).toEqual({ method: "POST", path: "/api/v1/config/crontab", body: { content: "# browser fixture\n@reboot /bin/true\n" } });

  await page.goto("/#/network-probe");
  await page.getByLabel("Test type").selectOption("connect");
  await page.getByLabel("Host name").fill("camera.local");
  await page.getByLabel("TCP port").fill("443");
  await page.getByRole("button", { name: "Run test" }).click();
  await expect(page.getByRole("status")).toContainText("completed");
  stats = await (await page.request.get("/__fixture__/stats")).json();
  expect(stats.last_mutation).toMatchObject({ method: "POST", path: "/api/v1/network/probe" });
  expect(stats.last_mutation.body).toContain("action=connect");
  expect(stats.last_mutation.body).toContain("target=camera.local%3A443");

  await page.goto("/#/files");
  await page.getByRole("button", { name: "media", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({
    last_mutation: { method: "POST", path: "/api/v1/files?rm=%2Fmnt%2Fmedia%2Fcamera-notes.txt", body: "" },
  });

  await page.goto("/#/reset");
  await expect(page.getByRole("button", { name: "Reset writable overlay" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Full factory reset" })).toHaveCount(0);
});

test("timezone, OSD and Information surfaces expose the focused responsive controls", async ({ page }) => {
  await login(page);
  await page.goto("/#/time");
  const timezone = page.getByLabel("Timezone", { exact: true });
  await expect(timezone).toHaveValue("Europe/Helsinki");
  await expect(timezone).toHaveAttribute("list", "field-timezone-options");
  await expect(page.locator('#field-timezone-options option[value="Europe/Helsinki"]')).toHaveCount(1);
  await timezone.fill("helsinki");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(timezone).toHaveValue("Europe/Helsinki");
  await expect(page.getByLabel("Camera local time")).toHaveValue(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (?:EET|EEST)$/);
  await expect(page.getByText("Advanced", { exact: true })).toBeVisible();
  await expect(page.locator(".grouped-form > .form-section-card")).toHaveCount(2);

  await page.goto("/#/osd");
  await expect(page.getByLabel("Burn-in overlay")).toBeChecked();
  await expect(page.getByLabel("Include short timezone (%Z)")).toBeVisible();
  await expect(page.getByLabel("Name").nth(0)).toHaveValue("clock");
  await expect(page.getByLabel("Type").nth(1)).toHaveValue("gain");
  await page.getByLabel("Burn-in overlay").uncheck();
  await expect(page.locator("#osd-burn-format")).toBeDisabled();
  await page.getByLabel("Burn-in overlay").check();
  await expect(page.locator("#osd-burn-format")).toBeEnabled();

  await page.goto("/#/status");
  await expect(page.locator(".section-nav")).toBeVisible();
  await expect(page.locator(".section-nav")).toHaveCSS("position", "sticky");
  await expect(page.locator(".section-nav-link.active")).toContainText("System status");
  const primaryNavBox = await page.locator(".desktop-nav").boundingBox();
  const sectionNavBox = await page.locator(".section-nav").boundingBox();
  const sectionContentBox = await page.locator(".section-content").boundingBox();
  expect(Math.abs((sectionNavBox?.y ?? 0) - (sectionContentBox?.y ?? 1))).toBeLessThanOrEqual(1);
  expect((sectionNavBox?.y ?? 0) - ((primaryNavBox?.y ?? 0) + (primaryNavBox?.height ?? 0))).toBeGreaterThanOrEqual(32);
  expect((sectionNavBox?.y ?? 0) - ((primaryNavBox?.y ?? 0) + (primaryNavBox?.height ?? 0))).toBeLessThanOrEqual(40);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("Information page")).toBeVisible();
  await page.getByLabel("Information page").selectOption("network-sockets");
  await expect(page).toHaveURL(/#\/network-sockets$/);
  await expect(page.getByRole("heading", { name: "Network connections" })).toBeVisible();

  for (const [path, label] of [["network", "Settings page"], ["imaging", "Streamer page"], ["home-assistant", "Services page"], ["files", "Tools page"]] as const) {
    await page.goto(`/#/${path}`);
    await expect(page.getByLabel(label)).toBeVisible();
  }
});

test("Streamer preview stays one session-scoped connection across routes and stops on hide/background/exit", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.evaluate(() => { location.hash = "#/imaging"; });
  await expect(page.getByRole("heading", { name: "Image quality" })).toBeVisible();
  const show = page.getByRole("button", { name: "Show live preview" });
  await expect(show).toBeVisible();
  await expect(show).toHaveCount(1);
  await expect(page.getByLabel("Live preview stream")).toBeHidden();
  const before = (await (await page.request.get("/__fixture__/stats")).json()).mjpeg_started as number;
  await show.click();
  await expect(page.getByRole("button", { name: "Hide live preview" })).toBeVisible();
  await expect(page.getByLabel("Live preview stream")).toBeVisible();
  await expect(page.locator(".streamer-preview-frame img")).toBeVisible();
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 1 });
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_active: 1, mjpeg_max_active: 1 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Image quality" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Hide live preview" })).toBeVisible();
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 2, mjpeg_active: 1, mjpeg_max_active: 1 });
  await page.evaluate(() => {
    const image = document.querySelector<HTMLImageElement>(".streamer-preview-frame img");
    if (image) (window as Window & { __streamerPreviewImage?: HTMLImageElement }).__streamerPreviewImage = image;
  });

  for (const [hash, heading] of [["#/streams", "Video streams"], ["#/osd", "On-screen display"], ["#/motion-privacy", "Motion and privacy"]] as const) {
    await page.evaluate((nextHash) => { location.hash = nextHash; }, hash);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    if (hash === "#/streams") {
      await page.getByRole("heading", { name: "Sub stream · CH1" }).click();
      await expect(page.getByLabel("Live preview stream")).toHaveValue("1");
      await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 3 });
    }
    if (hash === "#/osd") {
      await page.getByRole("button", { name: "Save settings" }).click();
      await expect(page.locator(".message[role=status]")).toContainText("OSD settings saved");
      await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 4 });
    }
    await expect.poll(async () => page.evaluate(() => {
      const previous = (window as Window & { __streamerPreviewImage?: HTMLImageElement }).__streamerPreviewImage;
      return previous === document.querySelector(".streamer-preview-frame img");
    })).toBe(true);
  }
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 4 });
  const closedBeforeBackground = (await (await page.request.get("/__fixture__/stats")).json()).mjpeg_closed as number;

  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_closed: closedBeforeBackground + 1 });
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: before + 5 });

  await page.getByRole("button", { name: "Hide live preview" }).click();
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_closed: closedBeforeBackground + 2 });
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_active: 0 });
  await expect(page.getByLabel("Live preview stream")).toBeHidden();
  await page.evaluate(() => { location.hash = "#/sensor"; });
  await expect(page.getByRole("heading", { name: "Sensor data" })).toBeVisible();
  await expect(page.locator(".streamer-preview-host")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { location.hash = "#/imaging"; });
  await expect(page.getByRole("heading", { name: "Image quality" })).toBeVisible();
  const asideTop = await page.locator(".streamer-preview-host").boundingBox();
  const pageTop = await page.locator(".page-host").boundingBox();
  expect(asideTop?.y ?? Infinity).toBeLessThanOrEqual(pageTop?.y ?? -1);
});

test("Preview and Streamer use the existing MJPEG element in fullscreen and fallback modes", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const previewFrame = page.locator(".preview-page .preview-frame");
  const previewImage = previewFrame.locator("img");
  await expect(previewImage).toBeVisible();
  await expect(previewImage).toHaveAttribute("src", /^blob:/);
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_active: 1 });
  const previewStarts = (await (await page.request.get("/__fixture__/stats")).json()).mjpeg_started as number;

  if (await previewFrame.evaluate((frame) => typeof frame.requestFullscreen === "function")) {
    await previewFrame.locator(".preview-fullscreen-action").click();
    await expect.poll(() => previewFrame.evaluate((frame) => document.fullscreenElement === frame)).toBe(true);
    await previewImage.click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
  }

  await previewFrame.evaluate((frame) => Object.defineProperty(frame, "requestFullscreen", { configurable: true, value: undefined }));

  await previewFrame.locator(".preview-fullscreen-action").click();
  await expect(previewFrame).toHaveClass(/fullscreen-fallback/);
  await expect(previewFrame.locator(".preview-fullscreen-action")).toHaveText("Close full screen");
  await page.keyboard.press("Escape");
  await expect(previewFrame).not.toHaveClass(/fullscreen-active/);

  await previewImage.focus();
  await page.keyboard.press("Enter");
  await expect(previewFrame).toHaveClass(/fullscreen-fallback/);
  await previewImage.click();
  await expect(previewFrame).not.toHaveClass(/fullscreen-active/);
  await expect(previewImage).toHaveAttribute("src", /^blob:/);
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: previewStarts });

  await page.goto("/#/imaging");
  await page.getByRole("button", { name: "Show live preview" }).click();
  const streamerFrame = page.locator(".streamer-preview-frame");
  const streamerImage = streamerFrame.locator("img");
  await expect(streamerImage).toBeVisible();
  await streamerFrame.evaluate((frame) => Object.defineProperty(frame, "requestFullscreen", { configurable: true, value: undefined }));
  const streamerStarts = (await (await page.request.get("/__fixture__/stats")).json()).mjpeg_started as number;
  await streamerFrame.locator(".preview-fullscreen-action").click();
  await expect(streamerFrame).toHaveClass(/fullscreen-fallback/);
  await streamerFrame.locator(".preview-fullscreen-action").click();
  await expect(streamerFrame).not.toHaveClass(/fullscreen-active/);
  await expect(streamerImage).toHaveAttribute("src", /^blob:/);
  await expect.poll(async () => (await page.request.get("/__fixture__/stats")).json()).toMatchObject({ mjpeg_started: streamerStarts, mjpeg_active: 1 });
});

test("expired session returns to login", async ({ page }) => {
  await login(page);
  await page.goto("/#/network");
  await expect(page.getByLabel("Hostname")).toHaveValue("dcs6100-a1");
  await setScenario(page, "unauthorized_once");
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to the camera" })).toBeVisible();
  await expect(page.getByText("Your session expired. Sign in again.", { exact: true })).toBeVisible();
});

test("slow API and failed stream expose bounded recovery states", async ({ page }) => {
  const mediaRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/media/v1/mjpeg") mediaRequests.push(request.url());
  });
  await login(page);
  await expect.poll(() => mediaRequests.length).toBe(1);
  await setScenario(page, "stream_error_once");
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByRole("button", { name: "Retry preview" })).toBeVisible();
  expect(mediaRequests).toHaveLength(2);
  expect(mediaRequests[1]).toContain("stream=0&q=51");
  await page.getByRole("button", { name: "Retry preview" }).click();
  await expect(page.locator(".badge")).toHaveText("Live");
  expect(mediaRequests).toHaveLength(3);
  expect(mediaRequests[2]).toContain("stream=0&q=52");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(250);
  expect(mediaRequests).toHaveLength(3);
  await page.getByRole("link", { name: "Settings" }).click();
  await setScenario(page, "slow_once");
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByRole("status")).toContainText(/did not respond in time|Failed to fetch/, { timeout: 12_000 });
});

test("unavailable streams never open a media request", async ({ page }) => {
  const mediaRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/media/v1/mjpeg") mediaRequests.push(request.url());
  });
  await page.route("**/api/v1/runtime/media", async (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ streams: {
      ch0: { available: false, enabled: false, snapshot_url: null },
      ch1: { available: false, enabled: false, snapshot_url: null },
    } }),
  }));
  await login(page);
  await expect(page.getByText("No enabled camera stream is available.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Take snapshot" })).toBeDisabled();
  await expect(page.getByLabel("Preview stream").locator("option:disabled")).toHaveCount(2);
  expect(mediaRequests).toHaveLength(0);
});

test("Prudynt motion settings keep Raptor status fields hidden", async ({ page }) => {
  await login(page);
  await page.goto("/#/motion-privacy");
  await expect(page.getByLabel("Enable motion detection", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("Motion sensitivity", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("Saved motion setting", { exact: true })).toBeHidden();
  await expect(page.getByLabel("Runtime matches saved setting", { exact: true })).toBeHidden();
  const write = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/prudynt"));
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect(Object.keys((await write).postDataJSON()).sort()).toEqual(["motion", "privacy"]);
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
});

test("failed heartbeat clears a confirmed motion event until a fresh observation", async ({ page }) => {
  let failed = false;
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    if (failed) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "error", error: { code: "unavailable", message: "runtime disconnected" } }) });
    } else {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), motion_enabled: true, motion_active: true } });
    }
  });
  await login(page);
  const motion = page.getByRole("button", { name: "Motion detection", exact: true });
  await expect(motion).toHaveText("Active");
  failed = true;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(motion).toHaveText("Unavailable");
  await expect(motion).toHaveAttribute("aria-pressed", "mixed");
  await expect(motion).toBeDisabled();
  failed = false;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(motion).toHaveText("Active");
});

test("slow heartbeat uses one in-flight request across reload and the existing five-second tick", async ({ page }) => {
  await page.clock.install();
  await login(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let calls = 0;
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    calls++;
    await held;
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), motion_enabled: true, motion_active: false } });
  });
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect.poll(() => calls).toBe(1);
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await page.clock.runFor(5_100);
  expect(calls).toBe(1);
  release();
  await expect(page.getByRole("button", { name: "Motion detection", exact: true })).toHaveText("On");
});


test("control confirmation makes a fresh heartbeat request after an older background read", async ({ page }) => {
  await page.clock.install();
  await login(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let captured = false;
  let calls = 0;
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    const call = ++calls;
    const response = await route.fetch();
    const body = await response.json();
    if (call === 1) { captured = true; await held; }
    await route.fulfill({ response, json: body });
  });
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect.poll(() => captured).toBe(true);
  const privacy = page.getByRole("button", { name: "Privacy mode", exact: true });
  const applied = page.waitForResponse((response) => response.url().endsWith("/api/v1/actions/control") && response.request().method() === "POST");
  await privacy.click();
  expect((await applied).ok()).toBe(true);
  release();
  await expect(privacy).toHaveAttribute("aria-pressed", "true", { timeout: 1_500 });
  expect(calls).toBe(2);
});
