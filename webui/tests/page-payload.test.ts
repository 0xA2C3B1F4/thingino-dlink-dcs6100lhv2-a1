import assert from "node:assert/strict";
import { readConfigSource } from "./support/config-source";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { decodeOsd } from "../src/api/decode";
import { buildConfigPayload, type FieldSpec } from "../src/app/forms";
import type { JsonObject } from "../src/api/contracts";
import { buildAccessUpdate, buildDayNightUpdate, buildGpioUpdate, buildHomeAssistantUpdate, buildImagingRequests, buildMotionPrivacyUpdate, buildOsdUpdate, buildTimeUpdate, buildWebuiUpdate, cameraLocalTime, composeRgba, loadTimeConfig, resolveTimezoneSearch, splitRgba, visibleTimezoneOptions, withOsdTimezoneToken } from "../src/pages/config";
import {
  audioFixture, daynightFixture, gpioFixture, homeAssistantFixture, imageFixture, imagingRuntimeFixture,
  motionFixture, networkFixture, osdFixture, privacyFixture, recorderFixture,
  rsyslogFixture, sendFixture, stream0Fixture, stream1Fixture, timeFixture, webuiFixture,
} from "./support/fixtures";
import { assertSecretSemantics, assertUnchangedPaths } from "./support/contracts";

const text = (path: string): FieldSpec => ({ path, label: path, type: "text" });
const number = (path: string): FieldSpec => ({ path, label: path, type: "number" });
const checkbox = (path: string, options: Partial<FieldSpec> = {}): FieldSpec => ({ path, label: path, type: "checkbox", ...options });
const secret = (path: string): FieldSpec => ({ path, label: path, type: "password", writeOnlySecret: true });

function changes(entries: Array<[string, string | number | boolean | null | undefined]>): ReadonlyMap<string, string | number | boolean | null | undefined> {
  return new Map(entries);
}

test("network save payload starts with the complete nested GET model", () => {
  const fields = [
    text("hostname"), text("interfaces.wlan0.address"), text("interfaces.eth0.address"), text("interfaces.usb0.address"),
    checkbox("wifi_ap.enabled", { readOnly: true }), checkbox("interfaces.wlan0.ipv6", { readOnly: true }), secret("wifi.password"),
  ];
  const payload = buildConfigPayload(networkFixture, changes([
    ["hostname", "dcs6100-lab"], ["interfaces.wlan0.address", "192.0.2.80"], ["wifi_ap.enabled", true], ["interfaces.wlan0.ipv6", true], ["wifi.password", ""],
  ]), fields);
  assertUnchangedPaths(networkFixture, payload, ["hostname", "interfaces.wlan0.address", "wifi.password"]);
  assert.equal((payload.wifi_ap as Record<string, unknown>).enabled, false);
  assert.equal(((payload.interfaces as Record<string, unknown>).wlan0 as Record<string, unknown>).ipv6, false);
  assert.equal(Object.hasOwn((payload.wifi as Record<string, unknown>), "password"), false);
  assert.equal((payload.wifi as Record<string, unknown>).password_set, true);
});

test("every structured domain preserves backend-only and read-only fields", () => {
  const cases: Array<[string, JsonObject, string]> = [
    ["audio", audioFixture, "mic_vol"],
    ["image", imageFixture, "brightness"],
    ["stream0", stream0Fixture, "bitrate"],
    ["stream1", stream1Fixture, "enabled"],
    ["webui", webuiFixture, "theme"],
    ["rsyslog", rsyslogFixture, "host"],
    ["daynight", daynightFixture, "night_threshold"],
    ["gpio", gpioFixture, "led.startup_indicator"],
    ["ha", homeAssistantFixture, "enabled"],
    ["osd", osdFixture, "burnin.enabled"],
    ["motion", motionFixture, "sensitivity"],
    ["privacy", privacyFixture, "enabled"],
  ];
  for (const [name, loaded, path] of cases) {
    const value = path.includes("enabled") ? false : path.includes("threshold") ? 30 : path.includes("sensitivity") ? 6 : path.includes("brightness") ? 130 : path.includes("vol") ? 71 : "changed";
    const payload = buildConfigPayload(loaded, changes([[path, value]]), [path.includes("vol") || path.includes("threshold") || path.includes("brightness") || path.includes("sensitivity") ? number(path) : typeof value === "boolean" ? checkbox(path) : text(path)]);
    assertUnchangedPaths(loaded, payload, [path]);
    assert.ok(Object.keys(payload).length >= Object.keys(loaded).length, `${name} payload dropped top-level data`);
  }
});

