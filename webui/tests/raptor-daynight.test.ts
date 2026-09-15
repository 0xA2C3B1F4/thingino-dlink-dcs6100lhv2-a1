import assert from "node:assert/strict";
import test from "node:test";
import { decodeDayNightForPage, buildDayNightUpdate, daynight } from "../src/pages/config/hardware";
import { daynightFixture } from "./support/fixtures";
import type { ApiClient } from "../src/api/client";
const state = { source: "raptor", persistent: true, supported: true, available: true,
  mode: "night", state: "night", saved_mode: "auto", matches_saved: false };

test("disabled RIC remains supported but cannot expose live ownership or save controls", () => {
  const outputs = { supported: true, available: false, color: true, ircut: true,
    color_owner: "disabled", ircut_owner: "disabled", color_state: null, ircut_commanded_state: null,
    saved_values: { color: true, ircut: true }, matches_saved: true };
  const raw = { ...state, service_enabled: false, available: false, state: null,
    automatic_outputs: outputs, ir850_at_night: true, saved_ir850_at_night: true,
    ir850_at_night_matches_saved: true };
  const loaded = decodeDayNightForPage(raw);
  assert.equal(loaded.supported, true);
  assert.equal(loaded.service_enabled, false);
  assert.equal(loaded.daynight_policy_control, false);
  assert.equal(loaded.daynight_ir850_policy_control, false);
  assert.equal(loaded.daynight_automatic_outputs_control, false);
  assert.throws(() => buildDayNightUpdate(loaded), /service is disabled/);
  assert.equal(daynight.fields.find(field => field.path === "service_enabled")?.readOnly, true);
  assert.equal(decodeDayNightForPage(state).service_enabled, true);
  for (const service_enabled of [null, "false", 0]) {
    assert.throws(() => decodeDayNightForPage({ ...raw, service_enabled }), /service state/);
  }
  assert.throws(() => decodeDayNightForPage({ ...raw, available: true }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({ ...raw, service_enabled: true }), /ownership/);
  assert.throws(() => decodeDayNightForPage({ ...raw, automatic_outputs: { ...outputs, color_owner: "manual" } }), /ownership/);
  assert.throws(() => decodeDayNightForPage({ ...raw, automatic_outputs: { ...outputs, color_state: "day" } }), /readback/);
  for (const key of ["thresholds", "timing", "schedule", "sun", "startup", "automation", "automatic_outputs", "confirmation", "loglevel"]) {
    assert.throws(() => decodeDayNightForPage({ ...raw, [key]: { available: true } }), /cannot report available/);
  }
});

test("Raptor timing payloads do not require a legacy schedule", () => {
  for (const timing of [
    { sample_interval_ms: 250 },
    { sample_interval_ms: 250, transition_delay_s: 7 },
  ]) {
    assert.equal(daynight.validate!({ mode: "auto", timing }, state), undefined);
  }
  for (const path of ["sample_interval_ms", "transition_delay_s"]) {
    assert.deepEqual(daynight.fields.find(field => field.path === path)?.visibleWhen, {
      path: "daynight_full_controls", equals: true,
    });
  }
});

test("Raptor day/night saves only policy and keeps saved state separate", () => {
  const loaded = decodeDayNightForPage(state);
  assert.equal(loaded.saved_mode, "auto");
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night" });
  assert.deepEqual(buildDayNightUpdate(decodeDayNightForPage(daynightFixture)), buildDayNightUpdate(daynightFixture));
  assert.throws(() => decodeDayNightForPage({ ...state, state: "day" }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({ ...state, mode: ["night"] }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({ ...state, matches_saved: true }), /inconsistent/);
});

test("Raptor pause, automatic output ownership, counts and log level stay independently confirmed", () => {
  const policies = {
    automation: { supported: true, available: true, paused: false, saved_paused: false, matches_saved: true },
    automatic_outputs: { supported: true, available: true, color: true, ircut: false,
      color_owner: "automatic", ircut_owner: "manual", color_state: "night", ircut_commanded_state: "day",
      saved_values: { color: true, ircut: false }, matches_saved: true },
    confirmation: { supported: true, available: true, values: { day: 3, night: 5 },
      saved_values: { day: 3, night: 5 }, matches_saved: true },
    loglevel: { supported: true, available: true, value: "info", saved_value: "info", matches_saved: true },
  };
  const raw = { ...state, ...policies };
  const loaded = decodeDayNightForPage(raw);
  assert.deepEqual(buildDayNightUpdate(loaded, raw), { mode: "night" });
  const update = buildDayNightUpdate({
    ...loaded,
    automation: { ...policies.automation, paused: true },
    automatic_outputs: { ...policies.automatic_outputs, color: false },
    confirmation: { ...policies.confirmation, values: { day: 4, night: 6 } },
    loglevel: { ...policies.loglevel, value: "debug" },
  }, raw);
  assert.deepEqual(update, {
    mode: "night",
    automation: { paused: true },
    automatic_outputs: { color: false, ircut: false },
    confirmation: { day: 4, night: 6 },
    loglevel: "debug",
  });
  assert.match(String(daynight.fields.find(field => field.path === "confirmation.supported")?.description), /Photo uses its own/);
  assert.throws(() => decodeDayNightForPage({ ...raw, automatic_outputs: { ...policies.automatic_outputs, color_state: "auto" } }), /readback/);

  const photo = decodeDayNightForPage({ ...state, confirmation: {
    supported: false, available: false, values: null, saved_values: null, matches_saved: false,
  } });
  assert.equal(photo.daynight_confirmation_control, false);
  assert.deepEqual(buildDayNightUpdate(photo), { mode: "night" });
});

test("Raptor startup initial mode is restart-only and hides legacy duplicates", () => {
  const startup = {
    supported: true,
    available: true,
    configured: "default",
    saved: "default",
    boot_active: "default",
    matches_saved: true,
    restart_required: false,
  };
  const raw = { ...state, mode: "auto", state: "day", matches_saved: true, startup };
  const loaded = decodeDayNightForPage(raw);
  assert.equal(loaded.daynight_initial_mode_control, true);
  assert.deepEqual(
    buildDayNightUpdate({
      ...loaded,
      startup: { ...startup, configured: "night" },
    }, raw),
    { mode: "auto", initial_mode: "night" },
  );
  assert.equal(daynight.validate!({ mode: "auto", initial_mode: "night" }, raw), undefined);
  assert.deepEqual(buildDayNightUpdate(loaded, raw), { mode: "auto" });
  assert.equal(
    daynight.fields.find(field => field.path === "startup.configured")?.options?.[0]?.label,
    "Default (Day until automation takes over)",
  );
  for (const path of ["initial_mode", "force_mode"]) {
    assert.deepEqual(daynight.fields.find(field => field.path === path)?.visibleWhen, {
      path: "daynight_full_controls",
      equals: true,
    });
  }
  assert.throws(() => decodeDayNightForPage({
    ...raw,
    startup: { ...startup, restart_required: true },
  }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({
    ...raw,
    startup: { ...startup, configured: "auto", matches_saved: false },
  }), /invalid/);

  const fullRaw = {
    ...raw,
    ir850_at_night: true,
    saved_ir850_at_night: true,
    ir850_at_night_matches_saved: true,
    thresholds: { supported: true, available: true, trigger: "gain",
      values: { day_threshold: 100, night_threshold: 200 },
      saved_values: { day_threshold: 100, night_threshold: 200 }, matches_saved: true },
    timing: { supported: true, available: true, transition_delay_supported: true,
      values: { sample_interval_ms: 250, transition_delay_s: 7 },
      saved_values: { sample_interval_ms: 250, transition_delay_s: 7 }, matches_saved: true },
    schedule: { supported: true, available: true, active: false, target: null, application_ok: true,
      values: { enabled: false, start_at: "07:00", stop_at: "19:00" },
      saved_values: { enabled: false, start_at: "07:00", stop_at: "19:00" }, matches_saved: true },
    sun: { supported: true, available: true, active: false, target: null, condition: null, application_ok: true,
      values: { enabled: false, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 0, sunset_offset: 0 },
      saved_values: { enabled: false, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 0, sunset_offset: 0 }, matches_saved: true },
  };
  const fullLoaded = decodeDayNightForPage(fullRaw);
  const initialOnly = buildDayNightUpdate({
    ...fullLoaded,
    startup: { ...(fullLoaded.startup as Record<string, unknown>), configured: "night" },
  }, fullRaw);
  assert.deepEqual(initialOnly, { mode: "auto", initial_mode: "night" });
  assert.equal(daynight.validate!(initialOnly, fullRaw), undefined);

  const unknownDisk = decodeDayNightForPage({
    ...raw,
    startup: { ...startup, saved: null, matches_saved: false, restart_required: null },
  });
  assert.equal((unknownDisk.startup as Record<string, unknown>).restart_required, null);

  const pending = {
    ...raw,
    startup: { ...startup, configured: "night", saved: "default", matches_saved: false },
  };
  assert.deepEqual(buildDayNightUpdate(decodeDayNightForPage(pending), pending), {
    mode: "auto",
    initial_mode: "night",
  });
});

test("Raptor 850 nm Night-mode policy is distinct from legacy output capability", () => {
  const loaded = decodeDayNightForPage({
    ...state,
    ir850_at_night: true,
    saved_ir850_at_night: false,
    ir850_at_night_matches_saved: false,
  });
  assert.equal(loaded.daynight_ir850_policy_control, true);
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night", ir850_at_night: true });
  assert.deepEqual(buildDayNightUpdate(loaded, {
    ...state,
    ir850_at_night: true,
    saved_ir850_at_night: false,
    ir850_at_night_matches_saved: false,
  }), { mode: "night", ir850_at_night: true });
  assert.equal(daynight.fields.some(field => field.path === "ir850_at_night"), true);
  assert.deepEqual(daynight.fields.find(field => field.path === "controls.ir850")?.enabledWhen, {
    path: "daynight_full_controls", equals: true,
  });
  assert.throws(() => decodeDayNightForPage({
    ...state,
    ir850_at_night: true,
    saved_ir850_at_night: true,
    ir850_at_night_matches_saved: false,
  }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({
    ...state,
    ir850_at_night: 1,
    saved_ir850_at_night: null,
    ir850_at_night_matches_saved: false,
  }), /inconsistent/);
  const oldNative = decodeDayNightForPage(state);
  assert.equal(oldNative.daynight_ir850_policy_control, false);
  assert.deepEqual(buildDayNightUpdate(oldNative), { mode: "night" });
});

test("Raptor day/night rejects unconfirmed save and allows an explicit recovery mode", async () => {
  const loaded = decodeDayNightForPage({ ...state, available: false, state: null });
  assert.deepEqual(buildDayNightUpdate({ ...loaded, mode: "day" }), { mode: "day" });
  const client = { postJson: async () => ({ status: "accepted", persistent: false }) } as unknown as ApiClient;
  await assert.rejects(daynight.save!(client, { mode: "day" }, loaded), /not confirmed/);
});


test("Raptor thresholds save only the active trigger and validate ordered values", () => {
  for (const [trigger, values] of Object.entries({
    luma: { night_luma: 22, night_gain: 90000, day_gain_pct: 30 },
    gain: { day_threshold: 100, night_threshold: 200 },
    adc: { adc_night: 200, adc_day: 600 },
    photo: { photo_ev_day: 10, photo_ev_night: 20, photo_ev_deep: 30 },
  })) {
    const loaded = decodeDayNightForPage({ ...state, thresholds: { trigger, supported: true, available: true, values, saved_values: values, matches_saved: true } });
    assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night", thresholds: { trigger, values } });
    assert.equal(daynight.validate!(buildDayNightUpdate(loaded), loaded), undefined);
    assert.throws(() => buildDayNightUpdate({ ...loaded, thresholds: { trigger, values: { ...values, extra: 1 } } }), /incomplete/);
    assert.throws(() => decodeDayNightForPage({ ...state, thresholds: { trigger, supported: true, available: true, values, saved_values: values, matches_saved: false } }), /inconsistent/);
  }
  assert.throws(() => decodeDayNightForPage({ ...state, thresholds: { trigger: "gain", supported: true, available: true,
    values: { day_threshold: 200, night_threshold: 100 }, saved_values: null, matches_saved: false } }), /whole numbers/);
  assert.throws(() => decodeDayNightForPage({ ...state, thresholds: { trigger: "luma", supported: true, available: true,
    values: { night_luma: 22.5, night_gain: 90000, day_gain_pct: 30 }, saved_values: null, matches_saved: false } }), /whole numbers/);
  const unsupported = decodeDayNightForPage({ ...state, thresholds: { trigger: "unsupported", supported: false, available: false, values: null, saved_values: null, matches_saved: false } });
  assert.deepEqual(buildDayNightUpdate(unsupported), { mode: "night" });
});

test("Raptor timing saves live sample and transition timing with independent saved evidence", () => {
  const timing = { supported: true, available: true, transition_delay_supported: true,
    values: { sample_interval_ms: 250, transition_delay_s: 7 },
    saved_values: { sample_interval_ms: 250, transition_delay_s: 7 }, matches_saved: true };
  const loaded = decodeDayNightForPage({ ...state, timing });
  assert.equal(loaded.daynight_timing_control, true);
  assert.equal(loaded.daynight_transition_delay_control, true);
  assert.deepEqual(buildDayNightUpdate(loaded), {
    mode: "night",
    timing: { sample_interval_ms: 250, transition_delay_s: 7 },
  });
  assert.throws(() => decodeDayNightForPage({ ...state, timing: { ...timing, matches_saved: false } }), /inconsistent/);
  assert.throws(() => decodeDayNightForPage({ ...state, timing: { ...timing,
    values: { sample_interval_ms: 250.5, transition_delay_s: 7 } } }), /whole numbers/);
  assert.throws(() => decodeDayNightForPage({ ...state, timing: { ...timing,
    values: { sample_interval_ms: 49, transition_delay_s: 7 } } }), /whole numbers/);
});

test("Raptor photo timing keeps sampling editable and reports transition delay unsupported", () => {
  const loaded = decodeDayNightForPage({ ...state, timing: {
    supported: true, available: true, transition_delay_supported: false,
    values: { sample_interval_ms: 100, transition_delay_s: null },
    saved_values: { sample_interval_ms: 100 }, matches_saved: true,
  } });
  assert.equal(loaded.daynight_timing_control, true);
  assert.equal(loaded.daynight_transition_delay_control, false);
  assert.equal(loaded.daynight_transition_delay_supported, false);
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night", timing: { sample_interval_ms: 100 } });
  assert.throws(() => decodeDayNightForPage({ ...state, timing: {
    supported: true, available: true, transition_delay_supported: false,
    values: { sample_interval_ms: 100, transition_delay_s: 2 },
    saved_values: { sample_interval_ms: 100 }, matches_saved: true,
  } }), /inconsistent/);
});

test("Raptor timing capability remains visible while invalid runtime timing disables editing", () => {
  const loaded = decodeDayNightForPage({ ...state, timing: {
    supported: true, available: false, transition_delay_supported: true,
    values: null, saved_values: { sample_interval_ms: 250, transition_delay_s: 7 }, matches_saved: false,
  } });
  assert.equal(loaded.daynight_timing_control, false);
  assert.equal(loaded.daynight_transition_delay_control, false);
  assert.equal(loaded.daynight_transition_delay_supported, true);
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night" });
});

test("Raptor fixed-time schedule follows the complete form validate and save path", () => {
  const schedule = { supported: true, available: true, active: true, target: "day",
    application_ok: true, values: { enabled: true, start_at: "07:00", stop_at: "19:00" },
    saved_values: { enabled: true, start_at: "07:00", stop_at: "19:00" }, matches_saved: true };
  const loaded = decodeDayNightForPage({ ...state, mode: "auto", state: "day", matches_saved: true, schedule });
  const update = buildDayNightUpdate(loaded);
  assert.deepEqual(update, { mode: "auto", schedule: { enabled: true, start_at: "07:00", stop_at: "19:00" } });
  assert.equal(daynight.validate!(update, loaded), undefined);
  assert.equal((decodeDayNightForPage({ ...state, mode: "auto", matches_saved: true, schedule: { ...schedule, application_ok: false } }).schedule as Record<string, unknown>).application_ok, false);
  const clockUnavailable = decodeDayNightForPage({ ...state, mode: "auto", matches_saved: true, schedule: { ...schedule, target: null, application_ok: false } });
  assert.equal((clockUnavailable.schedule as Record<string, unknown>).target, null);
  assert.equal((clockUnavailable.schedule as Record<string, unknown>).active, true);
  assert.throws(() => decodeDayNightForPage({ ...state, mode: "auto", matches_saved: true, schedule: { ...schedule, target: null, application_ok: true } }), /inconsistent/);
  assert.throws(() => buildDayNightUpdate({ ...loaded, schedule: { ...schedule, values: { enabled: true, start_at: "7:00", stop_at: "19:00" } } }), /incomplete/);
});

test("manual Raptor mode keeps a saved schedule visible but inactive", () => {
  const schedule = { supported: true, available: true, active: false, target: null,
    application_ok: true, values: { enabled: true, start_at: "20:00", stop_at: "06:00" },
    saved_values: { enabled: true, start_at: "20:00", stop_at: "06:00" }, matches_saved: true };
  const loaded = decodeDayNightForPage({ ...state, schedule });
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "night", schedule: schedule.values });
});

test("Raptor sunrise and sunset settings are local, persistent and mutually exclusive", () => {
  const schedule = { supported: true, available: true, active: false, target: null,
    application_ok: true, values: { enabled: false, start_at: "07:00", stop_at: "19:00" },
    saved_values: { enabled: false, start_at: "07:00", stop_at: "19:00" }, matches_saved: true };
  const values = { enabled: true, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 15, sunset_offset: -20 };
  const sun = { supported: true, available: true, active: true, target: "day", condition: "normal",
    application_ok: true, values, saved_values: values, matches_saved: true };
  const loaded = decodeDayNightForPage({ ...state, mode: "auto", state: "day", matches_saved: true, schedule, sun });
  assert.deepEqual(buildDayNightUpdate(loaded), { mode: "auto", schedule: schedule.values, sun: values });
  assert.equal(daynight.validate!(buildDayNightUpdate(loaded), loaded), undefined);
  assert.match(String(daynight.fields.find(field => field.path === "sun.values.enabled")?.description ?? ""), /camera/);

  assert.throws(() => buildDayNightUpdate({ ...loaded,
    schedule: { ...schedule, values: { ...schedule.values, enabled: true } } }), /either/);
  assert.throws(() => decodeDayNightForPage({ ...state, mode: "auto", state: "day", matches_saved: true,
    schedule: { ...schedule, values: { ...schedule.values, enabled: true }, saved_values: { ...schedule.values, enabled: true } }, sun }), /one automatic schedule/);
  assert.throws(() => decodeDayNightForPage({ ...state, mode: "auto", state: "day", matches_saved: true, schedule, sun: { ...sun,
    values: { ...values, latitude: Number.POSITIVE_INFINITY } } }), /invalid/);
  assert.throws(() => decodeDayNightForPage({ ...state, mode: "auto", state: "day", matches_saved: true, schedule, sun: { ...sun,
    values: { ...values, sunrise_offset: 1441 } } }), /invalid/);
});

test("Raptor solar clock failure remains visible and manual mode owns the camera", () => {
  const schedule = { supported: true, available: true, active: false, target: null,
    application_ok: true, values: { enabled: false, start_at: "07:00", stop_at: "19:00" },
    saved_values: null, matches_saved: false };
  const values = { enabled: true, latitude: 69.6492, longitude: 18.9553, sunrise_offset: 0, sunset_offset: 0 };
  const failed = decodeDayNightForPage({ ...state, mode: "auto", matches_saved: true, schedule,
    sun: { supported: true, available: true, active: true, target: null, condition: null,
      application_ok: false, values, saved_values: values, matches_saved: true } });
  assert.equal((failed.sun as Record<string, unknown>).application_ok, false);
  const manual = decodeDayNightForPage({ ...state, schedule,
    sun: { supported: true, available: true, active: false, target: null, condition: null,
      application_ok: true, values, saved_values: values, matches_saved: true } });
  assert.deepEqual(buildDayNightUpdate(manual), { mode: "night", schedule: schedule.values, sun: values });
});
