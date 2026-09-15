import { expect, test, type Page } from "@playwright/test";
import { pages, type PageId } from "../src/app/navigation";
import { homeAssistantFixture } from "../tests/support/fixtures";

const unsupported = "This media configuration operation is not implemented by the Raptor adapter";

test("disabled Raptor Day/Night service shows saved policy without offering physical controls", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const posts: unknown[] = [];
  await page.route("**/api/v1/config/daynight", route => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ status: 503, json: { error: "service_unavailable" } });
    }
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, service_enabled: false,
      available: false, mode: "night", state: null, saved_mode: "night", matches_saved: false,
      ir850_at_night: true, saved_ir850_at_night: true, ir850_at_night_matches_saved: true,
      automatic_outputs: {
        supported: true, available: false, color: true, ircut: true,
        color_owner: "disabled", ircut_owner: "disabled", color_state: null,
        ircut_commanded_state: null, saved_values: { color: true, ircut: true }, matches_saved: true,
      },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  await expect(page.getByLabel("Day/night service enabled", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Day/night service enabled", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Day/night mode", { exact: true })).toHaveValue("night");
  await expect(page.getByLabel("Day/night mode", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Saved day/night mode", { exact: true })).toHaveValue("night");
  await expect(page.locator("#field-ir850_at_night")).toBeDisabled();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Day/night service is disabled. These controls are unavailable.", { exact: true })).toBeVisible();
  expect(posts).toHaveLength(0);
});