test("write-only secrets are omitted when blank and included only when changed", () => {
  const fields = [secret("password")];
  const access = { username: "root", password: null, password_set: true } satisfies JsonObject;
  const unchanged = buildConfigPayload(access, changes([["password", ""]]), fields);
  assert.equal(Object.hasOwn(unchanged, "password"), false);
  assert.equal(unchanged.password_set, true);
  const changed = buildConfigPayload(access, changes([["password", "new-secret-for-fixture"]]), fields);
  assert.equal(changed.password, "new-secret-for-fixture");
});

test("nullable access fields remain unchanged instead of receiving invented endpoints", () => {
  assert.deepEqual(buildAccessUpdate({
    username: null, password: "", password_set: false, rtsp_port: null,
    rtsp_ch0: null, rtsp_ch1: null, rtsp_mic: null,
    onvif_port: 80, onvif_enabled: true, onvif_ingress: "same-origin",
  }), { onvif_port: 80, onvif_enabled: true });
});

test("structured save payloads keep disabled stream sentinels", () => {
  const payload = buildConfigPayload(stream1Fixture, changes([["enabled", false]]), [checkbox("enabled")]);
  assert.equal(payload.fps, 0);
  assert.equal(payload.buffers, -1);
  assert.equal(payload.audio_enabled, false);
});

test("fixture redaction remains testable after page payload construction", () => {
  for (const value of [networkFixture, audioFixture, homeAssistantFixture, sendFixture]) assertSecretSemantics(value);
});

test("recorder requests remain one canonical top-level domain", () => {
  const recorder = recorderFixture.data as JsonObject;
  const video = recorder.video as Record<string, unknown>;
  const payload = { video: { ...video, duration: 600 } };
  assert.deepEqual(Object.keys(payload), ["video"]);
  assert.equal((payload.video as Record<string, unknown>).duration, 600);
  assert.equal((recorder.timelapse as Record<string, unknown>).enabled, true);
});

