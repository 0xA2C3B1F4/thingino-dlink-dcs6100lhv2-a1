import assert from "node:assert/strict";
import test from "node:test";
import { buildImagingRequests, imaging } from "../src/pages/config/media";
import type { ApiClient } from "../src/api/client";
import type { JsonValue } from "../src/api/contracts";
import { imageFixture, imagingRuntimeFixture } from "./support/fixtures";

function runtime(available = true, setterConfig = false, wbMode = 1) {
  return { code: 200, result: "success", source: "raptor", persistent: true, message: { fields: {
    brightness: { supported: true, available, value: available ? 140 : null, min: 0, max: 255 },
    contrast: { supported: true, available, value: available ? 141 : null, min: 0, max: 255 },
    saturation: { supported: true, available, value: available ? 142 : null, min: 0, max: 255 },
    sharpness: { supported: true, available, value: available ? 143 : null, min: 0, max: 255 },
    backlight: { supported: true, available, value: available ? 4 : null, min: 0, max: 10 },
    wide_dynamic_range: { supported: true, available, value: available ? 150 : null, min: 0, max: 255 },
    tone: { supported: true, available, value: available ? 20 : null, min: 0, max: 255 },
    defog: { supported: true, available, value: available ? 130 : null, min: 0, max: 255 },
    noise_reduction: { supported: true, available, value: available ? 110 : null, min: 0, max: 255,
      ...(setterConfig ? { verification: "sdk-setter-config", observed_value: null, configured_value: 110 } : {}) },
    hue: { supported: true, available, value: available ? 135 : null, min: 0, max: 255 },
    dpc_strength: { supported: true, available, value: available ? 90 : null, min: 0, max: 255 },
    exposure_compensation: { supported: true, available, value: available ? 145 : null, min: 0, max: 255 },
    hflip: { supported: true, available, value: available ? 1 : null, min: 0, max: 1 },
    vflip: { supported: true, available, value: available ? 0 : null, min: 0, max: 1 },
    anti_flicker: { supported: true, available, value: available ? 1 : null, min: 0, max: 2,
      verification: "sdk-readback", observed_value: available ? 1 : null, configured_value: 1,
      saved_value: 1, matches_saved: available },
  }, white_balance: {
    supported: true, available, verification: "sdk-readback", modes: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    gain_min: 0, gain_max: 1024, mode: available ? wbMode : null, gains_effective: available && wbMode === 1,
    rgain: available && wbMode === 1 ? 300 : null, bgain: available && wbMode === 1 ? 400 : null,
    configured_mode: wbMode, configured_rgain: 300, configured_bgain: 400,
    saved_mode: wbMode, saved_rgain: 300, saved_bgain: 400, matches_saved: available,
  } } };
}

