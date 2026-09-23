import assert from "node:assert/strict";
import test from "node:test";
import { audio, buildAudioUpdate } from "../src/pages/config/media";

function audioResponse(micMuted: boolean | null) {
  const values = { mic_vol: 30, mic_gain: 20, mic_alc_gain: 2, spk_vol: 40, spk_gain: 10 };
  return {
    source: "raptor", mic_enabled: true, spk_enabled: true, mic_muted: micMuted,
    mic_format: "PCM", mic_sample_rate: 16000, input_readback: "owner", processing_readback: "owner",
    codecs_built: { PCM: true, G711A: true, G711U: true, AAC: false, OPUS: false },
    effects_built: false, processing_available: false, mic_noise_suppression: null,
    mic_agc_enabled: null, mic_high_pass_filter: null, mic_agc_target_level_dbfs: null,
    mic_agc_compression_gain_db: null,
    mic_is_digital: false, mic_input_basis: "dlink-a1-profile-amic",
    force_stereo: false, channel_basis: "rad-fixed-mono",
    buffer_warn_frames: null, buffer_cap_frames: null, buffer_control: "unsupported-prudynt-queue-policy",
    tap_enabled: null, tap_path: null, tap_control: "unsupported",
    levels: Object.fromEntries(Object.entries(values).map(([name, value]) => [name, {
      supported: true, available: true, value, min: name.endsWith("gain") ? 0 : -30,
      max: name === "mic_alc_gain" ? 7 : name.endsWith("gain") ? 31 : 120,
    }])), ...values,
  };
}

test("audio retry includes unchanged live levels and excludes unsupported controls", () => {
  const loaded = { audio: { source: "raptor", mic_vol: 30, mic_gain: 20, levels: {
    mic_vol: { supported: true, available: true }, mic_gain: { supported: true, available: true },
    spk_vol: { supported: true, available: false }, spk_gain: { supported: false, available: false },
  } } };
  assert.deepEqual(buildAudioUpdate({ audio: { mic_vol: 30, mic_gain: 20, mic_enabled: true, spk_vol: 50, spk_gain: 10 } }, loaded), { audio: { mic_enabled: true, mic_vol: 30, mic_gain: 20 } });
  assert.deepEqual(buildAudioUpdate({ audio: { mic_enabled: true, mic_vol: 30 } }, { audio: { mic_enabled: false } }), { audio: { mic_enabled: true, mic_vol: 30 } });
});


test("all unavailable audio controls reject saving before POST", async () => {
  const levels = Object.fromEntries(["mic_vol", "mic_gain", "spk_vol", "spk_gain"].map((name) => [name, { supported: true, available: false }]));
  const loaded = { audio: { source: "raptor", levels } };
  let calls = 0;
  const client = { postJson: async () => { calls++; } } as unknown as import("../src/api/client").ApiClient;
  await assert.rejects(audio.save!(client, { audio: {} }, loaded), /No editable audio settings/);
  assert.equal(calls, 0);
});


test("Raptor input disable omits active input settings but preserves speaker settings", () => {
  const loaded = { audio: { source: "raptor", mic_enabled: true, mic_muted: false, processing_available: true,
    codecs_built: { PCM: true }, levels: { mic_alc_gain: { supported: true, available: true }, spk_vol: { supported: true, available: true } } } };
  assert.deepEqual(buildAudioUpdate({ audio: { mic_enabled: false, mic_format: "PCM", mic_alc_gain: 5, mic_agc_enabled: true, spk_vol: 30 } }, loaded), { audio: { mic_enabled: false, spk_vol: 30 } });
});

test("Raptor processing profile and built codec survive an explicit save retry", () => {
  const loaded = { audio: { source: "raptor", mic_enabled: true, mic_muted: false, processing_available: true,
    codecs_built: { PCM: true, AAC: false }, levels: {} } };
  const requested = { mic_enabled: true, mic_format: "PCM", mic_noise_suppression: 4, mic_agc_enabled: true, mic_agc_target_level_dbfs: 12, mic_agc_compression_gain_db: 20, mic_high_pass_filter: true };
  assert.deepEqual(buildAudioUpdate({ audio: requested }, loaded), { audio: requested });
  assert.deepEqual(buildAudioUpdate({ audio: { mic_format: "AAC" } }, loaded), { audio: {} });
});