test("time save maps the selected IANA timezone without exposing POSIX input", async () => {
  const source = await readConfigSource();
  assert.match(source, /path: "timezone",[\s\S]*type: "search",[\s\S]*optionsFrom: "timezone_options"/);
  assert.match(source, /\.\.\.section\("Advanced"/);
  assert.doesNotMatch(source, /collapsibleSections: \["Advanced"\]/);
  assert.match(source, /current_local_time/);
  assert.doesNotMatch(source, /text\("tz_name"|text\("tz_data"|POSIX timezone value/);
  const update = buildTimeUpdate(timeFixture);
  const expected = { ...timeFixture } as JsonObject;
  for (const field of ["current_local_time", "timezone_options", "current_unix_time", "current_unix_ms", "local_time", "sync_status_raw_base64"]) delete expected[field];
  assert.deepEqual(update, { ...expected, action: "update" });
  assert.equal(Object.hasOwn(update, "current_local_time"), false);
  assert.equal(Object.hasOwn(update, "tz_name"), false);
  assert.equal(Object.hasOwn(update, "tz_data"), false);
  assert.match(cameraLocalTime(timeFixture), /2026-08-23 1[0-9]:34:56/);
  assert.match(String(loadTimeConfig(timeFixture).current_local_time), /EEST|GMT\+3/);
});

test("timezone search resolves cities and hides technical aliases without changing the saved IANA value", () => {
  const options = [
    { name: "Etc/GMT", label: "Etc/GMT" },
    { name: "Etc/GMT+2", label: "Etc/GMT+2" },
    { name: "Etc/UTC", label: "UTC" },
    { name: "Europe/Helsinki", label: "Europe/Helsinki" },
    { name: "America/New_York", label: "America/New_York" },
  ];
  assert.equal(resolveTimezoneSearch("helsinki", options), "Europe/Helsinki");
  assert.equal(resolveTimezoneSearch("HELSINKI", options), "Europe/Helsinki");
  assert.equal(resolveTimezoneSearch("York", options), "America/New_York");
  assert.equal(resolveTimezoneSearch("not-a-timezone", options), undefined);
  const visible = visibleTimezoneOptions(options.map(({ name, label }) => ({ value: name, label })), "Etc/GMT");
  assert.deepEqual(visible.map(({ value }) => value), ["Etc/GMT", "Etc/UTC", "Europe/Helsinki", "America/New_York"]);
  assert.equal(buildTimeUpdate({ ...timeFixture, timezone: "helsinki" }).timezone, "Europe/Helsinki");
  assert.throws(() => buildTimeUpdate({ ...timeFixture, timezone: "not-a-timezone" }), /supported timezone/);
});

test("P0/P1 pages expose structured controls instead of raw JSON editors", async () => {
  const source = await readConfigSource();
  assert.doesNotMatch(source, /renderJsonEditor/);
  for (const field of ["mic_format", "mic_agc_enabled", "mic_high_pass_filter", "mic_is_digital", "buffer_warn_frames", "buffer_cap_frames", "tap_enabled", "tap_path", "force_stereo", "hflip", "vflip", "audio_enabled", "core_wb_mode", "night_threshold", "sunrise_offset", "loglevel", "startup_indicator", "enable_motion_guard", "ota_check_interval", "ivs_polling_timeout", "motor_settle_ms", "roi_count", "playonspeaker"]) {
    assert.match(source, new RegExp(field));
  }
  for (const field of ["osd", "motion", "privacy"]) assert.match(source, new RegExp(`decode${field.charAt(0).toUpperCase()}${field.slice(1)}`));
  for (const range of [
    /audio\.mic_vol"[^\n]*-30, 120/, /audio\.mic_gain"[^\n]*0, 31/, /audio\.mic_alc_gain"[^\n]*0, 7/,
    /audio\.mic_noise_suppression"[^\n]*0, 3/, /audio\.mic_agc_compression_gain_db"[^\n]*0, 90/, /audio\.mic_agc_target_level_dbfs"[^\n]*0, 31/,
    /audio\.spk_vol"[^\n]*-30, 120/, /audio\.spk_gain"[^\n]*0, 31/, /image\.brightness"[^\n]*rangeFrom/,
    /image\.backlight_compensation"[^\n]*rangeFrom/, /image\.wb_bgain"[^\n]*0, 1024/, /image\.ae_compensation"[^\n]*0, 255/,
    /stream1\.fps"[^\n]*0, 30/, /stream1\.buffers"[^\n]*-1/,
  ]) assert.match(source, range);
  for (const values of ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM", "CAPPED_VBR", "CAPPED_QUALITY"]) assert.match(source, new RegExp(values));
});

test("stream settings use two bounded cards without changing the complete save model", async () => {
  const source = await readConfigSource();
  const formSource = await readFile("src/app/forms.ts", "utf8");
  const styles = await readFile("src/styles.css", "utf8");
  for (const marker of ["cardGroups: true", "cardFocusStreams: true", "cardState:", "On, no frames", "Frame rate is 0; set 1–30 fps to start", "Enable stream", "Codec and resolution", "Frame rate and rate control", "GOP, profile and buffers", "RTSP and audio"]) assert.ok(source.includes(marker), marker);
  for (const marker of ["Main stream · CH0", "Sub stream · CH1", "data-stream-card"]) assert.ok(formSource.includes(marker), marker);
  for (const marker of ["stream-resolution-field", "stream-resolution-inputs", 'text: "Width"', 'text: "Height"', 'text: "×"']) assert.ok(formSource.includes(marker), marker);
  assert.doesNotMatch(formSource, /controls\.append\(state\)/);
  assert.match(formSource, /header\.append\([\s\S]*state,[\s\S]*controls,[\s\S]*summary,/);
  assert.match(styles, /minmax\(min\(100%, 34rem\), 1fr\)/);
  assert.match(styles, /"title controls"\s+"state controls"\s+"summary summary"/);
  assert.match(styles, /"title title"\s+"state controls"\s+"summary summary"/);
  assert.doesNotMatch(source, /Main (width|height|codec|frames|GOP|buffers)|Substream (width|height|codec|frames|GOP|buffers)/);
  const loaded = { stream0: stream0Fixture, stream1: stream1Fixture } as JsonObject;
  const fields: FieldSpec[] = [
    { path: "stream0.bitrate", label: "Bitrate", type: "number" },
    { path: "stream1.fps", label: "Frames per second", type: "number" },
  ];
  const payload = buildConfigPayload(loaded, changes([["stream0.bitrate", 3000]]), fields);
  assert.equal((payload.stream0 as JsonObject).bitrate, 3000);
  assert.equal((payload.stream1 as JsonObject).fps, 0);
  assert.equal((payload.stream1 as JsonObject).buffers, -1);
  assert.deepEqual(Object.keys(payload), ["stream0", "stream1"]);
});

test("image save sends supported live ISP values before the full persisted image domain", () => {
  const requests = buildImagingRequests(
    { image: { ...imageFixture, brightness: 141, highlight_depress: 77 } },
    { image: imageFixture, imaging_runtime: imagingRuntimeFixture.message },
  );
  assert.deepEqual(requests.live, {
    brightness: 141,
    contrast: 128,
    sharpness: 128,
    saturation: 128,
    backlight: 0,
    wide_dynamic_range: 128,
    defog: 128,
    noise_reduction: 128,
  });
  assert.equal(Object.hasOwn(requests.live, "tone"), false);
  assert.deepEqual(requests.persist, { image: { ...imageFixture, brightness: 141, highlight_depress: 77 } });
});

test("image quality explains that ISP controls are shared by both streams", async () => {
  const source = await readConfigSource();
  assert.match(source, /Shared sensor and ISP settings applied before both stream encoders/);
  assert.match(source, /Stream-specific resolution, frame rate and encoding remain on Video streams/);
});

test("OSD controls preserve exact RGBA values and use the shared Streamer preview", async () => {
  assert.deepEqual(splitRgba("#A0b1C280"), { rgb: "#a0b1c2", alpha: 128 });
  assert.equal(composeRgba("#a0b1c2", 128), "#a0b1c280");
  assert.throws(() => splitRgba("#ffffff"), /RRGGBBAA/);
  assert.throws(() => composeRgba("#ffffff", 256), /alpha/);
  const source = await readConfigSource();
  const formsSource = await readFile("src/app/forms.ts", "utf8");
  const sharedPreview = await readFile("src/app/streamer-preview.ts", "utf8");
  for (const marker of ["osd-color-picker", "osd-alpha", "Include short timezone (%Z)", "DEFAULT_OSD_FORMAT = \"%F %T %Z\"", "switchField(\"Burn-in overlay\"", "osd-burn-toggle"]) assert.ok(source.includes(marker), marker);
  assert.doesNotMatch(source, /Style preview|osd-preview-sample|osdExample|Start live preview|streamPreview/);
  for (const marker of ["Show live preview", "Hide live preview", "STREAMER_PREVIEW_ROUTES", "thingino:config-saved", "Retrying…"]) assert.ok(sharedPreview.includes(marker), marker);
  for (const label of ["Name", "Type", "Format", "Position"]) assert.match(source, new RegExp(`entryField\\(\\"${label}\\"`));
  assert.match(source, /osd-entry-\$\{serial\}-(name|type|format|position)/);
  assert.match(source, /button secondary button-compact/);
  assert.match(source, /form-section-heading/);
  assert.match(formsSource, /collapsible \? "details" : "section"/);
  assert.match(formsSource, /collapsible \? "summary" : "h2"/);
  assert.doesNotMatch(formsSource, /collapsible \? "details" : "fieldset"/);
  assert.equal(withOsdTimezoneToken("%F %T", true), "%F %T %Z");
  assert.equal(withOsdTimezoneToken("%F %T %Z", false), "%F %T");
  assert.equal(withOsdTimezoneToken("%F %T %Z", true), "%F %T %Z");
});

test("OSD save merges edited controls into the complete decoded domain", () => {
  const loaded = decodeOsd({
    ...osdFixture,
    privacy: { enabled: false },
    burnin: { ...osdFixture.burnin, backend_only: "keep" },
    sei: { ...osdFixture.sei, future: true, entries: { clock: { ...osdFixture.sei.entries.clock, backend_only: 7 } } },
  });
  const updated = buildOsdUpdate(
    loaded,
    { ...osdFixture.burnin, scale: 2 },
    false,
    { clock: { type: "timestamp", format: "%T", position: "5,5" } },
  );
  assert.deepEqual(updated.privacy, { enabled: false });
  assert.equal((updated.burnin as JsonObject).backend_only, "keep");
  assert.equal((updated.sei as JsonObject).future, true);
  assert.equal((((updated.sei as JsonObject).entries as JsonObject).clock as JsonObject).backend_only, 7);
});

test("D-Link privacy save applies one physical privacy state to both streams", () => {
  const update = buildMotionPrivacyUpdate({ motion: motionFixture, privacy: { ...privacyFixture, enabled: true, backend_only: "drop" } });
  assert.deepEqual(update.privacy, { enabled: true, stream0_enabled: true, stream1_enabled: true });
});

test("disabled day and night schedules omit empty time values", () => {
  const update = buildDayNightUpdate({ ...daynightFixture, schedule: { enabled: false, start_at: "", stop_at: "" } });
  assert.deepEqual(update.schedule, { enabled: false });
  assert.equal((update.sun as JsonObject).enabled, false);
});

test("Home Assistant save omits response-only and unsupported fields", () => {
  const update = buildHomeAssistantUpdate({ ...homeAssistantFixture, enable_ir940: false, enable_white_light: false });
  assert.equal(Object.hasOwn(update, "enable_ir940"), false);
  assert.equal(Object.hasOwn(update, "enable_white_light"), false);
  assert.equal(Object.hasOwn(update, "doorbell_supported"), false);
  assert.equal(Object.hasOwn(update, "ota_supported"), false);
  assert.equal(update.enable_doorbell, false);
  assert.equal(update.enable_ota, false);
  assert.equal(Object.hasOwn(update.mqtt as JsonObject, "password_set"), false);
  assert.equal((update.mqtt as JsonObject).password, null);
});

test("GPIO, WebUI and admin pages use the exact Control write schema", async () => {
  const source = await readConfigSource();
  assert.deepEqual(buildGpioUpdate({
    startup_indicator: "green",
  }), {
    startup_indicator: "green",
  });
  assert.throws(() => buildGpioUpdate({ startup_indicator: "blue" }), /Off, Green or Red/);
  assert.match(source, /GPIO49 \/ GPIO50/);
  assert.match(source, /GPIO61/);
  assert.match(source, /Hardware I\/O map/);
  assert.deepEqual(buildWebuiUpdate({
    username: "root",
    theme: "dark",
    paranoid: false,
    track_focus: true,
    focus_timeout: 15,
    auth_bypass_ips: "192.0.2.0/24",
  }), {
    theme: "dark",
    paranoid: false,
    track_focus: true,
    focus_timeout: 15,
    auth_bypass_ips: "192.0.2.0/24",
  });
  for (const field of ["name", "email", "telegram", "discord"]) {
    assert.match(source, new RegExp(`text\\("${field}"`));
  }
  for (const legacy of ["admin_name", "admin_email", "admin_telegram", "admin_discord"]) {
    assert.doesNotMatch(source, new RegExp(legacy));
  }
});

test("configuration save stays disabled until a full GET succeeds", async () => {
  const source = await readFile("src/app/forms.ts", "utf8");
  const styles = await readFile("src/styles.css", "utf8");
  assert.match(source, /save\.disabled = true;[\s\S]*async function load/);
  assert.match(source, /loadedSuccessfully = true;\s*save\.disabled = false;/);
  assert.match(source, /save\.disabled = !loadedSuccessfully/);
  assert.match(styles, /\.field \{\s+align-content: start;/);
  assert.match(styles, /input\.input,\s+select\.input \{\s+height: 2\.7rem;\s+min-height: 2\.7rem;/);
});

test("shared typography and card headings use centralized design tokens", async () => {
  const styles = await readFile("src/styles.css", "utf8");
  const tools = await readFile("src/pages/tools.ts", "utf8");
  for (const token of ["--font-ui", "--font-display", "--font-code", "--text-overline", "--text-label", "--text-body", "--text-card-title", "--text-page-title"]) {
    assert.ok(styles.includes(token), token);
  }
  for (const line of styles.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("--text-") || trimmed.startsWith("--font-")) continue;
    if (trimmed.includes("font-size:")) assert.match(trimmed, /font-size: var\(--text-/);
    if (trimmed.includes("font-family:")) assert.match(trimmed, /font-family: var\(--font-/);
  }
  assert.match(styles, /\.form-section-title \{[\s\S]*font-size: var\(--text-card-title\)/);
  assert.match(styles, /\.tools-panel > h2:first-child \{[\s\S]*background: var\(--surface-muted\);[\s\S]*font-size: var\(--text-card-title\)/);
  assert.match(styles, /\.sensor-chart-label \{ font-family: var\(--font-ui\); font-size: var\(--text-overline\);/);
  assert.doesNotMatch(tools, /setAttribute\(["']font-(?:family|size|weight)["']/);
});