test("Raptor Streams saves RTSP paths independently and retries an unsaved live path after reload", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const live = ["front-door", "stream1"];
  const saved = [...live];
  const gops = [30, 30];
  let failSave = true;
  let ownerAvailable = true;
  const posts: { path: string; body: unknown }[] = [];
  await page.route(/\/api\/v1\/prudynt\/stream[01]$/, route => {
    const id = Number(route.request().url().slice(-1));
    return route.fulfill({ json: { source: "raptor", persistent: true, stream_id: id,
      supported: true, available: true, gop: gops[id], saved_gop: gops[id], matches_saved: true } });
  });
  await page.route("**/api/v1/prudynt", route => {
    const body = route.request().postDataJSON();
    posts.push({ path: "video", body });
    for (const id of [0, 1]) {
      if (body[`stream${id}`]?.gop !== undefined) gops[id] = body[`stream${id}`].gop;
    }
    return route.fulfill({ json: { status: "accepted", persistent: true } });
  });
  await page.route("**/api/v1/config/access", route => {
    if (!ownerAvailable) return route.fulfill({ status: 503, json: { error: "service_unavailable" } });
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push({ path: "access", body });
      for (const id of [0, 1]) {
        if (body[`rtsp_ch${id}`] !== undefined) live[id] = body[`rtsp_ch${id}`];
        if (!failSave) saved[id] = live[id]!;
      }
      return route.fulfill({ json: { status: "accepted", persistent: !failSave } });
    }
    return route.fulfill({ json: {
      source: "raptor", auth_enabled: true, username: "viewer", password: null,
      password_set: true, rtsp_port: 9554, rtsp_ch0: live[0], rtsp_ch1: live[1], rtsp_mic: "audio",
      onvif_port: null, onvif_enabled: null, onvif_ingress: null,
      saved_rtsp_ch0: saved[0], saved_rtsp_ch1: saved[1],
      rtsp_paths_match_saved: live.every((path, id) => path === saved[id]),
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/streams");
  const mainPath = page.locator("#field-stream0-rtsp_endpoint");
  const subPath = page.locator("#field-stream1-rtsp_endpoint");
  const mainGop = page.locator("#field-stream0-gop");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(mainPath).toHaveValue("front-door");
  await expect(subPath).toHaveValue("stream1");
  await mainGop.fill("60");
  await mainPath.fill("garden");
  await save.click();
  await expect(page.getByText(/RTSP path save is unconfirmed/)).toBeVisible();
  expect(posts).toEqual([
    { path: "video", body: { stream0: { gop: 60 } } },
    { path: "access", body: { rtsp_ch0: "garden" } },
  ]);
  await expect(mainPath).toHaveValue("garden");
  await page.reload();
  await expect(mainPath).toHaveValue("garden");
  await expect(mainGop).toHaveValue("60");
  await expect(page.locator("#field-stream0-rtsp_status")).toHaveValue(/differs from saved/);
  failSave = false;
  await save.click();
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  expect(posts[2]).toEqual({ path: "access", body: { rtsp_ch0: "garden" } });
  await subPath.fill("side-door");
  await save.click();
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  expect(posts[3]).toEqual({ path: "access", body: { rtsp_ch1: "side-door" } });
  await mainGop.fill("45");
  await save.click();
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  expect(posts.slice(4)).toEqual([{ path: "video", body: { stream0: { gop: 45 } } }]);
  await page.reload();
  await expect(mainPath).toHaveValue("garden");
  await expect(subPath).toHaveValue("side-door");
  ownerAvailable = false;
  await page.reload();
  await expect(mainPath).toBeDisabled();
  await expect(subPath).toBeDisabled();
  await expect(mainGop).toBeEnabled();
});

test("Raptor Audio preserves input mute while saving speaker settings", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let muted = true;
  let outputKnown = true;
  let microphoneGainSupported = true;
  const settings: Record<string, number | boolean> = { spk_enabled: false, spk_vol: 20, spk_gain: 5 };
  const posts: Record<string, unknown>[] = [];
  await page.route("**/api/v1/prudynt/audio", route => {
    const levels = Object.fromEntries(["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain"].map(name => {
      const gain = name.endsWith("gain");
      const supported = name !== "mic_gain" || microphoneGainSupported;
      const available = supported && (!name.startsWith("spk_") || (outputKnown && settings.spk_enabled === true));
      const value = available ? (name.startsWith("spk_") ? settings[name] : gain ? 2 : 20) : null;
      return [name, { supported, available, value, min: gain ? 0 : -30, max: name === "mic_alc_gain" ? 7 : gain ? 31 : 120 }];
    }));
    return route.fulfill({ json: {
      source: "raptor", mic_enabled: true, mic_muted: muted,
      spk_enabled: outputKnown ? settings.spk_enabled : null,
      mic_format: "PCM", mic_sample_rate: 8000, input_readback: "owner", processing_readback: "owner",
      mic_is_digital: false, mic_input_basis: "dlink-a1-profile-amic",
      force_stereo: false, channel_basis: "rad-fixed-mono",
      buffer_warn_frames: null, buffer_cap_frames: null,
      buffer_control: "unsupported-prudynt-queue-policy",
      tap_enabled: null, tap_path: null, tap_control: "unsupported",
      codecs_built: { PCM: true, G711A: true, G711U: true, AAC: false, OPUS: false },
      effects_built: false, processing_available: false,
      mic_noise_suppression: null, mic_agc_enabled: null, mic_high_pass_filter: null,
      mic_agc_target_level_dbfs: null, mic_agc_compression_gain_db: null,
      levels, ...Object.fromEntries(Object.entries(levels).map(([name, value]) => [name, value.value])),
    } });
  });
  await page.route("**/api/v1/prudynt", route => {
    const body = route.request().postDataJSON();
    posts.push(body.audio);
    for (const name of ["spk_enabled", "spk_vol", "spk_gain"]) {
      if (body.audio[name] !== undefined) settings[name] = body.audio[name];
    }
    return route.fulfill({ json: { status: "accepted", persistent: true } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/audio");
  const microphone = page.getByLabel("Microphone volume", { exact: true });
  const speaker = page.getByLabel("Enable speaker output", { exact: true });
  const volume = page.getByLabel("Speaker volume", { exact: true });
  await expect(microphone).toBeDisabled();
  await expect(page.getByLabel("Microphone gain", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Microphone ALC gain", { exact: true })).toBeDisabled();
  await expect(speaker).toBeEnabled();
  await expect(volume).toBeDisabled();
  await expect(page.getByLabel("Use digital microphone input", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Use digital microphone input", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Force stereo output", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Force stereo output", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Buffer capacity (frames)", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Write microphone tap for local diagnostics", { exact: true })).toBeDisabled();
  await speaker.check();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts[0]).toMatchObject({ spk_enabled: true });
  await volume.fill("45");
  await page.getByLabel("Speaker gain", { exact: true }).fill("7");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(volume).toHaveValue("45");
  await expect(microphone).toBeDisabled();
  expect(posts).toHaveLength(2);
  for (const body of posts) {
    expect(body).not.toHaveProperty("mic_vol");
    expect(body).not.toHaveProperty("mic_gain");
    expect(body).not.toHaveProperty("mic_alc_gain");
    expect(body).not.toHaveProperty("mic_muted");
    for (const name of ["mic_is_digital", "force_stereo", "buffer_warn_frames", "buffer_cap_frames", "tap_enabled", "tap_path"]) {
      expect(body).not.toHaveProperty(name);
    }
  }
  muted = false;
  microphoneGainSupported = false;
  await page.reload();
  await expect(microphone).toBeEnabled();
  await expect(page.getByLabel("Microphone gain", { exact: true })).toBeDisabled();
  outputKnown = false;
  await page.reload();
  await expect(speaker).toBeDisabled();
  await expect(volume).toBeDisabled();
});

test("Raptor solar form saves coordinates, rejects competing schedules and preserves manual recovery", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let mode = "auto";
  let clockFailed = false;
  let values = { enabled: false, latitude: 0, longitude: 0, sunrise_offset: 0, sunset_offset: 0 };
  let schedule = { enabled: false, start_at: "07:00", stop_at: "19:00" };
  const posts: unknown[] = [];
  await page.route("**/api/v1/config/daynight", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      mode = body.mode;
      if (body.sun !== undefined) values = { ...body.sun };
      if (body.schedule !== undefined) schedule = { ...body.schedule };
      return route.fulfill({ json: { status: "accepted", persistent: true } });
    }
    const active = mode === "auto" && values.enabled;
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, available: true,
      mode, state: "night", saved_mode: mode, matches_saved: true,
      schedule: {
        supported: true, available: true, active: false, application_ok: true,
        target: null, values: schedule, saved_values: schedule, matches_saved: true,
      },
      sun: {
        supported: true, available: true, active,
        application_ok: !active || !clockFailed,
        target: active && !clockFailed ? "night" : null,
        condition: active && !clockFailed ? "polar_night" : null,
        values, saved_values: values, matches_saved: true,
      },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  const solar = page.locator("#field-sun-values-enabled");
  const fixed = page.getByLabel("Use fixed-time schedule", { exact: true });
  const latitude = page.locator("#field-sun-values-latitude");
  const longitude = page.locator("#field-sun-values-longitude");
  const active = page.getByLabel("Sunrise/sunset currently owns automatic mode", { exact: true });
  const confirmed = page.getByLabel("Solar target application confirmed", { exact: true });
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(page.locator("#field-sun-enabled")).not.toBeVisible();
  await expect(page.locator("#field-sun-latitude")).not.toBeVisible();
  await expect(page.locator("#field-sun-longitude")).not.toBeVisible();
  await solar.check();
  await latitude.fill("69.6492");
  await longitude.fill("18.9553");
  await page.getByLabel("Sunrise offset in minutes", { exact: true }).fill("15");
  await page.getByLabel("Sunset offset in minutes", { exact: true }).fill("-20");
  await fixed.check();
  await save.click();
  await expect(page.getByText("Choose either the fixed-time schedule or sunrise/sunset, not both.", { exact: true })).toBeVisible();
  expect(posts).toEqual([]);
  await fixed.uncheck();
  await save.click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([{
    mode: "auto",
    sun: { enabled: true, latitude: 69.6492, longitude: 18.9553, sunrise_offset: 15, sunset_offset: -20 },
  }]);
  await page.reload();
  await expect(latitude).toHaveValue("69.6492");
  await expect(longitude).toHaveValue("18.9553");
  await expect(page.locator("#field-sun-latitude")).not.toBeVisible();
  await expect(page.locator("#field-sun-longitude")).not.toBeVisible();
  await expect(solar).toBeChecked();
  await expect(active).toBeChecked();
  await expect(confirmed).toBeChecked();
  await expect(page.getByLabel("Solar condition", { exact: true })).toHaveValue("polar_night");
  clockFailed = true;
  await page.reload();
  await expect(solar).toBeEnabled();
  await expect(active).toBeChecked();
  await expect(confirmed).not.toBeChecked();
  await page.getByLabel("Day/night mode", { exact: true }).selectOption("night");
  await save.click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(solar).toBeChecked();
  await expect(active).not.toBeChecked();
  await expect(confirmed).toBeChecked();
  expect(posts).toHaveLength(2);
});

test("Raptor Night-mode illumination policy stays separate from legacy IR control", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let policy = true;
  const posts: unknown[] = [];
  await page.route("**/api/v1/config/daynight", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      policy = body.ir850_at_night;
      return route.fulfill({ json: { status: "accepted", persistent: true } });
    }
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, available: true,
      mode: "night", state: "night", saved_mode: "night", matches_saved: true,
      ir850_at_night: policy, saved_ir850_at_night: policy,
      ir850_at_night_matches_saved: true,
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  const field = page.getByLabel("Use 850 nm illumination in Night mode", { exact: true });
  await expect(field).toBeChecked();
  await expect(page.locator("#field-controls-ir850")).toBeDisabled();
  await field.uncheck();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([{ mode: "night", ir850_at_night: false }]);
  await page.reload();
  await expect(field).not.toBeChecked();
  await expect(page.getByLabel("850 nm Night-mode policy matches saved setting", { exact: true })).toBeChecked();
});

test("Raptor schedule form saves overnight policy and remains usable after clock failure", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let mode = "auto";
  let values = { enabled: false, start_at: "07:00", stop_at: "19:00" };
  let clockFailed = false;
  const posts: unknown[] = [];
  await page.route("**/api/v1/config/daynight", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      mode = body.mode;
      if (body.schedule !== undefined) values = { ...body.schedule };
      return route.fulfill({ json: { status: "accepted", persistent: true } });
    }
    const active = mode === "auto" && values.enabled;
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, available: true,
      mode, state: mode === "night" ? "night" : "day", saved_mode: mode, matches_saved: true,
      schedule: {
        supported: true, available: true, active,
        application_ok: !active || !clockFailed,
        target: active && !clockFailed ? "day" : null,
        values, saved_values: values, matches_saved: true,
      },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  const enabled = page.getByLabel("Use fixed-time schedule", { exact: true });
  const start = page.getByLabel("Day starts", { exact: true }).and(page.locator(":visible"));
  const stop = page.getByLabel("Day ends", { exact: true }).and(page.locator(":visible"));
  const applied = page.getByLabel("Scheduled target application confirmed", { exact: true });
  const active = page.getByLabel("Schedule currently owns automatic mode", { exact: true });
  await enabled.check();
  await start.fill("20:00");
  await stop.fill("06:00");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([{ mode: "auto", schedule: { enabled: true, start_at: "20:00", stop_at: "06:00" } }]);
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(start).toHaveValue("20:00");
  await expect(stop).toHaveValue("06:00");
  await expect(active).toBeChecked();
  await expect(applied).toBeChecked();

  clockFailed = true;
  await page.reload();
  await expect(enabled).toBeEnabled();
  await expect(active).toBeChecked();
  await expect(applied).not.toBeChecked();
  await page.getByLabel("Day/night mode", { exact: true }).selectOption("night");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(active).not.toBeChecked();
  await expect(applied).toBeChecked();
  await enabled.uncheck();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(enabled).not.toBeChecked();
  expect(posts).toHaveLength(3);
  expect(posts[2]).toEqual({ mode: "night", schedule: { enabled: false, start_at: "20:00", stop_at: "06:00" } });
});

test("Raptor startup mode saves independently and explicitly retries incomplete persistence", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let configured = "default";
  let saved = "default";
  let failSave = true;
  const posts: unknown[] = [];
  const thresholds = { day_threshold: 100, night_threshold: 200 };
  const timing = { sample_interval_ms: 250, transition_delay_s: 7 };
  const schedule = { enabled: false, start_at: "07:00", stop_at: "19:00" };
  const sun = { enabled: false, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 0, sunset_offset: 0 };
  await page.route("**/api/v1/config/daynight", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      configured = body.initial_mode;
      if (!failSave) saved = configured;
      return route.fulfill({ json: {
        status: failSave ? "error" : "accepted",
        persistent: !failSave,
      } });
    }
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, available: true,
      mode: "auto", state: "day", saved_mode: "auto", matches_saved: true,
      ir850_at_night: true, saved_ir850_at_night: true,
      ir850_at_night_matches_saved: true,
      startup: {
        supported: true, available: true, configured, saved,
        boot_active: "default", matches_saved: configured === saved,
        restart_required: saved !== "default",
      },
      thresholds: {
        supported: true, available: true, trigger: "gain",
        values: thresholds, saved_values: thresholds, matches_saved: true,
      },
      timing: {
        supported: true, available: true, transition_delay_supported: true,
        values: timing, saved_values: timing, matches_saved: true,
      },
      schedule: {
        supported: true, available: true, active: false,
        application_ok: true, target: null,
        values: schedule, saved_values: schedule, matches_saved: true,
      },
      sun: {
        supported: true, available: true, active: false,
        application_ok: true, target: null, condition: null,
        values: sun, saved_values: sun, matches_saved: true,
      },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  const initial = page.getByLabel("Initial mode after startup", { exact: true });
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await initial.selectOption("night");
  await save.click();
  await expect(page.getByText("Day/night saving was not confirmed. Reload before retrying.", { exact: true })).toBeVisible();
  expect(posts).toEqual([{ mode: "auto", initial_mode: "night" }]);

  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(initial).toHaveValue("night");
  await expect(page.getByLabel("Saved startup initial mode", { exact: true })).toHaveValue("default");
  await expect(page.getByLabel("Startup initial mode matches saved setting", { exact: true })).not.toBeChecked();
  failSave = false;
  await save.click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([
    { mode: "auto", initial_mode: "night" },
    { mode: "auto", initial_mode: "night" },
  ]);
  await expect(page.getByLabel("Saved startup initial mode", { exact: true })).toHaveValue("night");
  await expect(page.getByLabel("Startup initial mode matches saved setting", { exact: true })).toBeChecked();
  await expect(page.getByLabel("RIC restart required to apply saved startup mode", { exact: true })).toBeChecked();
});

test("Raptor active ADC thresholds save and reload without changing the detector", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let values = { adc_night: 200, adc_day: 600 };
  let saved = { adc_night: 100, adc_day: 500 };
  let available = true;
  const posts: unknown[] = [];
  await page.route("**/api/v1/config/daynight", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      values = { ...body.thresholds.values };
      saved = { ...values };
      return route.fulfill({ json: { status: "accepted", persistent: true } });
    }
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, available: true,
      mode: "auto", state: "day", saved_mode: "auto", matches_saved: true,
      thresholds: {
        supported: true, available, trigger: "adc",
        values: available ? values : null, saved_values: saved,
        matches_saved: available && values.adc_night === saved.adc_night && values.adc_day === saved.adc_day,
      },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/daynight");
  const night = page.getByLabel("Night ADC threshold", { exact: true });
  const day = page.getByLabel("Day ADC threshold", { exact: true });
  await expect(night).toHaveValue("200");
  await expect(page.getByLabel("Saved night adc threshold", { exact: true })).toHaveValue("100");
  await night.fill("250");
  await day.fill("650");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([{ mode: "auto", thresholds: { trigger: "adc", values: { adc_night: 250, adc_day: 650 } } }]);
  await page.reload();
  await expect(night).toHaveValue("250");
  await expect(day).toHaveValue("650");
  await expect(page.getByLabel("Saved day adc threshold", { exact: true })).toHaveValue("650");
  available = false;
  await page.reload();
  await expect(night).toBeDisabled();
  await expect(day).toBeDisabled();
  await expect(page.getByLabel("Day/night mode", { exact: true })).toBeEnabled();
});

for (const photo of [false, true]) {
  test(`Raptor ${photo ? "Photo" : "gain"} timing saves only supported fields and reloads them`, async ({ page, request }) => {
    await request.post("/__fixture__/reset");
    await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
    let sample = photo ? 100 : 1000;
    let delay = 5;
    let savedSample = photo ? 200 : 500;
    let savedDelay = 4;
    let available = true;
    const posts: unknown[] = [];
    await page.route("**/api/v1/config/daynight", async route => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        posts.push(body);
        sample = savedSample = body.timing.sample_interval_ms;
        if (!photo) delay = savedDelay = body.timing.transition_delay_s;
        return route.fulfill({ json: { status: "accepted", persistent: true } });
      }
      return route.fulfill({ json: {
        source: "raptor", persistent: true, supported: true, available: true,
        mode: "auto", state: "day", saved_mode: "auto", matches_saved: true,
        timing: {
          supported: true, available, transition_delay_supported: !photo,
          values: available ? { sample_interval_ms: sample, transition_delay_s: photo ? null : delay } : null,
          saved_values: { sample_interval_ms: savedSample, ...(!photo ? { transition_delay_s: savedDelay } : {}) },
          matches_saved: available && sample === savedSample && (photo || delay === savedDelay),
        },
      } });
    });
    await page.goto("/");
    await page.getByLabel("Password", { exact: true }).fill("thingino");
    await page.getByRole("button", { name: "Log in", exact: true }).click();
    await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
    await page.goto("/#/daynight");
    const interval = page.getByLabel("Detection sample interval in milliseconds", { exact: true });
    const savedInterval = page.getByLabel("Saved detection sample interval", { exact: true });
    const transition = page.getByRole("spinbutton", { name: "Transition delay in seconds", exact: true });
    await expect(interval).toHaveValue(String(sample));
    await expect(savedInterval).toHaveValue(String(savedSample));
    await interval.fill("250");
    if (photo) {
      await expect(transition).toHaveCount(0);
    } else {
      await transition.fill("7");
    }
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
    expect(posts).toEqual([{
      mode: "auto",
      timing: { sample_interval_ms: 250, ...(!photo ? { transition_delay_s: 7 } : {}) },
    }]);
    await page.reload();
    await expect(interval).toHaveValue("250");
    await expect(savedInterval).toHaveValue("250");
    if (!photo) {
      await expect(transition).toHaveValue("7");
      await expect(page.getByLabel("Saved transition delay", { exact: true })).toHaveValue("7");
    }
    available = false;
    await page.reload();
    await expect(interval).toBeDisabled();
    await expect(savedInterval).toHaveValue("250");
    if (!photo) await expect(transition).toBeDisabled();
    await expect(page.getByLabel("Day/night mode", { exact: true })).toBeEnabled();
  });
}

test("Raptor image form saves fourteen controls and distinguishes acknowledged noise reduction", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const controls = [
    { name: "brightness", label: "Brightness", value: 140 },
    { name: "contrast", label: "Contrast", value: 141 },
    { name: "saturation", label: "Saturation", value: 142 },
    { name: "sharpness", label: "Sharpness", value: 143 },
    { name: "backlight", label: "Backlight compensation", value: 4 },
    { name: "wide_dynamic_range", label: "Dynamic range strength", value: 150 },
    { name: "tone", label: "Highlight tone", value: 20 },
    { name: "defog", label: "Defog strength", value: 130 },
    { name: "noise_reduction", label: "Noise reduction", value: 110 },
    { name: "hue", label: "Hue", value: 135 },
    { name: "dpc_strength", label: "Defective-pixel correction", value: 90 },
    { name: "exposure_compensation", label: "Exposure compensation", value: 145 },
    { name: "hflip", label: "Flip image horizontally", value: 1 },
    { name: "vflip", label: "Flip image vertically", value: 1 },
  ];
  const values: Record<string, number> = Object.fromEntries(controls.map(({ name }) => [name, 0]));
  const posts: Record<string, number>[] = [];
  let noiseAcknowledged = true;
  let hueAvailable = true;
  await page.route("**/api/v1/imaging", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      Object.assign(values, body);
    }
    const fields = Object.fromEntries(controls.map(({ name }) => {
      const noise = name === "noise_reduction";
      const available = (!noise || noiseAcknowledged) && (name !== "hue" || hueAvailable);
      return [name, {
        supported: true, available, min: 0,
        max: name.endsWith("flip") ? 1 : name === "backlight" ? 10 : 255,
        value: available ? values[name] : null,
        verification: noise ? "sdk-setter-config" : "sdk-readback",
        observed_value: noise ? null : values[name], configured_value: values[name],
      }];
    }));
    await route.fulfill({ json: {
      code: 200, result: "success", source: "raptor", persistent: true, message: { fields },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/imaging");
  for (const { name, label, value } of controls) {
    if (name.endsWith("flip")) await page.getByLabel(label, { exact: true }).check();
    else await page.getByLabel(label, { exact: true }).fill(String(value));
  }
  const status = page.getByLabel("Noise reduction status", { exact: true });
  await expect(status).toHaveValue("The ISP setter accepted this configured value; this SDK cannot read the live value back.");
  await expect(page.getByLabel("White-balance mode", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toEqual([Object.fromEntries(controls.map(({ name, value }) => [name, value]))]);
  await page.reload();
  for (const { name, label, value } of controls) {
    if (name.endsWith("flip")) await expect(page.getByLabel(label, { exact: true })).toBeChecked();
    else await expect(page.getByLabel(label, { exact: true })).toHaveValue(String(value));
  }

  noiseAcknowledged = false;
  hueAvailable = false;
  await page.reload();
  await expect(page.getByLabel("Noise reduction", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Hue", { exact: true })).toBeDisabled();
  await expect(status).toHaveValue("The configured value has not been acknowledged by the ISP setter in this runtime.");
  await page.getByLabel("Brightness", { exact: true }).fill("145");
  await page.getByLabel("Flip image horizontally", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).not.toHaveProperty("noise_reduction");
  expect(posts[1]).not.toHaveProperty("hue");
  expect(posts[1]).toHaveProperty("brightness", 145);
  expect(posts[1]).toHaveProperty("hflip", 0);
  expect(posts[1]).toHaveProperty("vflip", 1);
});

test("Raptor White Balance renders Auto, Manual gains and retained preset gains", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let state = { mode: 0, rgain: 300, bgain: 400 };
  const posts: Array<{ white_balance: typeof state }> = [];
  await page.route("**/api/v1/imaging", async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { white_balance: typeof state };
      expect(Object.keys(body)).toEqual(["white_balance"]);
      posts.push(structuredClone(body));
      state = structuredClone(body.white_balance);
    }
    const manual = state.mode === 1;
    await route.fulfill({ json: {
      code: 200, result: "success", source: "raptor", persistent: true,
      message: { fields: {}, white_balance: {
        supported: true, available: true, verification: "sdk-readback",
        modes: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], gain_min: 0, gain_max: 1024,
        mode: state.mode, gains_effective: manual,
        rgain: manual ? state.rgain : null, bgain: manual ? state.bgain : null,
        configured_mode: state.mode, configured_rgain: state.rgain, configured_bgain: state.bgain,
        saved_mode: state.mode, saved_rgain: state.rgain, saved_bgain: state.bgain,
        matches_saved: true,
      } },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/imaging");
  const mode = page.getByLabel("White-balance mode", { exact: true });
  const red = page.getByLabel("White-balance red gain", { exact: true });
  const blue = page.getByLabel("White-balance blue gain", { exact: true });
  await expect(mode).toHaveValue("0");
  await expect(red).toBeDisabled();
  await expect(red).toHaveValue("300");
  await mode.selectOption("1");
  await expect(red).toBeEnabled();
  await expect(blue).toBeEnabled();
  await red.fill("321");
  await blue.fill("421");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(mode).toHaveValue("1");
  await expect(red).toHaveValue("321");
  await expect(blue).toHaveValue("421");
  await mode.selectOption("2");
  await expect(red).toBeDisabled();
  await expect(blue).toBeDisabled();
  await expect(red).toHaveValue("321");
  await expect(blue).toHaveValue("421");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await page.reload();
  await expect(mode).toHaveValue("2");
  await expect(red).toBeDisabled();
  await expect(red).toHaveValue("321");
  expect(posts).toEqual([
    { white_balance: { mode: 1, rgain: 321, bgain: 421 } },
    { white_balance: { mode: 2, rgain: 321, bgain: 421 } },
  ]);
});

test("Raptor H264 profile choices save, reload and disappear for H265", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const sub = page.locator(".stream-card").nth(1);
  const profile = main.getByLabel("Profile", { exact: true });
  await expect(profile).toBeEnabled();
  await expect(profile).toHaveValue("2");
  await profile.selectOption("0");
  await sub.getByLabel("Profile", { exact: true }).selectOption("2");
  const save = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/api/v1/prudynt"));
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await save).postDataJSON()).toEqual({ stream0: { profile: 0 }, stream1: { profile: 2 } });
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(profile).toHaveValue("0");
  await expect(sub.getByLabel("Profile", { exact: true })).toHaveValue("2");
  await main.getByLabel("Codec", { exact: true }).selectOption("H265");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(main.getByLabel("Codec", { exact: true })).toHaveValue("H265");
  await expect(profile).toBeDisabled();
  await expect(sub.getByLabel("Profile", { exact: true })).toBeEnabled();
});

test("Raptor stream form preserves codec, encoding and GOP changes and recovery state", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const state = [0, 1].map(stream_id => ({
    source: "raptor", persistent: true, stream_id,
    supported: true, available: true, gop: 30, saved_gop: 30, matches_saved: true,
    fps_control: { supported: false, available: false },
    codec_control: stream_id === 0 ? {
      supported: true, available: true, recovery_required: false,
      codec: "H264", saved_codec: "H264", matches_saved: true, codecs: ["H264", "H265"],
    } : { supported: false, available: false },
    encoding_control: {
      supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000,
      saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true,
      bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000,
      modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"],
    },
    geometry_control: {
      supported: true, available: true, saved_available: true,
      profile: "dcs6100lhv2-a1-42m-22m-v1",
      active_width: stream_id === 0 ? 1920 : 640,
      active_height: stream_id === 0 ? 1080 : 360,
      saved_width: stream_id === 0 ? 1920 : 640,
      saved_height: stream_id === 0 ? 1080 : 360,
      matches_saved: true, pending_restart: false,
    },
  }));
  const requests: unknown[] = [];
  await page.route(/\/api\/v1\/prudynt\/stream[01]$/, route => {
    const id = Number(route.request().url().slice(-1));
    return route.fulfill({ json: state[id] });
  });
  await page.route("**/api/v1/prudynt", async route => {
    const body = route.request().postDataJSON();
    requests.push(body);
    for (const id of [0, 1]) {
      const fields = body[`stream${id}`];
      if (!fields) continue;
      if (fields.format !== undefined) {
        Object.assign(state[id]!.codec_control, {
          codec: fields.format, saved_codec: fields.format, matches_saved: true,
        });
      }
      if (fields.gop !== undefined) {
        state[id]!.gop = fields.gop;
        state[id]!.saved_gop = fields.gop;
      }
      if (fields.mode !== undefined) {
        Object.assign(state[id]!.encoding_control, {
          rc_mode: fields.mode, saved_rc_mode: fields.mode,
          bitrate: fields.bitrate, saved_bitrate: fields.bitrate,
        });
      }
      if (fields.width !== undefined) {
        Object.assign(state[id]!.geometry_control, {
          saved_width: fields.width, saved_height: fields.height,
          matches_saved: state[id]!.geometry_control.active_width === fields.width
            && state[id]!.geometry_control.active_height === fields.height,
          pending_restart: state[id]!.geometry_control.active_width !== fields.width
            || state[id]!.geometry_control.active_height !== fields.height,
        });
      }
    }
    await route.fulfill({ json: { status: "accepted", persistent: true,
      ...(Object.values(body as Record<string, Record<string, unknown>>).some(fields => fields.width !== undefined) ? { pending_restart: true } : {}) } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const sub = page.locator(".stream-card").nth(1);
  await expect(sub.getByLabel("Codec", { exact: true })).toBeDisabled();
  await main.getByLabel("Codec", { exact: true }).selectOption("H265");
  await main.getByLabel("Bitrate mode", { exact: true }).selectOption("VBR");
  await main.getByLabel("Bitrate", { exact: true }).fill("3000000");
  await main.getByLabel("GOP", { exact: true }).fill("45");
  await sub.getByLabel("GOP", { exact: true }).fill("60");
  await sub.getByLabel("Width", { exact: true }).fill("320");
  await sub.getByLabel("Height", { exact: true }).fill("180");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByRole("status").filter({
    hasText: "Settings saved with checked readback. Pending stream configuration remains inactive until you restart the full camera stack.",
  })).toBeVisible();
  expect(requests).toEqual([
    { stream0: { format: "H265" } },
    { stream0: { mode: "VBR", bitrate: 3_000_000 } },
    { stream0: { gop: 45 }, stream1: { gop: 60 } },
    { stream1: { width: 320, height: 180 } },
  ]);
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save settings", exact: true }) });
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(main.getByLabel("Codec", { exact: true })).toHaveValue("H265");
  await expect(main.getByLabel("Bitrate mode", { exact: true })).toHaveValue("VBR");
  await expect(main.getByLabel("Bitrate", { exact: true })).toHaveValue("3000000");
  await expect(main.getByLabel("GOP", { exact: true })).toHaveValue("45");
  await expect(sub.getByLabel("GOP", { exact: true })).toHaveValue("60");
  await expect(sub.getByLabel("Width", { exact: true })).toHaveValue("320");
  await expect(sub.getByLabel("Active width", { exact: true })).toHaveValue("640");
  await expect(sub.getByLabel("Geometry status", { exact: true })).toHaveValue(/Restart the full camera stack/);
  Object.assign(state[0]!.codec_control, {
    available: false, recovery_required: true, codec: null, matches_saved: false,
  });
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  for (const label of ["Codec", "Bitrate mode", "Bitrate", "GOP", "Width", "Height"]) {
    await expect(main.getByLabel(label, { exact: true })).toBeDisabled();
  }
  await expect(sub.getByLabel("GOP", { exact: true })).toBeEnabled();
});

test("Raptor FIXQP stays pending until a full camera-stack restart", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const mode = main.getByLabel("Bitrate mode", { exact: true });
  const bitrate = main.getByLabel("Bitrate", { exact: true });
  const qp = main.getByLabel("FIXQP initial QP", { exact: true });
  await expect(mode).toHaveValue("CBR");
  await expect(qp).toBeDisabled();
  await mode.selectOption("FIXQP");
  await expect(qp).toBeEnabled();
  await expect(bitrate).toBeDisabled();
  await qp.fill("37");
  const save = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/api/v1/prudynt"));
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await save).postDataJSON()).toEqual({ stream0: { mode: "FIXQP", qp_init: 37 } });
  await expect(page.getByText(/Pending stream configuration remains inactive/, { exact: false })).toBeVisible();
  await expect(main.getByLabel("Rate-control status", { exact: true })).toHaveValue(/Saved FIXQP at QP 37; active CBR/);
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save settings", exact: true }) });
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(mode).toHaveValue("FIXQP");
  await expect(qp).toHaveValue("37");
  await expect(bitrate).toHaveValue("2400000");
  await expect(bitrate).toBeDisabled();
});

test("Raptor GOP mode stays pending while GOP length remains live", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const mode = main.getByLabel("GOP mode", { exact: true });
  const gop = main.getByLabel("GOP", { exact: true });
  await expect(mode).toHaveValue("DEFAULT");
  await mode.selectOption("SMARTP");
  await gop.fill("45");
  const posts: unknown[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/prudynt"))
      posts.push(request.postDataJSON());
  });
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(main.getByLabel("GOP mode status", { exact: true })).toHaveValue(/Saved SMARTP; active DEFAULT/);
  await expect(gop).toHaveValue("45");
  expect(posts).toEqual([{ stream0: { gop_mode: "SMARTP" } }, { stream0: { gop: 45 } }]);
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save settings", exact: true }) });
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(mode).toHaveValue("SMARTP");
  await expect(main.getByLabel("GOP mode status", { exact: true })).toHaveValue(/Saved SMARTP; active DEFAULT/);
  await expect(gop).toHaveValue("45");
});

test("Raptor motion timing and event storage save together and reload checked values", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const state: Record<string, unknown> = {
    source: "raptor", persistent: true, supported: true, available: true,
    enabled: true, saved_enabled: true, matches_saved: true,
    lifecycle_available: true, lifecycle_matches_saved: true,
    debounce_time: 2, cooldown_time: 5, init_time: 5, min_time: 1, post_time: 0,
    event_actions_available: true, video_length: 10, send2storage: false, playonspeaker: false, speaker_repeats: 1,
    saved_video_length: 10, saved_send2storage: false, saved_playonspeaker: false, saved_speaker_repeats: 1, event_actions_match_saved: true,
    storage_action_available: false, speaker_action_available: true, speaker_playback: "idle", event_action_worker_running: true,
    event_storage_owned_channel: null, event_storage_starts: 0,
    event_storage_stops: 0, event_storage_failures: 0, event_action_queue_dropped: 0,
    event_speaker_requests: 0, event_speaker_failures: 0, event_speaker_rate_limited: 0,
  };
  const fields = [
    ["debounce_time", "Debounce samples", 3],
    ["cooldown_time", "Cooldown time (seconds)", 7],
    ["init_time", "Initialization time (seconds)", 4],
    ["min_time", "Minimum event time (seconds)", 2],
    ["post_time", "Post-event time (seconds)", 6],
  ] as const;
  const saved: unknown[] = [];
  await page.route("**/api/v1/prudynt/motion", route => route.fulfill({ json: state }));
  await page.route("**/api/v1/prudynt", async route => {
    const body = route.request().postDataJSON();
    saved.push(body);
    Object.assign(state, body.motion);
    state.saved_video_length = state.video_length;
    state.saved_send2storage = state.send2storage;
    state.saved_playonspeaker = state.playonspeaker;
    state.saved_speaker_repeats = state.speaker_repeats;
    await route.fulfill({ json: { status: "accepted", persistent: true } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("heading", { name: "Preview", exact: true, level: 1 }).waitFor();
  await page.goto("/#/motion-privacy");
  const videoLength = page.getByLabel("Event video length (seconds)", { exact: true });
  const eventStorage = page.getByLabel("Record event video to storage", { exact: true });
  const speaker = page.getByLabel("Play built-in motion alert", { exact: true });
  const repeats = page.getByLabel("Motion alert repeats", { exact: true });
  await expect(videoLength).toBeEnabled();
  await expect(eventStorage).toBeEnabled();
  await expect(speaker).toBeEnabled();
  await expect(repeats).toBeEnabled();
  await expect(page.getByLabel("Event action status", { exact: true })).toHaveValue(/Storage owner is unavailable/);
  await videoLength.fill("20");
  await eventStorage.check();
  await speaker.check();
  await repeats.fill("2");
  for (const [, label, value] of fields) {
    const input = page.getByLabel(label, { exact: true });
    await expect(input).toBeEnabled();
    await input.fill(String(value));
  }
  const applied = page.waitForResponse(response => response.url().endsWith("/api/v1/prudynt") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await applied).status()).toBe(200);
  expect(saved).toEqual([{ motion: { enabled: true, debounce_time: 3, cooldown_time: 7, init_time: 4, min_time: 2, post_time: 6, video_length: 20, send2storage: true, playonspeaker: true, speaker_repeats: 2 } }]);
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save settings", exact: true }) });
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  for (const [, label, value] of fields) await expect(page.getByLabel(label, { exact: true })).toHaveValue(String(value));
  await expect(videoLength).toHaveValue("20");
  await expect(eventStorage).toBeChecked();
  await expect(speaker).toBeChecked();
  await expect(repeats).toHaveValue("2");
  await expect(page.getByLabel("Saved event video length", { exact: true })).toHaveValue("20");
  await expect(page.getByLabel("Event actions match saved settings", { exact: true })).toBeChecked();
  state.lifecycle_available = false;
  state.lifecycle_matches_saved = false;
  for (const [key] of fields) state[key] = null;
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  for (const [, label] of fields) await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
  await expect(eventStorage).toBeEnabled();
  state.event_actions_available = false;
  state.event_actions_match_saved = false;
  state.video_length = null;
  state.send2storage = null;
  state.playonspeaker = null;
  state.speaker_repeats = null;
  await form.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(videoLength).toBeDisabled();
  await expect(eventStorage).toBeDisabled();
  await expect(speaker).toBeDisabled();
});

const pageReads: Record<PageId, [string, string | null, number]> = {
  preview: ["Preview", "/runtime/media", 200], status: ["System status", "/runtime/system", 200],
  usage: ["System usage", "/runtime/system", 200], crontab: ["Scheduled tasks", "/config/crontab", 200],
  "onvif-info": ["ONVIF", "/diagnostics/info?onvif", 200],
  "thingino-info": ["Thingino", "/diagnostics/info?thingino", 200],
  "kernel-log": ["Kernel log", "/diagnostics/info?dmesg", 200],
  "streamer-log": ["Streamer log", "/diagnostics/info?logcat", 200],
  "system-log": ["System log", "/diagnostics/info?logread", 200],
  processes: ["Processes", "/diagnostics/info?top", 200],
  "network-sockets": ["Network connections", "/diagnostics/info?netstat", 200],
  "kernel-modules": ["Kernel modules", "/diagnostics/info?lsmod", 200],
  "os-release": ["OS release", "/diagnostics/info?release", 200],
  overlay: ["Overlay partition", "/storage/overlay", 200], network: ["Network settings", "/config/network", 200],
  time: ["Time and timezone", "/config/time", 200], audio: ["Audio", "/prudynt/audio", 200],
  access: ["Media access", "/config/access", 200], webui: ["Web interface", "/config/webui", 200],
  admin: ["Admin profile", "/config/admin", 200], logging: ["Remote logging", "/config/rsyslog", 200],
  daynight: ["Day and night automation", "/config/daynight", 503], gpio: ["Hardware controls", "/config/gpio", 200],
  imaging: ["Image quality", "/imaging", 200], streams: ["Video streams", "/prudynt/stream0", 200],
  osd: ["On-screen display", "/prudynt/osd", 503], "motion-privacy": ["Motion and privacy", "/prudynt/motion", 200],
  sensor: ["Sensor data", "/runtime/daynight/sensors", 503],
  "home-assistant": ["Home Assistant", "/config/ha", 503],
  recorder: ["Recorder", "/recorder", 503], timelapse: ["Timelapse", "/recorder", 503],
  files: ["Files", "/files?", 200], storage: ["SD storage", "/storage/sd", 200],
  "network-probe": ["Network test", null, 200], reset: ["Restart and reset", null, 200],
  help: ["About this interface", "/health", 200],
};

async function decodedImage(page: Page, stream: 0 | 1): Promise<void> {
  const image = page.locator(".preview-content img");
  await expect(page.getByLabel("Preview stream", { exact: true })).toHaveValue(String(stream));
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth > 0 && node.naturalHeight > 0)).toBe(true);
}

test("Raptor login, navigation, both streams, persistent imaging and missing daemon state", async ({ page, request }) => {
  test.setTimeout(90_000);
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  const mainRequest = page.waitForRequest((request) => request.url().includes("/media/v1/mjpeg?stream=0"));
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await mainRequest;
  await decodedImage(page, 0);
  const subRequest = page.waitForRequest((request) => request.url().includes("/media/v1/mjpeg?stream=1"));
  await page.getByLabel("Preview stream", { exact: true }).selectOption("1");
  await subRequest;
  await decodedImage(page, 1);
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  for (const entry of pages) {
    const [heading, path, status] = pageReads[entry.id];
    const read = path ? page.waitForResponse((response) => response.request().method() === "GET" && response.url().includes(`/api/v1${path}`)) : null;
    await page.goto(`/#/${entry.id}`);
    if (entry.id === "preview") await page.reload();
    await expect(page.getByRole("heading", { name: heading, exact: true, level: 1 })).toBeVisible();
    if (read) {
      const response = await read;
      expect(response.status(), entry.id).toBe(status);
      if (status === 503) {
        expect(await response.json()).toMatchObject({ error: { code: "service_unavailable", message: unsupported } });
        await expect(page.locator("main")).toContainText(unsupported);
      }
    } else {
      await expect(page.getByRole("button", { name: entry.id === "reset" ? "Reboot camera" : "Run test", exact: true })).toBeVisible();
    }
  }
  await page.goto("/#/reset");
  await expect(page.getByRole("button", { name: "Reboot to restart media" })).toBeVisible();
  await expect(page.locator("main")).toContainText("cannot safely reacquire this sensor after a warm stop");
  await page.goto("/#/imaging");
  await expect(page.getByRole("button", { name: "Save settings" })).toBeEnabled();
  await expect(page.getByLabel("Hue", { exact: true })).toBeDisabled();
  await page.getByLabel("Brightness", { exact: true }).fill("140");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator("body")).toContainText("Settings saved.");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-failure" } });
  await page.goto("/#/preview");
  await decodedImage(page, 0);
  await expect(page.getByRole("button", { name: "Motion detection", exact: true })).toBeDisabled();
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.reload();
  await expect(page.getByRole("button", { name: "Motion detection", exact: true })).toBeEnabled();
  await expect(page.locator("body")).not.toContainText("rtsp://127.0.0.1:554");
  expect(failures).toEqual([]);
});

test("Raptor controls require readback and recover after rejected mutations", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  for (const [label, field] of [["Privacy mode", "privacy_enabled"], ["Motion detection", "motion_enabled"]] as const) {
    const button = page.getByRole("button", { name: label, exact: true });
    for (const enabled of (field === "motion_enabled" ? [false, true] : [true, false])) {
      await expect(button).toHaveAttribute("aria-pressed", String(!enabled));
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", String(enabled));
      expect(await (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({ [field]: enabled });
    }
  }
  for (const mode of ["day", "night", "auto"]) {
    const button = page.getByRole("group", { name: "Day and night mode" }).getByRole("button", { name: mode.charAt(0).toUpperCase() + mode.slice(1), exact: true });
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    expect(await (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({ daynight_enabled: mode === "auto", daynight_mode: mode === "auto" ? "unknown" : mode });
  }
  const privacy = page.getByRole("button", { name: "Privacy mode", exact: true });
  await page.route("**/api/v1/actions/control", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "error", error: { code: "service_unavailable", message: "Privacy participant failed" } }) }), { times: 1 });
  await privacy.click();
  await expect(page.locator("main")).toContainText("Privacy participant failed");
  await expect(privacy).toHaveAttribute("aria-pressed", "false");
  await privacy.click();
  await expect(privacy).toHaveAttribute("aria-pressed", "true");
  expect(await (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({ privacy_enabled: true });
});

test("native Control adapter exposes partial state and accepts explicit recovery targets", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the opt-in native Control bridge");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const privacy = page.getByRole("button", { name: "Privacy mode", exact: true });
  await expect(privacy).toHaveAttribute("aria-pressed", "false");
  await page.route("**/api/v1/runtime/heartbeat", (route) => route.fulfill({
    status: 503, contentType: "application/json",
    body: JSON.stringify({ status: "error", error: { code: "service_unavailable", message: "readback connection unavailable" } }),
  }), { times: 1 });
  const failed = page.waitForResponse((response) => response.url().endsWith("/api/v1/actions/control"));
  await privacy.click();
  expect(await (await failed).json()).toMatchObject({ error: { code: "partial_apply" } });
  await expect(privacy).toBeDisabled();
  expect(await (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({ privacy_enabled: null, controls_supported: { privacy: true } });
  const protection = page.getByRole("button", { name: "Retry privacy protection", exact: true });
  await expect(protection).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Turn privacy off", exact: true })).toBeVisible();
  const retry = page.waitForRequest((request) => request.url().endsWith("/api/v1/actions/control"));
  await protection.click();
  expect((await retry).postDataJSON()).toEqual({ privacy: { enabled: true } });
  await expect(privacy).toHaveAttribute("aria-pressed", "true");
  await expect(protection).toBeHidden();
  await privacy.click();
  const explicitOff = page.getByRole("button", { name: "Turn privacy off", exact: true });
  await expect(explicitOff).toBeVisible();
  const offRequest = page.waitForRequest((request) => request.url().endsWith("/api/v1/actions/control"));
  await explicitOff.click();
  expect((await offRequest).postDataJSON()).toEqual({ privacy: { enabled: false } });
  await expect(privacy).toHaveAttribute("aria-pressed", "false");
  const motion = page.getByRole("button", { name: "Motion detection", exact: true });
  await expect(motion).toHaveAttribute("aria-pressed", "true");
  const failedStop = page.waitForResponse((response) => response.url().endsWith("/api/v1/actions/control"));
  await motion.click();
  expect(await (await failedStop).json()).toMatchObject({ error: { code: "partial_apply" } });
  await expect(motion).toBeDisabled();
  const stop = page.getByRole("button", { name: "Retry stopping motion", exact: true });
  await expect(stop).toBeVisible();
  expect(await (await page.request.get("/api/v1/runtime/heartbeat")).json()).toMatchObject({ motion_enabled: null, controls_supported: { motion: true } });
  expect(await (await page.request.get("/api/v1/runtime/motion")).json()).toMatchObject({ available: false, monitoring: false, receiving: true });
  await stop.click();
  await expect(motion).toHaveAttribute("aria-pressed", "false");
  await expect(stop).toBeHidden();
  const motionRestart = page.waitForResponse((response) => response.url().endsWith("/api/v1/actions/control"));
  await motion.click();
  expect((await motionRestart).ok()).toBe(true);
  await expect(motion).toHaveAttribute("aria-pressed", "true");
  for (const [kind, enabled, available, selected] of [
    ["main-only", [true, false], [true, false], 0],
    ["failed-sub", [true, true], [true, false], 0],
    ["missing-main-jpeg", [true, true], [true, true], 1],
    ["missing-sub-jpeg", [true, true], [true, true], 0],
    ["both", [true, true], [true, true], 1],
  ] as const) {
    await request.post("/__fixture__/native-media", { data: { kind } });
    const mediaRead = page.waitForResponse((response) => response.url().endsWith("/api/v1/runtime/media"));
    const mediaRequest = page.waitForRequest((request) => request.url().includes(`/media/v1/mjpeg?stream=${selected}`));
    await page.reload();
    const media = await (await mediaRead).json();
    for (const id of [0, 1]) expect(media.streams[`ch${id}`], kind).toMatchObject({ enabled: enabled[id], available: available[id] });
    // Video availability and JPEG capability are independent observations.
    if (kind === "missing-main-jpeg") expect(media.streams.ch0.snapshot_url).toBeNull();
    if (kind === "missing-sub-jpeg") expect(media.streams.ch1.snapshot_url).toBeNull();
    expect(media.streams.ch0).toMatchObject({ width: 1280, height: 720, fps: 20 });
    if (selected === 1) await page.getByLabel("Preview stream", { exact: true }).selectOption("1");
    await mediaRequest;
    await decodedImage(page, selected);
  }
  expect(errors).toEqual([]);
});


test("native Control adapter verifies audio persistence and explicit retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the opt-in native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/audio");
  const volume = page.getByLabel("Microphone volume", { exact: true });
  await expect(volume).toHaveValue("20");
  await expect(page.getByLabel("Microphone codec", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("Enable microphone input", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("Enable automatic gain control", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Microphone codec", { exact: true }).locator("option")).toHaveText(["G711A", "G711U", "PCM"]);
  await page.getByLabel("Microphone ALC gain", { exact: true }).fill("5");
  await page.getByLabel("Microphone codec", { exact: true }).selectOption("G711A");
  await volume.fill("30");
  const failed = page.waitForResponse((response) => response.url().endsWith("/api/v1/prudynt") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect(await (await failed).json()).toMatchObject({ error: { code: "partial_apply" } });
  await expect(page.getByText(/Audio may have changed/)).toBeVisible();
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(volume).toHaveValue("30");
  const saved = page.waitForResponse((response) => response.url().endsWith("/api/v1/prudynt") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect(await (await saved).json()).toMatchObject({ status: "accepted", persistent: true });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(volume).toHaveValue("30");
  await expect(page.getByLabel("Microphone ALC gain", { exact: true })).toHaveValue("5");
  await expect(page.getByLabel("Microphone codec", { exact: true })).toHaveValue("G711A");
  for (const enabled of [false, true]) {
    await page.getByLabel("Enable microphone input", { exact: true }).setChecked(enabled);
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Enable microphone input", { exact: true })).toBeChecked({ checked: enabled });
    if (enabled) await expect(volume).toBeEnabled();
    else await expect(volume).toBeDisabled();
  }
  const speaker = page.getByLabel("Enable speaker output", { exact: true });
  const speakerVolume = page.getByLabel("Speaker volume", { exact: true });
  for (const enabled of [false, true]) {
    await speaker.setChecked(enabled);
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
    await page.reload();
    await expect(speaker).toBeChecked({ checked: enabled });
    if (enabled) await expect(speakerVolume).toBeEnabled();
    else await expect(speakerVolume).toBeDisabled();
  }
  await speakerVolume.fill("45");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(speakerVolume).toHaveValue("45");
});

test("unavailable Raptor audio settings never report a successful no-op save", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-audio-unavailable" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/audio");
  for (const label of ["Microphone volume", "Microphone gain", "Speaker volume", "Speaker gain"]) await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
  let posts = 0;
  page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/api/v1/prudynt")) posts++; });
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("No editable audio settings are available.", { exact: true })).toBeVisible();
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  expect(posts).toBe(0);
});

test("Raptor access form explicitly retries changed live credentials, port and paths", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-access-retry" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/access");
  await expect(page.getByLabel("RTSP port", { exact: true })).toHaveValue("8554");
  await expect(page.getByLabel("Microphone stream path", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("ONVIF active through same-origin ingress", { exact: true })).toHaveJSProperty("indeterminate", true);
  await page.getByLabel("RTSP viewer username", { exact: true }).fill("new-viewer");
  await page.getByLabel("New RTSP viewer password", { exact: true }).fill("test-password");
  await page.getByLabel("RTSP port", { exact: true }).fill("9554");
  await page.getByLabel("Main stream path", { exact: true }).fill("front-door");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText(/RTSP access may have changed/)).toBeVisible();
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(page.getByLabel("RTSP port", { exact: true })).toHaveValue("9554");
  await expect(page.getByLabel("RTSP viewer username", { exact: true })).toHaveValue("new-viewer");
  // These values also existed before Reload; wait for its request to finish
  // before entering a write-only value that load() deliberately clears.
  await expect(page.getByRole("button", { name: "Save settings", exact: true })).toBeEnabled();
  await page.getByLabel("New RTSP viewer password", { exact: true }).fill("test-password");
  const retry = page.waitForRequest((request) => request.url().endsWith("/api/v1/config/access") && request.method() === "POST");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await retry).postDataJSON()).toEqual({ username: "new-viewer", password: "test-password", rtsp_port: 9554, rtsp_ch0: "front-door", rtsp_ch1: "stream1" });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
});


test("effects build exposes processing settings and preserves them after save", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-audio-effects" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/audio");
  await expect(page.getByLabel("Noise suppression", { exact: true })).toHaveAttribute("max", "4");
  await page.getByLabel("Noise suppression", { exact: true }).fill("4");
  await page.getByLabel("Enable automatic gain control", { exact: true }).check();
  await page.getByLabel("Enable microphone high-pass filter", { exact: true }).check();
  await page.getByLabel("AGC target level", { exact: true }).fill("12");
  await page.getByLabel("AGC compression gain dB", { exact: true }).fill("20");
  const saved = page.waitForRequest((request) => request.url().endsWith("/api/v1/prudynt") && request.method() === "POST");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await saved).postDataJSON()).toMatchObject({ audio: { mic_noise_suppression: 4, mic_agc_enabled: true, mic_high_pass_filter: true, mic_agc_target_level_dbfs: 12, mic_agc_compression_gain_db: 20 } });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(page.getByLabel("Noise suppression", { exact: true })).toHaveValue("4");
  await expect(page.getByLabel("AGC target level", { exact: true })).toHaveValue("12");
  await expect(page.getByLabel("Enable automatic gain control", { exact: true })).toBeChecked();
});