test("Raptor image load and unchanged save use only the imaging route", async () => {
  const calls: string[] = [];
  const client = {
    json: async (route: string) => { calls.push(route); return runtime(); },
    postJson: async (route: string, body: unknown) => {
      calls.push(route);
      assert.deepEqual(body, {
        brightness: 140,
        contrast: 141,
        saturation: 142,
        sharpness: 143,
        backlight: 4,
        wide_dynamic_range: 150,
        tone: 20,
        defog: 130,
        noise_reduction: 110,
        hue: 135,
        dpc_strength: 90,
        exposure_compensation: 145,
        hflip: 1,
        vflip: 0,
        anti_flicker: 1,
        white_balance: { mode: 1, rgain: 300, bgain: 400 },
      });
      return runtime();
    },
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  assert.equal(loaded.image_persistent, true);
  assert.equal((loaded.image as Record<string, unknown>).backlight_compensation, 4);
  assert.equal((loaded.image as Record<string, unknown>).drc_strength, 150);
  assert.equal((loaded.image as Record<string, unknown>).highlight_depress, 20);
  assert.equal((loaded.image as Record<string, unknown>).defog_strength, 130);
  assert.equal((loaded.image as Record<string, unknown>).sinter_strength, 110);
  assert.equal((loaded.image as Record<string, unknown>).hflip, true);
  assert.equal((loaded.image as Record<string, unknown>).vflip, false);
  assert.equal((loaded.image as Record<string, unknown>).ae_compensation, 145);
  assert.equal((loaded.image as Record<string, unknown>).anti_flicker, 1);
  assert.equal(loaded.image_antiflicker_status, "The live anti-flicker mode matches the saved setting.");
  assert.equal((loaded.image as Record<string, unknown>).core_wb_mode, 1);
  assert.equal((loaded.image as Record<string, unknown>).wb_rgain, 300);
  await imaging.save!(client, loaded, loaded);
  assert.deepEqual(calls, ["/api/v1/imaging", "/api/v1/imaging"]);
});

test("Raptor white balance enables manual gains from the draft mode and rejects ignored preset gain edits", async () => {
  const client = {
    json: async () => runtime(true, false, 2),
    postJson: async () => runtime(true, false, 2),
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  const red = imaging.fields.find(field => field.path === "image.wb_rgain")!;
  assert.equal(typeof red.enabledWhen, "function");
  if (typeof red.enabledWhen === "function") {
    const preset: Record<string, JsonValue> = { image_white_balance_control: true, "image.core_wb_mode": 2 };
    const manual: Record<string, JsonValue> = { image_white_balance_control: true, "image.core_wb_mode": 1 };
    assert.equal(red.enabledWhen(path => preset[path]), false);
    assert.equal(red.enabledWhen(path => manual[path]), true);
  }
  const ignored = structuredClone(loaded);
  (ignored.image as Record<string, unknown>).wb_rgain = 301;
  await assert.rejects(imaging.save!(client, ignored, loaded), /gains can change only while Manual mode/);
  const manual = structuredClone(loaded);
  Object.assign(manual.image as Record<string, unknown>, { core_wb_mode: 1, wb_rgain: 301, wb_bgain: 401 });
  const request = buildImagingRequests(manual, loaded);
  assert.deepEqual(request.live.white_balance, { mode: 1, rgain: 301, bgain: 401 });
});

test("Raptor anti-flicker keeps live and saved state distinct", async () => {
  const response = runtime();
  Object.assign(response.message.fields.anti_flicker, {
    value: 2, observed_value: 2, configured_value: 1, saved_value: 1, matches_saved: false,
  });
  const loaded = await imaging.load!({ json: async () => response } as unknown as ApiClient);
  assert.equal((loaded.image as Record<string, unknown>).anti_flicker, 2);
  assert.equal(loaded.image_antiflicker_status, "The live anti-flicker mode differs from the saved setting.");
  const next = structuredClone(loaded);
  (next.image as Record<string, unknown>).anti_flicker = 1;
  assert.equal(buildImagingRequests(next, loaded).live.anti_flicker, 1);

  response.message.fields.anti_flicker.matches_saved = true;
  await assert.rejects(
    imaging.load!({ json: async () => response } as unknown as ApiClient),
    /inconsistent saved state/,
  );
});

test("preset white-balance status does not blame the mode for a retained gain difference", async () => {
  const response = runtime(true, false, 2);
  response.message.white_balance.configured_rgain = 301;
  response.message.white_balance.matches_saved = false;
  const client = { json: async () => response } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  assert.equal(loaded.image_white_balance_status, "Live white-balance settings differ from saved settings.");
  assert.equal(loaded.image_white_balance_control, true);
});

test("unavailable white-balance configuration disables only its group", async () => {
  const response = structuredClone(runtime()) as Record<string, any>;
  Object.assign(response.message.white_balance, {
    configured_mode: null, configured_rgain: null, configured_bgain: null,
    saved_mode: null, saved_rgain: null, saved_bgain: null, matches_saved: false,
  });
  let posted: unknown;
  const client = {
    json: async () => response,
    postJson: async (_route: string, body: unknown) => { posted = body; return response; },
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  assert.equal(loaded.image_white_balance_control, false);
  assert.match(String(loaded.image_white_balance_status), /editing is disabled/);
  const mode = imaging.fields.find(field => field.path === "image.core_wb_mode")!;
  assert.deepEqual(mode.enabledWhen, { path: "image_white_balance_control", equals: true });
  const next = structuredClone(loaded);
  (next.image as Record<string, unknown>).brightness = 141;
  await imaging.save!(client, next, loaded);
  assert.equal((posted as Record<string, unknown>).brightness, 141);
  assert.equal(Object.hasOwn(posted as Record<string, unknown>, "white_balance"), false);
});

test("unavailable Raptor image controls reject saving without a POST", async () => {
  let posts = 0;
  const client = {
    json: async () => runtime(false),
    postJson: async () => { posts++; return runtime(false); },
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  await assert.rejects(imaging.save!(client, loaded, loaded), /No image controls are available/);
  assert.equal(posts, 0);
});

test("Raptor hue and DPC use the checked imaging route", async () => {
  const posts: Array<{ route: string; body: Record<string, unknown> }> = [];
  const client = {
    json: async () => runtime(),
    postJson: async (route: string, body: Record<string, unknown>) => {
      posts.push({ route, body });
      return runtime();
    },
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  assert.equal((loaded.image as Record<string, unknown>).anti_flicker, 1);
  const antiFlicker = imaging.fields.find(field => field.path === "image.anti_flicker")!;
  assert.equal(antiFlicker.visibleWhen, undefined);
  assert.equal(typeof antiFlicker.enabledWhen, "function");
  if (typeof antiFlicker.enabledWhen === "function") {
    assert.equal(antiFlicker.enabledWhen(path => path.endsWith(".supported") || path.endsWith(".available") ? true : undefined), true);
  }
  const next = structuredClone(loaded);
  Object.assign(next.image as Record<string, unknown>, { hue: 135, dpc_strength: 90 });
  await imaging.save!(client, next, loaded);
  const live = posts.find(post => post.route === "/api/v1/imaging");
  assert.ok(live);
  assert.equal(live.body.hue, 135);
  assert.equal(live.body.dpc_strength, 90);
  assert.equal(posts.some(post => post.route === "/api/v1/prudynt"), false);
  for (const name of ["hue", "dpc_strength"]) {
    const enabled = imaging.fields.find(field => field.path === `image.${name}`)!.enabledWhen;
    assert.equal(typeof enabled, "function");
    if (typeof enabled === "function") {
      assert.equal(enabled(path => path.endsWith(".supported") || path.endsWith(".available") ? true : undefined), true);
      assert.equal(enabled(() => undefined), false);
    }
  }
});


test("a persistent image page rejects a live-only save response", async () => {
  for (const persistent of [false]) {
    const calls: string[] = [];
    const client = {
      json: async () => runtime(),
      postJson: async (route: string) => {
        calls.push(route);
        return { ...runtime(), persistent };
      },
    } as unknown as ApiClient;
    const loaded = await imaging.load!(client);
    assert.equal(loaded.image_persistent, true);
    await assert.rejects(imaging.save!(client, loaded, loaded), /saving was not confirmed/);
    assert.deepEqual(calls, ["/api/v1/imaging"]);
  }
});

test("setter-acknowledged noise reduction stays editable without claiming live readback", async () => {
  const client = {
    json: async () => runtime(true, true),
  } as unknown as ApiClient;
  const loaded = await imaging.load!(client);
  assert.equal(loaded.image_noise_reduction_status, "The ISP setter accepted this configured value; this SDK cannot read the live value back.");
  assert.equal((loaded.image as Record<string, unknown>).sinter_strength, 110);
  const field = ((loaded.imaging_runtime as { fields: Record<string, Record<string, unknown>> }).fields.noise_reduction);
  assert.ok(field);
  assert.equal(field.verification, "sdk-setter-config");
  assert.equal(field.observed_value, null);
  assert.equal(field.configured_value, 110);

  const unacknowledgedClient = {
    json: async () => runtime(false, true),
  } as unknown as ApiClient;
  const unacknowledged = await imaging.load!(unacknowledgedClient);
  assert.equal(unacknowledged.image_noise_reduction_status, "The configured value has not been acknowledged by the ISP setter in this runtime.");
  assert.equal((unacknowledged.image as Record<string, unknown>).sinter_strength, undefined);
});