test("Raptor speaker enable persists independently of microphone mute", () => {
  assert.deepEqual(audio.fields.find((field) => field.path === "audio.spk_enabled")?.enabledWhen,
    { path: "audio_output_control", equals: true });
  const loaded = { audio: { source: "raptor", mic_enabled: true, spk_enabled: true, mic_muted: true,
    processing_available: false, codecs_built: { PCM: true }, levels: {
      mic_vol: { supported: true, available: true }, spk_vol: { supported: true, available: true },
    } } };
  assert.deepEqual(buildAudioUpdate({ audio: { mic_enabled: true, spk_enabled: true, mic_vol: 25, spk_vol: 30 } }, loaded),
    { audio: { mic_enabled: true, spk_enabled: true, spk_vol: 30 } });
  assert.deepEqual(buildAudioUpdate({ audio: { mic_enabled: true, spk_enabled: false, spk_vol: 30 } }, loaded),
    { audio: { mic_enabled: true, spk_enabled: false } });
  assert.deepEqual(buildAudioUpdate({ audio: { spk_enabled: true } }, { audio: { ...loaded.audio, spk_enabled: null } }),
    { audio: {} });
});

test("Raptor microphone mute disables only input levels without hiding capability", async () => {
  for (const muted of [true, null]) {
    const loaded = await audio.load!({ json: async () => audioResponse(muted) } as unknown as import("../src/api/client").ApiClient);
    const ranges = loaded.audio_ranges as Record<string, { supported: boolean; available: boolean }>;
    for (const name of ["mic_vol", "mic_gain", "mic_alc_gain"]) {
      assert.equal(ranges[name]?.supported, true);
      assert.equal(ranges[name]?.available, false);
    }
    assert.equal(ranges.spk_vol?.available, true);
    assert.equal(ranges.spk_gain?.available, true);
  }
  const loaded = await audio.load!({ json: async () => audioResponse(false) } as unknown as import("../src/api/client").ApiClient);
  const ranges = loaded.audio_ranges as Record<string, { available: boolean }>;
  assert.equal(ranges.mic_vol?.available, true);
  assert.equal(ranges.mic_gain?.available, true);
  assert.equal(ranges.mic_alc_gain?.available, true);
  for (const name of ["mic_vol", "mic_gain", "mic_alc_gain"]) {
    assert.match(audio.fields.find((field) => field.path === `audio.${name}`)?.description ?? "", /Clear microphone mute/);
  }
});

test("Raptor classifies fixed topology separately from unsupported Prudynt controls", async () => {
  const loaded = await audio.load!({ json: async () => audioResponse(false) } as unknown as import("../src/api/client").ApiClient);
  assert.equal((loaded.audio as Record<string, unknown>).mic_is_digital, false);
  assert.equal((loaded.audio as Record<string, unknown>).force_stereo, false);
  for (const name of ["buffer_warn_frames", "buffer_cap_frames", "tap_enabled", "tap_path"]) {
    assert.equal((loaded.audio as Record<string, unknown>)[name], null);
  }
  for (const name of ["mic_is_digital", "force_stereo", "buffer_warn_frames", "buffer_cap_frames", "tap_enabled", "tap_path"]) {
    assert.match(audio.fields.find((field) => field.path === `audio.${name}`)?.description ?? "", /^Raptor mode:/);
  }
  assert.deepEqual(buildAudioUpdate({ audio: {
    mic_is_digital: false, force_stereo: false, buffer_warn_frames: null,
    buffer_cap_frames: null, tap_enabled: null, tap_path: null,
  } }, loaded), { audio: {} });
});