test("native Control adapter verifies imaging persistence and explicit retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/imaging");
  const brightness = page.getByLabel("Brightness", { exact: true });
  await expect(brightness).toHaveValue("128");
  await expect(page.getByLabel("Hue", { exact: true })).toBeDisabled();
  await brightness.fill("140");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("body")).toContainText("Image settings may have changed");
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(brightness).toHaveValue("140");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
});


test("a live-only response cannot report image settings saved after a persistent load", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/imaging");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(save).toBeEnabled();
  await page.route("**/api/v1/imaging", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (route.request().method() === "POST") body.persistent = false;
    await route.fulfill({ response, json: body });
  });
  await save.click();
  await expect(page.getByText("Image settings were applied live, but saving was not confirmed. Reload before retrying.", { exact: true })).toBeVisible();
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
});

test("Raptor Motion shows the Home Assistant-owned MQTT destination and settings link", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.route("**/api/v1/config/ha", route => route.fulfill({
    json: { ...homeAssistantFixture, enabled: true, enable_motion: true },
  }));
  await page.route("**/api/v1/runtime/ha", route => route.fulfill({ json: {
    enabled: true, state: "online", connected: true,
    last_connect_unix: 1_787_480_000, last_disconnect_unix: null, last_error: null,
    reconnect_in_ms: null, queue_depth: 0, queue_high_water_mark: 1,
    published_messages: 3, received_commands: 0, rejected_commands: 0, dropped_messages: 0,
  } }));
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  await expect(page.getByLabel("Publish motion through Home Assistant MQTT", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Home Assistant MQTT status", { exact: true })).toHaveValue(/connected.*retained ON\/OFF/);
  const settings = page.getByRole("link", { name: "Open Home Assistant settings", exact: true });
  await expect(settings).toHaveAttribute("href", "#/home-assistant");
  await settings.click();
  await expect(page.getByRole("heading", { name: "Home Assistant", exact: true, level: 1 })).toBeVisible();
});

test("Raptor Motion saves a write-only bounded webhook destination", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  await expect(page.getByRole("heading", { name: "Motion webhook", exact: true })).toBeVisible();
  const webhookForm = page.locator("form").filter({
    has: page.getByRole("button", { name: "Save webhook", exact: true }),
  });
  await expect(webhookForm.getByLabel("Verified HTTP transport is available", { exact: true })).toBeChecked();
  await page.getByLabel("Webhook URL", { exact: true }).fill("https://alerts.example.test/motion");
  await expect(page.getByLabel("Webhook URL", { exact: true })).toHaveAttribute("id", "motion-webhook-url");
  await expect(page.getByLabel("ntfy topic URL", { exact: true })).toHaveValue("");
  await page.getByLabel("Send Motion events to webhook", { exact: true }).check();
  const saving = page.waitForRequest((candidate) => candidate.url().endsWith("/api/v1/config/motion-webhook") && candidate.method() === "POST");
  await page.getByRole("button", { name: "Save webhook", exact: true }).click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true, url: "https://alerts.example.test/motion" });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Send Motion events to webhook", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Webhook URL is saved", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Webhook URL", { exact: true })).toHaveValue("");
});

test("Raptor Motion saves and clears a write-only ntfy destination", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");

  const endpoint = "https://alerts.example.test/private-topic";
  const token = "browser-fixture-bearer";
  const enabled = page.getByLabel("Send Motion events to ntfy", { exact: true });
  const url = page.getByLabel("ntfy topic URL", { exact: true });
  const bearer = page.getByLabel("Bearer token", { exact: true });
  const save = page.getByRole("button", { name: "Save ntfy", exact: true });

  await expect(page.getByRole("heading", { name: "Motion ntfy", exact: true })).toBeVisible();
  await url.fill(endpoint);
  await bearer.fill(token);
  await enabled.check();
  let saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ntfy") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true, url: endpoint, token });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();

  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(page.getByLabel("ntfy topic URL is saved", { exact: true })).toBeChecked();
  await expect(page.getByLabel("ntfy bearer token is saved", { exact: true })).toBeChecked();
  await expect(url).toHaveValue("");
  await expect(bearer).toHaveValue("");
  await expect(page.locator("body")).not.toContainText(endpoint);
  await expect(page.locator("body")).not.toContainText(token);

  await enabled.uncheck();
  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ntfy") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: false });
  await page.reload();
  await expect(enabled).not.toBeChecked();
  await expect(page.getByLabel("ntfy topic URL is saved", { exact: true })).toBeChecked();

  await enabled.check();
  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ntfy") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true });
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(url).toHaveValue("");
  await expect(bearer).toHaveValue("");

  await page.getByLabel("Clear saved bearer token", { exact: true }).check();
  saving = page.waitForRequest(candidate =>
    candidate.url().endsWith("/api/v1/config/motion-ntfy") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true, clear_token: true });
  await page.reload();
  await expect(page.getByLabel("ntfy topic URL is saved", { exact: true })).toBeChecked();
  await expect(page.getByLabel("ntfy bearer token is saved", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Clear saved bearer token", { exact: true })).not.toBeChecked();
  await expect(page.locator("body")).not.toContainText(endpoint);
  await expect(page.locator("body")).not.toContainText(token);
});

test("Raptor Motion Gotify preserves blank secrets and clears them explicitly", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");

  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save Gotify", exact: true }) });
  const enabled = form.getByLabel("Send Motion events to Gotify", { exact: true });
  const endpoint = form.getByLabel("Gotify message endpoint", { exact: true });
  const token = form.getByLabel("Gotify application token", { exact: true });
  const save = form.getByRole("button", { name: "Save Gotify", exact: true });
  const endpointValue = "https://gotify.example.test/message";
  const tokenValue = "__BROWSER_FIXTURE_GOTIFY__";

  await expect(page.getByRole("heading", { name: "Motion Gotify", exact: true })).toBeVisible();
  await endpoint.fill(endpointValue);
  await token.fill(tokenValue);
  await enabled.check();
  let saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-gotify") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true, endpoint: endpointValue, token: tokenValue });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");

  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(form.getByLabel("Gotify endpoint is saved", { exact: true })).toBeChecked();
  await expect(form.getByLabel("Gotify token is saved", { exact: true })).toBeChecked();
  await expect(endpoint).toHaveValue("");
  await expect(token).toHaveValue("");
  await expect(page.locator("body")).not.toContainText(endpointValue);
  await expect(page.locator("body")).not.toContainText(tokenValue);

  await enabled.uncheck();
  saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-gotify") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: false });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("Gotify endpoint is saved", { exact: true })).toBeChecked();
  await expect(form.getByLabel("Gotify token is saved", { exact: true })).toBeChecked();

  await form.getByLabel("Clear saved endpoint", { exact: true }).check();
  await form.getByLabel("Clear saved token", { exact: true }).check();
  saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-gotify") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: false, clear_endpoint: true, clear_token: true });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("Gotify endpoint is saved", { exact: true })).not.toBeChecked();
  await expect(form.getByLabel("Gotify token is saved", { exact: true })).not.toBeChecked();
});

test("Raptor Motion Telegram preserves blank secrets and clears them explicitly", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");

  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Save Telegram", exact: true }) });
  const enabled = form.getByLabel("Send Motion events to Telegram", { exact: true });
  const token = form.getByLabel("Telegram bot token", { exact: true });
  const chat = form.getByLabel("Telegram chat destination", { exact: true });
  const save = form.getByRole("button", { name: "Save Telegram", exact: true });
  const tokenValue = "123456:__BROWSER_FIXTURE_BOT__";
  const chatValue = "@fixture_camera_alerts";

  await expect(page.getByRole("heading", { name: "Motion Telegram", exact: true })).toBeVisible();
  await token.fill(tokenValue);
  await chat.fill(chatValue);
  await enabled.check();
  let saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-telegram") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: true, bot_token: tokenValue, chat_id: chatValue });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");

  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(form.getByLabel("Telegram bot token is saved", { exact: true })).toBeChecked();
  await expect(form.getByLabel("Telegram chat destination is saved", { exact: true })).toBeChecked();
  await expect(token).toHaveValue("");
  await expect(chat).toHaveValue("");
  await expect(page.locator("body")).not.toContainText(tokenValue);
  await expect(page.locator("body")).not.toContainText(chatValue);

  await enabled.uncheck();
  saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-telegram") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: false });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("Telegram bot token is saved", { exact: true })).toBeChecked();
  await expect(form.getByLabel("Telegram chat destination is saved", { exact: true })).toBeChecked();

  await form.getByLabel("Clear saved bot token", { exact: true }).check();
  await form.getByLabel("Clear saved chat destination", { exact: true }).check();
  saving = page.waitForRequest(candidate => candidate.url().endsWith("/api/v1/config/motion-telegram") && candidate.method() === "POST");
  await save.click();
  expect((await saving).postDataJSON()).toEqual({ enabled: false, clear_bot_token: true, clear_chat_id: true });
  await expect(form.locator("..").locator(':scope > [role="status"]')).toHaveText("Settings saved.");
  await page.reload();
  await expect(form.getByLabel("Telegram bot token is saved", { exact: true })).not.toBeChecked();
  await expect(form.getByLabel("Telegram chat destination is saved", { exact: true })).not.toBeChecked();
});

for (const destination of [
  { name: "Gotify", path: "motion-gotify", matchLabel: "Gotify runtime matches saved setting", save: "Save Gotify" },
  { name: "Telegram", path: "motion-telegram", matchLabel: "Telegram runtime matches saved setting", save: "Save Telegram" },
]) {
  test(`Raptor Motion keeps working when ${destination.name} is mismatched and its save fails`, async ({ page, request }) => {
    await request.post("/__fixture__/reset");
    await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
    await page.route(`**/api/v1/config/${destination.path}`, async route => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ status: "error", error: { code: "partial_apply", message: `Fixture rejected the ${destination.name} destination save.` } }),
        });
        return;
      }
      const response = await route.fetch();
      const json = await response.json();
      await route.fulfill({ response, json: { ...json, saved_enabled: true, enabled: false, matches_saved: false } });
    });
    await page.goto("/");
    await page.getByLabel("Password", { exact: true }).fill("thingino");
    await page.getByRole("button", { name: "Log in", exact: true }).click();
    await page.goto("/#/motion-privacy");

    const form = page.locator("form").filter({ has: page.getByRole("button", { name: destination.save, exact: true }) });
    await expect(form.getByLabel(destination.matchLabel, { exact: true })).not.toBeChecked();
    const failedSave = page.waitForResponse(response => response.url().endsWith(`/api/v1/config/${destination.path}`) &&
      response.request().method() === "POST");
    await form.getByRole("button", { name: destination.save, exact: true }).click();
    expect((await failedSave).status()).toBe(503);
    const destinationStatus = form.locator("..").locator(':scope > [role="status"]');
    await expect(destinationStatus).toHaveText(`Fixture rejected the ${destination.name} destination save.`);
    await expect(destinationStatus).not.toHaveText("Settings saved.");

    const motionForm = page.locator("form").filter({ has: page.getByRole("button", { name: "Save settings", exact: true }) });
    const motionEnabled = motionForm.getByLabel("Enable motion detection", { exact: true });
    await expect(motionEnabled).toBeChecked();
    await motionEnabled.uncheck();
    await motionForm.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(motionForm.locator("..").locator(':scope > [role="status"]')).toHaveText(
      "Motion is saved as off. Region edits were not saved. Turn motion on to save a region.",
    );
    await expect(motionEnabled).not.toBeChecked();
    await expect(page.getByRole("heading", { name: "Motion ntfy", exact: true })).toBeVisible();
    await expect(page.getByLabel("Send Motion events to ntfy", { exact: true })).toBeEnabled();
  });
}


test("native Control adapter verifies motion persistence and saved versus live state", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  const enabled = page.getByLabel("Enable motion detection", { exact: true });
  await expect(enabled).toBeEnabled();
  await expect(page.getByLabel("Motion sensitivity", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("ROI left", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Mask every enabled camera stream", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Saved motion setting", { exact: true })).not.toBeChecked();
  await enabled.check();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("body")).toContainText("Motion may have changed");
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(page.getByLabel("Saved motion setting", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Runtime matches saved setting", { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Saved motion setting", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Runtime matches saved setting", { exact: true })).toBeChecked();
  const sensitivity = page.getByLabel("Motion sensitivity (0–4)", { exact: true });
  await expect(sensitivity).toBeEnabled();
  await expect(sensitivity).toHaveValue("3");
  await sensitivity.fill("4");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.locator("body")).toContainText("Motion may have changed");
  await page.reload();
  await expect(sensitivity).toHaveValue("4");
  await expect(page.getByLabel("Saved motion sensitivity", { exact: true })).toHaveValue("3");
  await expect(page.getByLabel("Sensitivity matches saved setting", { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Saved motion sensitivity", { exact: true })).toHaveValue("4");
  await expect(page.getByLabel("Sensitivity matches saved setting", { exact: true })).toBeChecked();
});


test("native Control adapter verifies OSD persistence and explicit retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/osd");
  const fill = page.locator("#osd-fill");
  await expect(page.locator("#osd-burn-enabled")).toBeDisabled();
  await expect(page.locator("#osd-fill-alpha")).toBeDisabled();
  await expect(fill).toHaveValue("#ffffffff");
  await fill.fill("#ff0000ff");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await save.click();
  await expect(page.locator("body")).toContainText("OSD may have changed");
  await expect(page.getByText("OSD settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(fill).toHaveValue("#ff0000ff");
  await expect(page.locator("body")).toContainText("Current text settings differ from the saved settings.");
  await save.click();
  await expect(page.getByText("OSD settings saved.", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Current text settings differ from the saved settings.");
});


test("Raptor OSD rejects persistence downgrade and unavailable editing", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let available = true;
  let posts = 0;
  await page.route("**/api/v1/prudynt/osd", (route) => route.fulfill({ json: {
    source: "raptor", persistent: true, enabled: true, available, matches_saved: true,
    fields: { format: true, fill_color: true, outline_color: true },
    format: "%H:%M:%S", fill_color: "#ffffffff", outline_color: "#000000ff",
  } }));
  await page.route("**/api/v1/prudynt", (route) => {
    posts++;
    return route.fulfill({ json: { status: "accepted", persistent: false } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/osd");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText("Hardware OSD saving was not confirmed. Reload and explicitly retry saving.", { exact: true })).toBeVisible();
  await expect(page.getByText("OSD settings saved.", { exact: true })).toHaveCount(0);
  expect(posts).toBe(1);
  available = false;
  await page.reload();
  await expect(page.locator("body")).toContainText("Text editing is currently unavailable.");
  await expect(save).toBeDisabled();
  await expect(page.locator("#osd-fill")).toBeDisabled();
  expect(posts).toBe(1);
});

test("named OSD metadata stays editable when hardware OSD is unavailable", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let enabled = true;
  let metadataPosts = 0;
  let hardwarePosts = 0;
  const metadata = () => ({
    source: "raptor", persistent: true, supported: true, confirmed: true,
    saved: { available: true, id: "0123456789abcdef", enabled, entries: [
      { name: "room", type: "text", format: "Keittiö", position: "1,2", available: true },
      { name: "gain", type: "gain", format: "%s", position: "1,3", available: false, unavailable_reason: "gain producer missing" },
    ] },
    published: { id: "0123456789abcdef", generation: 2, status: 0, fresh: true, matches_saved: true },
  });
  await page.route("**/api/v1/prudynt/osd", (route) => route.fulfill({ json: { source: "raptor" } }));
  await page.route("**/api/v1/prudynt", (route) => { hardwarePosts++; return route.fulfill({ json: {} }); });
  await page.route("**/api/v1/config/osd-metadata", async (route) => {
    if (route.request().method() === "POST") {
      metadataPosts++;
      enabled = (await route.request().postDataJSON()).enabled;
    }
    return route.fulfill({ json: metadata() });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/osd");
  await expect(page.locator("body")).toContainText("OSD state is incomplete.");
  await expect(page.locator("#osd-burn-enabled")).toBeDisabled();
  await expect(page.locator("#osd-sei-enabled")).toBeEnabled();
  await expect(page.locator(".osd-entry")).toHaveCount(2);
  await expect(page.getByText("gain producer missing", { exact: false })).toBeVisible();
  await page.locator("#osd-sei-enabled").uncheck();
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("OSD settings saved.", { exact: true })).toBeVisible();
  expect(metadataPosts).toBe(1);
  expect(hardwarePosts).toBe(0);
});

test("unknown named OSD metadata is not replaced with an empty table", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let posts = 0;
  await page.route("**/api/v1/prudynt/osd", (route) => route.fulfill({ json: {
    source: "raptor", persistent: true, enabled: false, available: false, matches_saved: false,
    fields: { format: true, fill_color: true, outline_color: true }, format: "%T",
    fill_color: "#ffffffff", outline_color: "#000000ff",
  } }));
  await page.route("**/api/v1/config/osd-metadata", (route) => {
    if (route.request().method() === "POST") posts++;
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, confirmed: false,
      saved: { available: false, id: "", enabled: null, entries: [] },
      published: { id: "", generation: 0, status: -2, fresh: false, matches_saved: false },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/osd");
  await expect(page.locator("#osd-sei-enabled")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Save settings", exact: true })).toBeDisabled();
  expect(posts).toBe(0);
});

test("named OSD metadata save rejects an intervening readback ID", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let calls = 0;
  await page.route("**/api/v1/prudynt/osd", (route) => route.fulfill({ json: { source: "raptor" } }));
  await page.route("**/api/v1/config/osd-metadata", (route) => {
    calls++;
    const id = calls === 1 ? "1111111111111111" : calls === 2 ? "2222222222222222" : "3333333333333333";
    return route.fulfill({ json: {
      source: "raptor", persistent: true, supported: true, confirmed: true,
      saved: { available: true, id, enabled: true, entries: [
        { name: "room", type: "text", format: "Kitchen", position: "1,2", available: true },
      ] },
      published: { id, generation: calls, status: 0, fresh: true, matches_saved: true },
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/osd");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText(
    "named metadata saved, but the complete operation was not confirmed. Some OSD settings may have changed, but complete readback was not confirmed. Reload and explicitly retry saving.",
    { exact: true },
  )).toBeVisible();
  await expect(page.getByText("OSD settings saved.", { exact: true })).toHaveCount(0);
  expect(calls).toBe(3);
});


test("native Control adapter verifies daynight persistence and explicit retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/daynight");
  const mode = page.getByLabel("Day/night mode", { exact: true });
  await expect(mode).toBeEnabled();
  await expect(page.getByLabel("Night threshold", { exact: true })).toBeDisabled();
  await mode.selectOption("night");
  const threshold = page.getByLabel("Night luminance threshold", { exact: true });
  await expect(threshold).toBeEnabled();
  await threshold.fill("23");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await save.click();
  await expect(page.locator("body")).toContainText("Day/night may have changed");
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(mode).toHaveValue("night");
  await expect(threshold).toHaveValue("23");
  await expect(page.getByLabel("Saved night luminance threshold", { exact: true })).toHaveValue("20");
  await expect(page.getByLabel("Detection thresholds match saved settings", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Saved day/night mode", { exact: true })).toHaveValue("auto");
  await expect(page.getByLabel("Day/night mode matches saved setting", { exact: true })).not.toBeChecked();
  await save.click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Saved day/night mode", { exact: true })).toHaveValue("night");
  await expect(page.getByLabel("Saved night luminance threshold", { exact: true })).toHaveValue("23");
  await expect(page.getByLabel("Detection thresholds match saved settings", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Day/night mode matches saved setting", { exact: true })).toBeChecked();
});


test("Raptor timezone editing requires confirmed owners and shows an explicit failure reason", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  let supported = false;
  let applied = false;
  let selected = "Europe/Helsinki";
  let saves = 0;
  await page.route("**/api/v1/config/time", async (route) => {
    if (route.request().method() === "POST") {
      expect(supported).toBe(true);
      selected = route.request().postDataJSON().timezone;
      applied = true;
      saves += 1;
      await route.fulfill({ json: { status: "ok", persistent: true, applied: true } });
      return;
    }
    const response = await route.fetch();
    await route.fulfill({ json: {
      ...await response.json(), timezone: selected, source: "raptor",
      timezone_reload_supported: supported, timezone_applied: applied,
    } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/time");
  const timezone = page.getByLabel("Timezone", { exact: true });
  const application = page.getByLabel("Timezone application", { exact: true });
  await expect(timezone).toBeDisabled();
  await expect(application).toHaveValue("A required media service could not be verified. Timezone changes are unavailable.");
  expect(saves).toBe(0);
  supported = true;
  await page.reload();
  await expect(timezone).toBeEnabled();
  await expect(application).toHaveValue("Not confirmed; reload and retry saving");
  await timezone.fill("Etc/UTC");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(application).toHaveValue("Applied to required services");
  await expect(timezone).toHaveValue("Etc/UTC");
  expect(saves).toBe(1);
});

test("native Control adapter verifies timezone application and explicit same-zone retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/time");
  const timezone = page.getByLabel("Timezone", { exact: true });
  const application = page.getByLabel("Timezone application", { exact: true });
  await expect(timezone).toHaveValue("Etc/GMT");
  await expect(application).toHaveValue("Applied to required services");
  await timezone.fill("Europe/Helsinki");
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await save.click();
  await expect(page.locator("body")).toContainText("Time settings may be saved");
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(timezone).toHaveValue("Europe/Helsinki");
  await expect(application).toHaveValue("Not confirmed; reload and retry saving");
  const retry = page.waitForRequest((request) => request.url().endsWith("/api/v1/config/time") && request.method() === "POST");
  await save.click();
  expect((await retry).postDataJSON()).toMatchObject({ action: "update", timezone: "Europe/Helsinki" });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(application).toHaveValue("Applied to required services");
});

test("native Control adapter verifies privacy persistence and explicit same-state retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  const { writeFile, readFile } = await import("node:fs/promises");
  const root = process.env.RAPTOR_BROWSER_ROOT!;
  await writeFile(`${root}/privacy-case`, "1");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  const enabled = page.getByLabel("Protect video and microphone", { exact: true });
  const saved = page.getByLabel("Saved privacy setting", { exact: true });
  const matching = page.getByLabel("Privacy matches saved setting", { exact: true });
  const save = page.getByRole("button", { name: "Save privacy", exact: true });
  await expect(save).toBeEnabled();
  await expect(enabled).toBeEnabled();
  await expect(enabled).not.toBeChecked();
  await enabled.check();
  await save.click();
  await expect(page.locator("body")).toContainText("Privacy may have changed");
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(saved).not.toBeChecked();
  await expect(matching).not.toBeChecked();
  const retry = page.waitForRequest((request) => request.url().endsWith("/api/v1/prudynt") && request.method() === "POST");
  await save.click();
  expect((await retry).postDataJSON()).toEqual({ privacy: { enabled: true } });
  await expect(saved).toBeChecked();
  await expect(matching).toBeChecked();
  expect(JSON.parse(await readFile(`${root}/privacy-disk.json`, "utf8"))).toEqual({ enabled: "true" });
  await page.reload();
  await expect(enabled).toBeChecked();
  await expect(saved).toBeChecked();
  await enabled.uncheck();
  await save.click();
  await expect(matching).toBeChecked();
  await expect(saved).not.toBeChecked();
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
});


test("native Control adapter replaces multiple motion regions and verifies saved ROI after retry", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  const { writeFile, readFile } = await import("node:fs/promises");
  const root = process.env.RAPTOR_BROWSER_ROOT!;
  await writeFile(`${root}/roi-case`, "1");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/motion-privacy");
  const count = page.getByLabel("Current ROI count", { exact: true });
  const matches = page.getByLabel("Region matches saved setting", { exact: true });
  const save = page.getByRole("button", { name: "Save settings", exact: true });
  await expect(count).toHaveValue("16");
  const frameWidth = page.getByRole("spinbutton", { name: "Detection frame width", exact: true });
  await expect(frameWidth).toHaveValue("640");
  await expect(frameWidth).toHaveJSProperty("readOnly", true);
  await expect(page.locator("#field-motion-frame_width")).toBeHidden();
  await expect(page.getByLabel("ROI left", { exact: true })).toBeEmpty();
  for (const [label, value] of [["ROI left", "64"], ["ROI top", "32"], ["ROI right", "320"], ["ROI bottom", "240"]]) {
    await page.getByLabel(label!, { exact: true }).fill(value!);
  }
  await page.getByLabel("Enable motion detection", { exact: true }).check();
  await save.click();
  await expect(page.locator("body")).toContainText("Motion may have changed");
  expect(JSON.parse(await readFile(`${root}/motion-disk.json`, "utf8"))).not.toHaveProperty("roi_count");
  await page.reload();
  await expect(count).toHaveValue("1");
  await expect(page.getByLabel("ROI right", { exact: true })).toHaveValue("320");
  await expect(matches).not.toBeChecked();
  await expect(save).toBeEnabled();
  const retry = page.waitForRequest((request) => request.url().endsWith("/api/v1/prudynt") && request.method() === "POST");
  await save.click();
  expect((await retry).postDataJSON()).toMatchObject({ motion: { enabled: true, roi_count: 1, roi_0_x: 64, roi_0_y: 32, roi_1_x: 320, roi_1_y: 240 } });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(matches).toBeChecked();
  expect(JSON.parse(await readFile(`${root}/motion-disk.json`, "utf8"))).toMatchObject({ roi_count: "1", roi0: "64,32,319,239" });
  await page.reload();
  await expect(page.getByLabel("Saved detection region", { exact: true })).toHaveValue("64, 32 to 320, 240 (end edges excluded)");
  await expect(matches).toBeChecked();

  // Turning off must bypass even invalid draft geometry, without claiming it was saved.
  const enabled = page.getByLabel("Enable motion detection", { exact: true });
  const right = page.getByLabel("ROI right", { exact: true });
  const corners = ["ROI left", "ROI top", "ROI right", "ROI bottom"];
  const offMessage = "Motion is saved as off. Region edits were not saved. Turn motion on to save a region.";
  await expect(right).toBeEnabled();
  await right.fill("-1");
  await enabled.uncheck();
  for (const label of corners) await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
  const off = page.waitForRequest((request) => request.url().endsWith("/api/v1/prudynt") && request.method() === "POST");
  await save.click();
  expect((await off).postDataJSON()).toEqual({ motion: { enabled: false } });
  await expect(page.getByText(offMessage, { exact: true })).toBeVisible();
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);
  await expect(right).toHaveValue("320");
  expect(JSON.parse(await readFile(`${root}/motion-disk.json`, "utf8"))).toMatchObject({ enabled: "false", roi0: "64,32,319,239" });

  // An already-off load cannot offer editable corners or silently save a region.
  await page.reload();
  await expect(enabled).not.toBeChecked();
  for (const label of corners) await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText(offMessage, { exact: true })).toBeVisible();
  await expect(page.getByText("Settings saved.", { exact: true })).toHaveCount(0);

  // Only the UI observation is made uncertain; the native bridge stays unchanged.
  await page.route("**/api/v1/prudynt/motion", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    expect(body.enabled).toBe(false);
    body.roi.applied = false;
    body.roi.matches_saved = false;
    await route.fulfill({ response, json: body });
  }, { times: 1 });
  await page.reload();
  await expect(page.getByLabel("Region editing", { exact: true })).toHaveValue(/Update unconfirmed/);
  await expect(right).toBeDisabled();
  await enabled.check();
  for (const label of corners) await expect(page.getByLabel(label, { exact: true })).toBeEnabled();
  await right.fill("321");
  const repair = page.waitForRequest((request) => request.url().endsWith("/api/v1/prudynt") && request.method() === "POST");
  await save.click();
  expect((await repair).postDataJSON()).toMatchObject({ motion: { enabled: true, roi_count: 1, roi_1_x: 321 } });
  await expect(page.getByText("Settings saved.", { exact: true })).toBeVisible();
  await expect(matches).toBeChecked();
  expect(JSON.parse(await readFile(`${root}/motion-disk.json`, "utf8"))).toMatchObject({ enabled: "true", roi0: "64,32,320,239" });

});

test("native Control adapter carries current motion events and clears stopped or disconnected observations", async ({ page, request }) => {
  test.skip(!process.env.RAPTOR_BROWSER_ROOT, "requires the native Control bridge");
  const { writeFile } = await import("node:fs/promises");
  const root = process.env.RAPTOR_BROWSER_ROOT!;
  const observation = (active: boolean, receiving: boolean, motion: boolean, supported = true) => JSON.stringify({ status: "ok", active, receiving, motion, supported });
  await writeFile(`${root}/motion-event-status.json`, observation(true, true, true));
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const motion = page.getByRole("button", { name: "Motion detection", exact: true });
  await expect(motion).toHaveText("Active");
  for (const [state, text, available, detected] of [
    [observation(true, true, false), "On", true, false],
    [observation(false, false, true), "Off", true, false],
    [observation(false, true, true), "Unavailable", false, false],
    [observation(true, true, true, false), "Unavailable", false, false],
    ["{}", "Unavailable", null, null],
    ["disconnect", "Unavailable", null, null],
    [observation(true, true, false), "On", true, false],
    [observation(true, true, true), "Active", true, true],
  ] as const) {
    await writeFile(`${root}/motion-event-status.json`, state);
    const heartbeat = page.waitForResponse((response) => response.url().endsWith("/api/v1/runtime/heartbeat"));
    await page.getByRole("button", { name: "Reload", exact: true }).click();
    expect(await (await heartbeat).json()).toMatchObject({ motion_active: available ? detected : null });
    await expect(motion).toHaveText(text);
    if (text === "Unavailable") await expect(motion).toBeDisabled();
    const runtime = await page.request.get("/api/v1/runtime/motion");
    if (available === null) expect(runtime.status()).toBe(502);
    else expect(await runtime.json()).toMatchObject({ source: "raptor", available, active: detected });
  }
});

test("native Control adapter saves only available stream GOP and retries persistence", async ({ page, request }) => {
  const root = process.env.RAPTOR_BROWSER_ROOT;
  test.skip(!root, "requires native Control bridge");
  const { readFile, writeFile, unlink } = await import("node:fs/promises");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/streams");
  const main = page.locator(".stream-card").nth(0);
  const sub = page.locator(".stream-card").nth(1);
  await expect(main.getByLabel("GOP", { exact: true })).toHaveValue("30");
  await expect(main.getByLabel("Maximum GOP", { exact: true })).toBeDisabled();
  await expect(main.getByLabel("Buffers", { exact: true })).toBeDisabled();
  await expect(main.getByLabel("Buffers", { exact: true })).toHaveValue("1");
  await expect(main.getByLabel("Buffer status", { exact: true })).toHaveValue(
    /Active, configured and saved FrameSource buffers are 1/
  );
  await expect(main.getByLabel("Bitrate", { exact: true })).toBeDisabled();
  await expect(sub.getByLabel("GOP", { exact: true })).toBeDisabled();
  await expect(main.locator(":scope > .form-section-card")).toHaveCount(4);
  await main.getByLabel("GOP", { exact: true }).fill("60");
  const post = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/v1/prudynt"));
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  expect((await post).postDataJSON()).toEqual({ stream0: { gop: 60 } });
  await expect(page.getByText(/Stream GOP may have changed/)).toBeVisible();
  expect(await readFile(`${root}/gop-disk`, "utf8")).toBe("30");
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(main.getByLabel("GOP", { exact: true })).toHaveValue("60");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  expect(await readFile(`${root}/gop-disk`, "utf8")).toBe("60");
  await writeFile(`${root}/gop-clamp`, "1");
  await main.getByLabel("GOP", { exact: true }).fill("90");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByText(/Stream GOP may have changed/)).toBeVisible();
  expect(await readFile(`${root}/gop-disk`, "utf8")).toBe("60");
  await unlink(`${root}/gop-clamp`);
  await writeFile(`${root}/gop-unavailable`, "1");
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(main.getByLabel("GOP", { exact: true })).toBeDisabled();
  await unlink(`${root}/gop-unavailable`);
});

test("native Control adapter FPS main/sub success, explicit persistence retry and failure states", async ({ page, request }) => {
  const root = process.env.RAPTOR_BROWSER_ROOT;
  test.skip(!root, "requires native Control bridge");
  const { readFile, writeFile, unlink } = await import("node:fs/promises");
  await writeFile(`${root}/fps-enabled`, "1");
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor-native" } });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.goto("/#/streams");
  const cards = page.locator(".stream-card");
  const main = cards.nth(0).getByLabel("Frames per second", { exact: true });
  const sub = cards.nth(1).getByLabel("Frames per second", { exact: true });
  const save = () => page.getByRole("button", { name: "Save settings", exact: true }).click();
  const reload = () => page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(main).toHaveValue("20");
  await expect(sub).toHaveValue("20");
  for (const [id, input, rate] of [[0, main, "15"], [1, sub, "12"]] as const) {
    await input.fill(rate);
    const post = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/v1/prudynt"));
    await save();
    expect((await post).postDataJSON()).toEqual({ [`stream${id}`]: { fps: Number(rate) } });
    await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
    expect(await readFile(`${root}/fps-disk-${id}`, "utf8")).toBe(rate);
  }
  await writeFile(`${root}/fps-save-fail`, "1");
  await main.fill("18");
  await save();
  await expect(page.getByText(/FPS configuration applied, but saving is unconfirmed/)).toBeVisible();
  await expect(main).toHaveValue("18");
  expect(await readFile(`${root}/fps-disk-0`, "utf8")).toBe("15");
  const count = await readFile(`${root}/fps-sdk-count`, "utf8");
  await unlink(`${root}/fps-save-fail`);
  await sub.fill("17"); // Retry the pending owner first, then save this retained draft.
  const retry = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/v1/prudynt"));
  await page.getByRole("button", { name: "Retry FPS persistence and save changes", exact: true }).click();
  expect((await retry).postDataJSON()).toEqual({ stream0: { fps: 18 } });
  await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toBeVisible();
  // The pending main-stream save reuses its live FPS; only the new substream
  // draft requires an additional SDK change.
  expect(await readFile(`${root}/fps-sdk-count`, "utf8")).toBe(String(Number(count) + 1));
  expect(await readFile(`${root}/fps-disk-1`, "utf8")).toBe("17");
  for (const [marker, message] of [["fps-sdk-fail", "FPS was not applied."], ["fps-disconnect", "FPS result is unknown."], ["fps-motion-fail", "FPS configuration was applied and saved, but motion did not resume."]] as const) {
    await writeFile(`${root}/${marker}`, "1");
    await main.fill("19");
    await save();
    await expect(page.getByText(message, { exact: false })).toBeVisible();
    await expect(main).toHaveValue("19");
    await expect(main).toBeDisabled();
    await expect(page.getByText("Stream settings applied and saved with checked readback.", { exact: true })).toHaveCount(0);
    await unlink(`${root}/${marker}`);
    await reload();
    await expect(main).toBeEnabled();
  }
  await writeFile(`${root}/fps-recovery`, "1");
  await main.fill("21");
  await save();
  await expect(page.getByText(/FPS recovery is required. Your draft is kept/)).toBeVisible();
  await expect(main).toHaveValue("21");
  await expect(main).toBeDisabled();
  await unlink(`${root}/fps-recovery`);
  await reload();
  await expect(main).toBeDisabled();
  await expect(cards.nth(0)).toContainText("Recovery required");
  await unlink(`${root}/fps-enabled`);
  await reload();
  await expect(main).toBeDisabled();
  await expect(sub).toBeDisabled();
});

test("Live audio distinguishes unavailable owners from controllable Off and confirms toggles", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let state: boolean | null = null;
  const commands: unknown[] = [];
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), mic_enabled: state, spk_enabled: state } });
  });
  await page.route("**/api/v1/actions/control", async (route) => {
    const body = route.request().postDataJSON() as { audio: Record<string, boolean> };
    commands.push(body);
    state = Object.values(body.audio)[0]!;
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  for (const name of ["Microphone", "Speaker"]) {
    const toggle = page.getByRole("button", { name, exact: true });
    await expect(toggle).toHaveText("Unavailable");
    await expect(toggle).toBeDisabled();
  }
  state = false;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  for (const [name, key] of [["Microphone", "mic_enabled"], ["Speaker", "spk_enabled"]] as const) {
    const toggle = page.getByRole("button", { name, exact: true });
    await expect(toggle).toHaveText("Off");
    await expect(toggle).toBeEnabled();
    await toggle.click();
    await expect(toggle).toHaveText("On");
    expect(commands.at(-1)).toEqual({ audio: { [key!]: true } });
    await toggle.click();
    await expect(toggle).toHaveText("Off");
    expect(commands.at(-1)).toEqual({ audio: { [key!]: false } });
  }
  state = null;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(page.getByRole("button", { name: "Microphone", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Speaker", exact: true })).toBeDisabled();
});

test("Live IR controls keep unknown state disabled and send independent output commands", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  let state: 0 | 1 | null = null;
  const commands: unknown[] = [];
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), ircut_state: state, ir850_state: state } });
  });
  await page.route("**/api/v1/actions/control", async (route) => {
    const body = route.request().postDataJSON() as { cmd: string; val: 0 | 1 };
    commands.push(body);
    state = body.val;
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  for (const name of ["IR filter", "850 nm IR LED"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }
  state = 0;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  for (const [name, cmd] of [["IR filter", "ircut"], ["850 nm IR LED", "ir850"]] as const) {
    const toggle = page.getByRole("button", { name, exact: true });
    await expect(toggle).toHaveText("Off");
    await toggle.click();
    await expect(toggle).toHaveText("On");
    expect(commands.at(-1)).toEqual({ cmd, val: 1 });
    await toggle.click();
    await expect(toggle).toHaveText("Off");
    expect(commands.at(-1)).toEqual({ cmd, val: 0 });
  }
});

test("Live recording blocks no-card start and keeps channel stop independent", async ({ page, request }) => {
  await request.post("/__fixture__/reset");
  await request.post("/__fixture__/scenario", { data: { kind: "raptor" } });
  const states: [boolean | null, boolean | null] = [false, true];
  let available = false;
  const commands: unknown[] = [];
  await page.route("**/api/v1/runtime/heartbeat", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), rec_ch0: states[0], rec_ch1: states[1],
      rec_ch0_available: available, rec_ch1_available: available,
      rec_ch0_file_closed: states[0] === false, rec_ch1_file_closed: states[1] === false,
      rec_ch0_reason: available ? null : "no_sd", rec_ch1_reason: available ? null : "no_sd" } });
  });
  await page.route("**/api/v1/actions/control", async (route) => {
    const body = route.request().postDataJSON() as { mp4: { start?: { channel: 0 | 1 }; stop?: { channel: 0 | 1 } } };
    commands.push(body);
    const action = body.mp4.start ?? body.mp4.stop!;
    states[action.channel] = body.mp4.start !== undefined;
    await route.fulfill({ json: { status: "accepted" } });
  });
  await page.goto("/");
  await page.getByLabel("Password", { exact: true }).fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const main = page.getByRole("button", { name: "Record main stream", exact: true });
  const sub = page.getByRole("button", { name: "Record substream", exact: true });
  await expect(main).toHaveText("Off · No SD card");
  await expect(main).toBeDisabled();
  await expect(sub).toHaveText("On");
  await expect(sub).toBeEnabled();
  await sub.click();
  await expect(sub).toHaveText("Off · No SD card");
  expect(commands).toEqual([{ mp4: { stop: { channel: 1 } } }]);
  available = true;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await main.click();
  await expect(main).toHaveText("On");
  await expect(sub).toHaveText("Off");
  expect(commands.at(-1)).toEqual({ mp4: { start: { channel: 0 } } });
  states[0] = null;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(main).toBeDisabled();
  await expect(main).toHaveText("Unavailable");
  await page.getByRole("button", { name: "Stop main stream recording", exact: true }).click();
  await expect(main).toHaveText("Off");
  expect(commands.at(-1)).toEqual({ mp4: { stop: { channel: 0 } } });
  await expect(page.getByRole("button", { name: "Stop main stream recording", exact: true })).toBeHidden();
});
