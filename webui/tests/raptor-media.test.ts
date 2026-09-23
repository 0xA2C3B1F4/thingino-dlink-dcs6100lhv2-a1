import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient } from "../src/api/client";
import { ControlApi } from "../src/api/control";
import { decodeRuntimeMedia, decodeMotionRuntime } from "../src/api/decode";

const media = {
  streams: {
    ch0: { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=0", width: 1920, height: 1080, fps: 15, format: "H264", rtsp_endpoint: "stream0" },
    ch1: { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=1", width: 640, height: 360, fps: 15, format: "H264", rtsp_endpoint: "stream1" },
  },
  rtsp: { port: 8554 },
};

test("Preview metadata uses only the canonical runtime response", async () => {
  const paths: string[] = [];
  const api = new ControlApi(new ApiClient({ fetchImpl: async (input) => {
    paths.push(String(input));
    assert.equal(String(input), "/api/v1/runtime/media");
    return new Response(JSON.stringify(media), { headers: { "content-type": "application/json" } });
  } }));
  const result = await api.media();
  assert.deepEqual(paths, ["/api/v1/runtime/media"]);
  assert.equal(result.stream0.width, 1920);
  assert.equal(result.stream1.width, 640);
  assert.equal(result.rtsp?.port, 8554);
});

test("legacy runtime payloads remain accepted without guessed stream settings", () => {
  const stream = { available: false, enabled: false, snapshot_url: null };
  assert.deepEqual(decodeRuntimeMedia({ streams: { ch0: stream, ch1: stream } }), { streams: { ch0: stream, ch1: stream } });
  const { rtsp: _rtsp, ...withoutRtsp } = media;
  assert.equal(decodeRuntimeMedia(withoutRtsp).rtsp, undefined);
  assert.throws(() => decodeRuntimeMedia({ ...media, rtsp: { port: 0 } }), /port/);
});

test("Raptor motion capability is independent of current monitoring", () => {
  const motion = { version: 1, source: "raptor", available: true, supported: true, monitoring: false, active: false, receiving: false };
  assert.deepEqual(decodeMotionRuntime(motion), motion);
  assert.throws(() => decodeMotionRuntime({ ...motion, monitoring: null }), /monitoring/);
});

test("Raptor stream GOP decoder keeps missing and malformed observations distinct", async () => {
  const { decodeRaptorStream } = await import("../src/api/decode");
  const value = { source: "raptor", persistent: true, stream_id: 0, supported: true, available: true, gop: 60, saved_gop: 30, matches_saved: false };
  assert.deepEqual(decodeRaptorStream(value, 0), value);
  for (const update of [{ stream_id: 1 }, { gop: 0 }, { gop: 65536 }, { gop: 1.5 }, { gop: null }, { supported: false }, { matches_saved: true }, { bitrate: 1000 }]) assert.throws(() => decodeRaptorStream({ ...value, ...update }, 0));
  assert.equal(decodeRaptorStream({ ...value, available: false, gop: null }, 0).gop, null);
});

test("Raptor substream enable keeps active, saved and owner blockers distinct", async () => {
  const { decodeRaptorStreamEnable } = await import("../src/api/decode");
  const pending = {
    supported: true, available: true, editable: true, required: false,
    active_enabled: true, configured_enabled: false, saved_available: true,
    saved_enabled: false, configured_matches_saved: true, pending_restart: true,
    motion_blocks_disable: false, recorder_blocks_disable: false,
    recorder_state_known: true, recorder_active_blocks_disable: false,
    apply: "full-camera-restart",
  };
  assert.deepEqual(decodeRaptorStreamEnable(pending, 1), pending);
  const savedUnknown = {
    ...pending,
    saved_available: false,
    saved_enabled: null,
    configured_matches_saved: false,
    pending_restart: null,
  };
  assert.deepEqual(decodeRaptorStreamEnable(savedUnknown, 1), savedUnknown);
  assert.throws(() => decodeRaptorStreamEnable({ ...pending, pending_restart: false }, 1));
  assert.throws(() => decodeRaptorStreamEnable({ ...savedUnknown, pending_restart: false }, 1));
  assert.throws(() => decodeRaptorStreamEnable({ ...pending, editable: false }, 1));
  assert.throws(() => decodeRaptorStreamEnable({ ...pending, recorder_state_known: null }, 1));

  const { buildStreamUpdate } = await import("../src/pages/config/media");
  const loaded = {
    stream_full_controls: false,
    stream0_enable_control: false,
    stream1_enable_control: true,
    stream0: { enabled: true, enable_control: { saved_enabled: true } },
    stream1: { enabled: true, enable_control: { saved_enabled: true } },
  };
  assert.deepEqual(buildStreamUpdate({ stream0: { enabled: false }, stream1: { enabled: false } }, loaded), {
    stream1: { enabled: false },
  });
  const retryLoaded = {
    ...loaded,
    stream1: {
      enabled: true,
      enable_control: {
        saved_available: true,
        saved_enabled: true,
        configured_enabled: false,
        configured_matches_saved: false,
      },
    },
  };
  assert.deepEqual(buildStreamUpdate({ stream1: { enabled: false } }, retryLoaded), {
    stream1: { enabled: false },
  });
  assert.deepEqual(buildStreamUpdate({ stream1: { enabled: true } }, retryLoaded), {
    stream1: { enabled: true },
  });
  assert.deepEqual(buildStreamUpdate({ stream1: { enabled: false, gop: 45 } }, retryLoaded), {
    stream1: { enabled: false },
  });
  const unavailableSaved = {
    ...loaded,
    stream1: {
      enabled: true,
      enable_control: {
        saved_available: false,
        saved_enabled: null,
        configured_enabled: true,
        configured_matches_saved: false,
      },
    },
  };
  assert.deepEqual(buildStreamUpdate({ stream1: { enabled: true } }, unavailableSaved), {});
});

test("Raptor stream audio decoder and save transform keep both consumers checked", async () => {
  const { decodeRaptorStream, decodeRaptorStreamAudio } = await import("../src/api/decode");
  const audio = {
    supported: true, available: true, editable: true, apply: "full-camera-restart",
    selection_required: false, legacy_selection_pending: false,
    configured_enabled: false, saved_available: true, saved_enabled: false,
    saved_readback_available: true,
    configured_matches_saved: true, active_available: true, active_enabled: false,
    pending_restart: false, rsd_active_enabled: false, rmr_required: true,
    rmr_active_enabled: false,
    rsd_source_available: true, rmr_source_available: true,
  };
  assert.deepEqual(decodeRaptorStreamAudio(audio), audio);
  const envelope = {
    source: "raptor", persistent: true, stream_id: 0, supported: true, available: true,
    gop: 30, saved_gop: 30, matches_saved: true, audio_control: audio,
  };
  assert.deepEqual(decodeRaptorStream(envelope, 0), envelope);
  const ownersUnknown = {
    ...audio, editable: false, active_available: false, active_enabled: null,
    pending_restart: null, rmr_active_enabled: true,
  };
  assert.deepEqual(decodeRaptorStreamAudio(ownersUnknown), ownersUnknown);
  const savedUnknown = {
    ...audio, editable: false, saved_readback_available: false,
    saved_available: false, saved_enabled: null,
    configured_matches_saved: null, pending_restart: null,
  };
  assert.deepEqual(decodeRaptorStreamAudio(savedUnknown), savedUnknown);
  const coldSub = {
    ...audio, rmr_required: false, rmr_active_enabled: null, rmr_source_available: null,
  };
  assert.deepEqual(decodeRaptorStreamAudio(coldSub), coldSub);
  assert.throws(() => decodeRaptorStreamAudio({ ...audio, active_available: false }));
  assert.throws(() => decodeRaptorStreamAudio({ ...audio, apply: "live" }));

  const selectionRequired = {
    ...audio,
    selection_required: true,
    configured_enabled: null,
    saved_available: false,
    saved_enabled: null,
    configured_matches_saved: null,
    active_available: false,
    active_enabled: null,
    pending_restart: null,
    rsd_active_enabled: true,
    rmr_active_enabled: false,
  };
  assert.deepEqual(decodeRaptorStreamAudio(selectionRequired), selectionRequired);

  const { buildStreamUpdate } = await import("../src/pages/config/media");
  const loaded = {
    stream_full_controls: false,
    stream0_audio_control: true,
    stream1_audio_control: true,
    stream0: { audio_enabled: false, audio_control: audio },
    stream1: { audio_enabled: false, audio_control: audio },
  };
  assert.deepEqual(buildStreamUpdate({
    stream0: { audio_enabled: true }, stream1: { audio_enabled: false },
  }, loaded), { stream0: { audio_enabled: true } });
  assert.deepEqual(buildStreamUpdate({
    stream0: { audio_enabled: false }, stream1: { audio_enabled: true },
  }, loaded), { stream1: { audio_enabled: true } });
  assert.deepEqual(buildStreamUpdate({ stream0: { audio_enabled: true }, stream1: { audio_enabled: false } }, {
    ...loaded, stream0_audio_control: false,
  }), {});
  const retry = {
    ...loaded,
    stream0: { audio_enabled: true, audio_control: {
      ...audio, configured_enabled: true, saved_enabled: false, configured_matches_saved: false,
      pending_restart: false,
    } },
  };
  assert.deepEqual(buildStreamUpdate({ stream0: { audio_enabled: true }, stream1: { audio_enabled: false } }, retry), {
    stream0: { audio_enabled: true },
  });
  assert.deepEqual(buildStreamUpdate({ stream0: { audio_enabled: false }, stream1: { audio_enabled: false } }, retry), {
    stream0: { audio_enabled: false },
  });
  const legacyLoaded = {
    ...loaded,
    stream0: { audio_enabled: null, audio_control: selectionRequired },
  };
  assert.deepEqual(buildStreamUpdate({
    stream0: { audio_enabled: null }, stream1: { audio_enabled: false },
  }, legacyLoaded), {});
  assert.deepEqual(buildStreamUpdate({
    stream0: { audio_enabled: null, gop: 45 }, stream1: { audio_enabled: false },
  }, {
    ...legacyLoaded,
    stream0_gop_control: true,
    stream0: { ...legacyLoaded.stream0, gop: 30 },
  }), { stream0: { gop: 45 } });
  assert.deepEqual(buildStreamUpdate({
    stream0: { audio_enabled: false }, stream1: { audio_enabled: false },
  }, legacyLoaded), { stream0: { audio_enabled: false } });
});

test("Raptor stream load retries configured enable state and permits an explicit disk rollback", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const stream = (id: number) => ({
    source: "raptor", persistent: true, stream_id: id,
    supported: true, available: true, gop: 30, saved_gop: 30, matches_saved: true,
    ...(id === 1 ? { enable_control: {
      supported: true, available: true, editable: true, required: false,
      active_enabled: true, configured_enabled: false,
      saved_available: true, saved_enabled: true,
      configured_matches_saved: false, pending_restart: false,
      motion_blocks_disable: false, recorder_blocks_disable: false,
      recorder_state_known: true, recorder_active_blocks_disable: false,
      apply: "full-camera-restart",
    } } : {}),
  });
  const replies = [stream(0), stream(1), {}];
  const client = new ApiClient({ fetchImpl: async () => new Response(
    JSON.stringify(replies.shift()),
    { headers: { "content-type": "application/json" } },
  ) });
  const loaded = await streams.load!(client);
  assert.equal((loaded.stream1 as Record<string, unknown>).enabled, false);
  assert.deepEqual(buildStreamUpdate(loaded, loaded), { stream1: { enabled: false } });

  const rollback = {
    ...loaded,
    stream1: { ...(loaded.stream1 as Record<string, unknown>), enabled: true },
  };
  assert.deepEqual(buildStreamUpdate(rollback, loaded), { stream1: { enabled: true } });

  const requests: unknown[] = [];
  const saveClient = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const enable = request.stream1?.enabled !== undefined;
    return new Response(JSON.stringify({
      status: "accepted", persistent: true,
      ...(enable ? { pending_restart: true } : {}),
    }), { headers: { "content-type": "application/json" } });
  } });
  const withGop = {
    ...loaded,
    stream0: { ...(loaded.stream0 as Record<string, unknown>), gop: 45 },
  };
  await streams.save!(saveClient, withGop, loaded);
  assert.deepEqual(requests, [
    { stream1: { enabled: false } },
    { stream0: { gop: 45 } },
  ]);
});

test("Raptor stream encoding decoder accepts only the checked mode and bitrate contract", async () => {
  const { decodeRaptorEncoding } = await import("../src/api/decode");
  const value = { supported: true, available: true, rc_mode: "VBR", bitrate: 3_000_000, saved_rc_mode: "VBR", saved_bitrate: 3_000_000, matches_saved: true,
    bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"] };
  assert.deepEqual(decodeRaptorEncoding(value), value);
  assert.deepEqual(decodeRaptorEncoding({ supported: false, available: false }), { supported: false, available: false });
  for (const update of [{ rc_mode: "FIXQP" }, { bitrate: 3_000_001 }, { bitrate_min: 1 }, { modes: ["CBR", "VBR"] }, { matches_saved: false }, { saved_bitrate: null }, { supported: false, available: true }]) {
    assert.throws(() => decodeRaptorEncoding({ ...value, ...update }));
  }
  assert.throws(() => decodeRaptorEncoding({ ...value, available: false }));
});

test("Raptor FIXQP decoder keeps active and saved startup state distinct", async () => {
  const { decodeRaptorRcConfig } = await import("../src/api/decode");
  const pending = { supported: true, available: true, active_mode: "CBR", active_qp: null,
    saved_available: true, saved_mode: "FIXQP", saved_qp: 37, matches_saved: false, pending_restart: true,
    qp_min: 0, qp_max: 51, qp_default: 35, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"] };
  assert.deepEqual(decodeRaptorRcConfig(pending), pending);
  assert.deepEqual(decodeRaptorRcConfig({ ...pending, active_mode: "FIXQP", active_qp: 37,
    matches_saved: true, pending_restart: false }), { ...pending, active_mode: "FIXQP", active_qp: 37,
    matches_saved: true, pending_restart: false });
  assert.deepEqual(decodeRaptorRcConfig({ supported: false, available: false }), { supported: false, available: false });
  for (const update of [{ active_qp: 37 }, { saved_qp: 52 }, { saved_mode: "SMART" }, { qp_min: 1 },
    { modes: ["CBR", "FIXQP"] }, { matches_saved: true }, { pending_restart: false }]) {
    assert.throws(() => decodeRaptorRcConfig({ ...pending, ...update }));
  }
});

test("Raptor GOP mode decoder keeps SMARTP distinct from SMART rate control", async () => {
  const { decodeRaptorGopMode } = await import("../src/api/decode");
  const pending = { supported: true, available: true, active_mode: "DEFAULT",
    saved_available: true, saved_mode: "SMARTP", matches_saved: false, pending_restart: true,
    modes: ["DEFAULT", "PYRAMIDAL", "SMARTP"] };
  assert.deepEqual(decodeRaptorGopMode(pending), pending);
  assert.deepEqual(decodeRaptorGopMode({ supported: false, available: false }), { supported: false, available: false });
  for (const update of [{ active_mode: "SMART" }, { saved_mode: "SMART" }, { modes: ["DEFAULT", "SMARTP"] },
    { matches_saved: true }, { pending_restart: false }]) {
    assert.throws(() => decodeRaptorGopMode({ ...pending, ...update }));
  }
});

test("Raptor buffer decoder reports fixed profile state without an editable range", async () => {
  const { decodeRaptorBuffers } = await import("../src/api/decode");
  const value = { supported: true, available: true, editable: false, active_buffers: 1,
    configured_buffers: 1, saved_available: true, saved_buffers: 1,
    active_matches_configured: true, configured_matches_saved: true,
    profile: "dcs6100lhv2-a1-42m-22m-v1", profile_required_buffers: 1,
    profile_admitted: true, hardware_limit_known: false };
  assert.deepEqual(decodeRaptorBuffers(value), value);
  assert.deepEqual(decodeRaptorBuffers({ supported: false, available: false, editable: false }),
    { supported: false, available: false, editable: false });
  assert.deepEqual(decodeRaptorBuffers({ supported: null, available: false, editable: false }),
    { supported: null, available: false, editable: false });
  for (const update of [
    { editable: true }, { hardware_limit_known: true }, { active_buffers: 2 },
    { saved_buffers: 2 }, { profile_required_buffers: 2 }, { profile_admitted: false },
  ]) assert.throws(() => decodeRaptorBuffers({ ...value, ...update }));
  assert.deepEqual(decodeRaptorBuffers({ ...value, active_buffers: 2,
    active_matches_configured: false, profile_admitted: false }),
  { ...value, active_buffers: 2, active_matches_configured: false, profile_admitted: false });

  const { streams } = await import("../src/pages/config/media");
  assert.equal(streams.fields.find(field => field.path === "stream0.buffers")?.description,
    "Use 1–65535 while enabled; -1 is reserved for a disabled stream.");
});

test("Raptor stream codec decoder requires an exact checked capability", async () => {
  const { decodeRaptorCodec } = await import("../src/api/decode");
  const value = { supported: true, available: true, recovery_required: false, codec: "H264", saved_codec: "H264", matches_saved: true, codecs: ["H264", "H265"] };
  assert.deepEqual(decodeRaptorCodec(value), value);
  assert.deepEqual(decodeRaptorCodec({ supported: false, available: false }), { supported: false, available: false });
  for (const update of [{ codecs: ["H265"] }, { codec: "H266" }, { available: false }, { recovery_required: true }, { matches_saved: false }, { supported: false }]) {
    assert.throws(() => decodeRaptorCodec({ ...value, ...update }));
  }
});

test("Raptor H.264 profile decoder requires the exact checked capability", async () => {
  const { decodeRaptorProfile } = await import("../src/api/decode");
  const value = { supported: true, available: true, recovery_required: false, profile: 2, saved_profile: 1, matches_saved: false, profiles: [0, 1, 2] };
  assert.deepEqual(decodeRaptorProfile(value), value);
  assert.deepEqual(decodeRaptorProfile({ supported: false, available: false }), { supported: false, available: false });
  for (const update of [{ profiles: [0, 1] }, { profile: 3 }, { profile: 1.5 }, { available: false }, { recovery_required: true }, { matches_saved: true }, { supported: false }]) {
    assert.throws(() => decodeRaptorProfile({ ...value, ...update }));
  }
});

test("Raptor stream load accepts a native unsupported codec and disables only its control", async () => {
  const { decodeRaptorStream, decodeRaptorCodec } = await import("../src/api/decode");
  const unsupported = { supported: false, available: false };
  assert.deepEqual(decodeRaptorCodec(unsupported), unsupported);
  const stream = {
    source: "raptor", persistent: true, stream_id: 0, supported: true, available: true,
    gop: 30, saved_gop: 30, matches_saved: true, codec_control: unsupported,
  };
  assert.deepEqual(decodeRaptorStream(stream, 0), stream);
});

test("Raptor geometry decoder keeps saved and active values distinct", async () => {
  const { decodeRaptorGeometry } = await import("../src/api/decode");
  const pending = { supported: true, available: true, saved_available: true, profile: "dcs6100lhv2-a1-42m-22m-v1",
    active_width: 640, active_height: 360, saved_width: 320, saved_height: 180,
    matches_saved: false, pending_restart: true };
  assert.deepEqual(decodeRaptorGeometry(pending), pending);
  assert.deepEqual(decodeRaptorGeometry({ supported: false, available: false }), { supported: false, available: false });
  assert.deepEqual(decodeRaptorGeometry({ ...pending, available: false, active_width: null,
    active_height: null, matches_saved: false, pending_restart: false }),
  { ...pending, available: false, active_width: null, active_height: null, matches_saved: false, pending_restart: false });
  for (const update of [
    { active_width: 641 }, { saved_height: null }, { matches_saved: true },
    { pending_restart: false }, { profile: "generic" }, { saved_available: false },
    { available: false, active_width: null, pending_restart: false },
    { available: false, active_height: null, pending_restart: false },
    { saved_available: false, saved_width: null, pending_restart: false },
    { saved_available: false, saved_height: null, pending_restart: false },
  ]) assert.throws(() => decodeRaptorGeometry({ ...pending, ...update }));
});

test("Raptor geometry save preserves the draft and reports restart-only state", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const geometry = (width: number, height: number) => ({ supported: true, available: true, saved_available: true,
    profile: "dcs6100lhv2-a1-42m-22m-v1", active_width: width, active_height: height,
    saved_width: width, saved_height: height, matches_saved: true, pending_restart: false });
  const loaded = { stream_full_controls: false, stream0_geometry_control: true, stream1_geometry_control: true,
    stream0_gop_control: true,
    stream0: { width: 1920, height: 1080, active_width: 1920, active_height: 1080, gop: 30, matches_saved: true, geometry_control: geometry(1920, 1080) },
    stream1: { width: 640, height: 360, active_width: 640, active_height: 360, geometry_control: geometry(640, 360) } };
  const value = { stream0: { width: 1920, height: 1080, gop: 45 }, stream1: { width: 320, height: 180 } };
  assert.deepEqual(buildStreamUpdate(value, loaded), { stream0: { gop: 45 }, stream1: { width: 320, height: 180 } });
  assert.equal(streams.validate?.(value, loaded), undefined);
  assert.match(streams.validate?.({ stream0: { width: 1280, height: 720, gop: 45 }, stream1: { width: 640, height: 360 } }, loaded) ?? "", /one third/);
  assert.match(streams.validate?.({ stream0: { width: 1920, height: 1080, gop: 45 }, stream1: { width: 321, height: 180 } }, loaded) ?? "", /outside the admitted/);
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const body = JSON.parse(String(init?.body));
    requests.push(body);
    return new Response(JSON.stringify({ status: "accepted", persistent: true, ...(body.stream1?.width ? { pending_restart: true } : {}) }), { headers: { "content-type": "application/json" } });
  } });
  await streams.save!(client, value, loaded);
  assert.deepEqual(requests, [{ stream0: { gop: 45 } }, { stream1: { width: 320, height: 180 } }]);
  assert.equal(value.stream0.gop, 45);
  assert.equal(value.stream1.width, 320);
  assert.equal((loaded.stream1.geometry_control as Record<string, unknown>).active_width, 640);
  assert.equal((loaded.stream1.geometry_control as Record<string, unknown>).saved_width, 320);
  assert.equal((loaded.stream1.geometry_control as Record<string, unknown>).pending_restart, true);
  assert.match(typeof streams.successMessage === "function" ? streams.successMessage(loaded) : "", /full camera stack/);
  assert.deepEqual(buildStreamUpdate(value, loaded), {});
});

test("Raptor stream load exposes only checked H.264 profile choices", async () => {
  const { streams } = await import("../src/pages/config/media");
  const stream = (id: number, format: "H264" | "H265", profile: number | null) => ({
    source: "raptor", persistent: true, stream_id: id, supported: true, available: true,
    gop: 30, saved_gop: 30, matches_saved: true, fps_control: fpsReceipt(id),
    encoding_control: { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000, saved_rc_mode: "CBR", saved_bitrate: 2_000_000,
      matches_saved: true, bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"] },
    rc_config_control: { supported: true, available: true, active_mode: "CBR", active_qp: null,
      saved_available: false, saved_mode: null, saved_qp: null, matches_saved: false, pending_restart: false,
      qp_min: 0, qp_max: 51, qp_default: 35, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"] },
    codec_control: { supported: true, available: true, recovery_required: false, codec: format, saved_codec: format, matches_saved: true, codecs: ["H264", "H265"] },
    profile_control: format === "H264"
      ? { supported: true, available: true, recovery_required: false, profile, saved_profile: profile, matches_saved: true, profiles: [0, 1, 2] }
      : { supported: false, available: false },
  });
  const replies = [stream(0, "H264", 2), stream(1, "H265", null)];
  const client = new ApiClient({ fetchImpl: async () => new Response(JSON.stringify(replies.shift()), { headers: { "content-type": "application/json" } }) });
  const loaded = await streams.load!(client);
  assert.equal((loaded.stream0 as Record<string, unknown>).profile, 2);
  assert.deepEqual(loaded.stream0_profiles, [0, 1, 2]);
  assert.equal(loaded.stream0_profile_control, true);
  assert.equal(loaded.stream0_rc_config_control, true);
  assert.equal((loaded.stream0 as Record<string, unknown>).mode, "CBR");
  assert.equal((loaded.stream0 as Record<string, unknown>).qp_init, 35);
  assert.equal((loaded.stream1 as Record<string, unknown>).profile, null);
  assert.deepEqual(loaded.stream1_profiles, []);
  assert.equal(loaded.stream1_profile_control, false);
});

test("Raptor stream reload blocks every mutable stream control during codec recovery", async () => {
  const { streams } = await import("../src/pages/config/media");
  const stream = (id: number) => ({
    source: "raptor", persistent: true, stream_id: id, supported: true, available: true,
    gop: 30, saved_gop: 30, matches_saved: true,
    fps_control: fpsReceipt(id),
    encoding_control: { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000, saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true, bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"] },
    codec_control: { supported: true, available: false, recovery_required: true, codec: null, saved_codec: "H264", matches_saved: false, codecs: ["H264", "H265"] },
  });
  const replies = [stream(0), stream(1)];
  const client = new ApiClient({ fetchImpl: async () => new Response(JSON.stringify(replies.shift()), { headers: { "content-type": "application/json" } }) });
  const loaded = await streams.load!(client);
  for (const name of ["stream0", "stream1"]) {
    for (const control of ["gop", "fps", "encoding", "codec", "profile"])
      assert.equal(loaded[`${name}_${control}_control`], false);
  }
});

test("Raptor stream saves rate control and bitrate as one checked operation", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const encoding = { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000, saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true };
  const loaded = { stream_full_controls: false, stream0_encoding_control: true, stream1_encoding_control: false,
    stream0_gop_control: true, stream0_fps_control: true, stream0: { mode: "CBR", bitrate: 2_000_000, gop: 30, fps: 15, fps_control: { persisted: true }, encoding_control: encoding } };
  const changed = { stream0: { mode: "VBR", bitrate: 3_000_000, gop: 60, fps: 15 } };
  assert.deepEqual(buildStreamUpdate(changed, loaded), { stream0: { gop: 60, mode: "VBR", bitrate: 3_000_000 } });
  assert.equal(streams.validate?.(changed, loaded), undefined);
  assert.match(streams.validate?.({ stream0: { mode: "FIXQP", bitrate: 3_000_000, gop: 30, fps: 15 } }, loaded) ?? "", /initial QP/);
  assert.match(streams.validate?.({ stream0: { mode: "VBR", bitrate: 3_000_001, gop: 30, fps: 15 } }, loaded) ?? "", /steps of 1000/);
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ status: "accepted", persistent: true }), { headers: { "content-type": "application/json" } });
  } });
  await streams.save!(client, changed, loaded);
  assert.deepEqual(requests, [{ stream0: { mode: "VBR", bitrate: 3_000_000 } }, { stream0: { gop: 60 } }]);
});

test("Raptor FIXQP save preserves bitrate and reports saved versus active state", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const rc = { supported: true, available: true, active_mode: "CBR", active_qp: null,
    saved_available: true, saved_mode: "CBR", saved_qp: 35, matches_saved: true, pending_restart: false,
    qp_min: 0, qp_max: 51, qp_default: 35, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"] };
  const encoding = { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000,
    saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true };
  const loaded = { stream_full_controls: false, stream0_rc_config_control: true, stream0_mode_control: true,
    stream0_encoding_control: true, stream0_gop_control: true,
    stream0: { mode: "CBR", active_mode: "CBR", qp_init: 35, bitrate: 2_000_000, gop: 30,
      matches_saved: true, rc_config_control: rc, encoding_control: encoding } };
  const value = { stream0: { mode: "FIXQP", qp_init: 37, bitrate: 2_000_000, gop: 45 } };
  assert.deepEqual(buildStreamUpdate(value, loaded), { stream0: { mode: "FIXQP", qp_init: 37, gop: 45 } });
  assert.equal(streams.validate?.(value, loaded), undefined);
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const fixqp = request.stream0?.qp_init !== undefined;
    return new Response(JSON.stringify({ status: "accepted", persistent: true, ...(fixqp ? { pending_restart: true } : {}) }), { headers: { "content-type": "application/json" } });
  } });
  await streams.save!(client, value, loaded);
  assert.deepEqual(requests, [{ stream0: { mode: "FIXQP", qp_init: 37 } }, { stream0: { gop: 45 } }]);
  assert.equal(loaded.stream0.mode, "FIXQP");
  assert.equal(loaded.stream0.bitrate, 2_000_000);
  assert.equal((loaded.stream0.rc_config_control as Record<string, unknown>).active_mode, "CBR");
  assert.equal((loaded.stream0.rc_config_control as Record<string, unknown>).saved_mode, "FIXQP");
  assert.equal((loaded.stream0.rc_config_control as Record<string, unknown>).pending_restart, true);
  assert.match(typeof streams.successMessage === "function" ? streams.successMessage(loaded) : "", /full camera stack/);
  assert.deepEqual(buildStreamUpdate(value, loaded), {});
});

test("Raptor GOP mode save stays pending and keeps live GOP length independent", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const mode = { supported: true, available: true, active_mode: "DEFAULT", saved_available: true,
    saved_mode: "DEFAULT", matches_saved: true, pending_restart: false, modes: ["DEFAULT", "PYRAMIDAL", "SMARTP"] };
  const loaded = { stream_full_controls: false, stream0_gop_control: true, stream0_gop_mode_control: true,
    stream0: { gop: 30, saved_gop: 30, matches_saved: true, gop_mode: "DEFAULT", gop_mode_control: mode } };
  const value = { stream0: { gop: 45, gop_mode: "SMARTP" } };
  assert.deepEqual(buildStreamUpdate(value, loaded), { stream0: { gop: 45, gop_mode: "SMARTP" } });
  assert.equal(streams.validate?.(value, loaded), undefined);
  assert.match(streams.validate?.({ stream0: { gop: 45, gop_mode: "SMART" } }, loaded) ?? "", /unavailable/);
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    return new Response(JSON.stringify({ status: "accepted", persistent: true,
      ...(request.stream0?.gop_mode ? { pending_restart: true } : {}) }), { headers: { "content-type": "application/json" } });
  } });
  await streams.save!(client, value, loaded);
  assert.deepEqual(requests, [{ stream0: { gop_mode: "SMARTP" } }, { stream0: { gop: 45 } }]);
  assert.equal((loaded.stream0.gop_mode_control as Record<string, unknown>).active_mode, "DEFAULT");
  assert.equal((loaded.stream0.gop_mode_control as Record<string, unknown>).saved_mode, "SMARTP");
  assert.equal((loaded.stream0.gop_mode_control as Record<string, unknown>).pending_restart, true);
  assert.equal(loaded.stream0.gop, 45);
  assert.deepEqual(buildStreamUpdate(value, loaded), {});
});

test("Raptor pending FIXQP survives unrelated stream drafts and exits by startup config", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const pending = { supported: true, available: true, active_mode: "CBR", active_qp: null,
    saved_available: true, saved_mode: "FIXQP", saved_qp: 37, matches_saved: false, pending_restart: true,
    qp_min: 0, qp_max: 51, qp_default: 35, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"] };
  const loaded = { stream_full_controls: false, stream0_rc_config_control: true, stream0_encoding_control: true, stream0_gop_control: true,
    stream0: { mode: "FIXQP", active_mode: "CBR", qp_init: 37, bitrate: 2_000_000, gop: 30, matches_saved: true,
      rc_config_control: pending, encoding_control: { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000,
        saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true } } };
  assert.deepEqual(buildStreamUpdate({ stream0: { mode: "FIXQP", qp_init: 37, bitrate: 2_000_000, gop: 45 } }, loaded), { stream0: { gop: 45 } });

  const activeFixqp = { ...pending, active_mode: "FIXQP", active_qp: 37, saved_mode: "FIXQP", matches_saved: true, pending_restart: false };
  const leaving = { ...loaded, stream0_encoding_control: false,
    stream0: { ...loaded.stream0, active_mode: "FIXQP", rc_config_control: activeFixqp } };
  const value = { stream0: { mode: "VBR", qp_init: 37, bitrate: 2_000_000, gop: 30 } };
  assert.deepEqual(buildStreamUpdate(value, leaving), { stream0: { mode: "VBR", qp_init: 37 } });
  assert.equal(streams.validate?.(value, leaving), undefined);
});

test("Raptor stream saves select only available GOP fields and require persistence receipt", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, stream0_gop_control: true, stream1_gop_control: false };
  const values = { stream0: { gop: 60, bitrate: 1000, enabled: false }, stream1: { gop: 30 } };
  assert.deepEqual(buildStreamUpdate(values, loaded), { stream0: { gop: 60 } });
  assert.deepEqual(buildStreamUpdate(values, { ...loaded, stream0_gop_control: false }), {});
  assert.equal(streams.validate?.(values, loaded), undefined);
  assert.match(streams.validate?.({ stream0: { gop: 1.5 } }, loaded) ?? "", /integer/);
  const client = new ApiClient({ fetchImpl: async () => new Response(JSON.stringify({ status: "accepted" }), { headers: { "content-type": "application/json" } }) });
  await assert.rejects(() => streams.save!(client, values, loaded), /not confirmed/);
});

const fpsReceipt = (id = 0, extra: Record<string, unknown> = {}) => ({ supported: true, available: true, status: "ok", stream_id: id, recovery_required: false, persistence_pending: false, live_applied: true, persisted: true, fps: 20,
  monitoring: { related: true, active: true, paused: false, receiving: true, thread_owned: true }, ...extra });

test("Raptor stream save sequences every edited operation without dropping drafts", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const encoding = (mode: string, bitrate: number) => ({ supported: true, available: true, rc_mode: mode, bitrate, saved_rc_mode: mode, saved_bitrate: bitrate, matches_saved: true,
    bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"] });
  const loaded = { stream_full_controls: false,
    stream0_gop_control: true, stream0_fps_control: true, stream0_encoding_control: true, stream0_codec_control: true, stream0_profile_control: true,
    stream1_gop_control: true, stream1_fps_control: true, stream1_encoding_control: true, stream1_codec_control: true, stream1_profile_control: true,
    stream0: { available: true, format: "H264", profile: 1, profile_control: { matches_saved: true }, codec_control: { matches_saved: true }, gop: 30, matches_saved: true, fps: 20, fps_control: fpsReceipt(), mode: "CBR", bitrate: 2_000_000, encoding_control: encoding("CBR", 2_000_000) },
    stream1: { available: true, format: "H264", profile: 1, profile_control: { matches_saved: true }, codec_control: { matches_saved: true }, gop: 30, matches_saved: true, fps: 20, fps_control: fpsReceipt(1), mode: "VBR", bitrate: 1_000_000, encoding_control: encoding("VBR", 1_000_000) } };
  const value = { stream0: { format: "H265", profile: 2, gop: 60, fps: 15, mode: "VBR", bitrate: 3_000_000 }, stream1: { format: "H264", profile: 2, gop: 45, fps: 10, mode: "CBR", bitrate: 1_500_000 } };
  assert.deepEqual(buildStreamUpdate(value, loaded), {
    stream0: { format: "H265", gop: 60, fps: 15, mode: "VBR", bitrate: 3_000_000 },
    stream1: { profile: 2, gop: 45, fps: 10, mode: "CBR", bitrate: 1_500_000 },
  });
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const fpsName = Object.keys(request).find((name) => request[name].fps !== undefined);
    if (fpsName) {
      const id = Number(fpsName.slice(-1));
      const fps = request[fpsName].fps;
      return new Response(JSON.stringify({ status: "accepted", persistent: true, fps_result: fpsReceipt(id, { fps }) }), { headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({ status: "accepted", persistent: true }), { headers: { "content-type": "application/json" } });
  } });
  await streams.save!(client, value, loaded);
  assert.deepEqual(requests, [
    { stream0: { fps: 15 } },
    { stream1: { fps: 10 } },
    { stream0: { format: "H265" } },
    { stream1: { profile: 2 } },
    { stream0: { mode: "VBR", bitrate: 3_000_000 }, stream1: { mode: "CBR", bitrate: 1_500_000 } },
    { stream0: { gop: 60 }, stream1: { gop: 45 } },
  ]);
  assert.equal(streams.fields.find((field) => field.path === "stream0.mode")?.optionsFrom, "stream0_encoding_modes");
  assert.equal(streams.fields.find((field) => field.path === "stream0.format")?.optionsFrom, "stream0_codecs");
  assert.equal(streams.fields.find((field) => field.path === "stream0.profile")?.optionsFrom, "stream0_profiles");
});

test("Raptor stream save stops after an unknown codec restart", async () => {
  const { streams, buildStreamUpdate } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, stream0_codec_control: true, stream0_profile_control: true, stream0_encoding_control: true,
    stream0_gop_control: true, stream0_fps_control: true,
    stream0: { available: true, format: "H264", codec_control: { matches_saved: true }, mode: "CBR", bitrate: 2_000_000, encoding_control: { matches_saved: true }, gop: 30, matches_saved: true, fps: 20, fps_control: fpsReceipt() } };
  const value = { stream0: { format: "H265", mode: "VBR", bitrate: 3_000_000, gop: 60, fps: 20 } };
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ status: "error", persistent: false }), { headers: { "content-type": "application/json" } });
  } });
  await assert.rejects(() => streams.save!(client, value, loaded), /codec result is unknown/i);
  assert.deepEqual(requests, [{ stream0: { format: "H265" } }]);
  assert.equal(loaded.stream0_codec_control, false);
  assert.equal(loaded.stream0_profile_control, false);
  assert.equal(loaded.stream0_encoding_control, false);
  assert.deepEqual(buildStreamUpdate(value, loaded), {});
});

test("Raptor stream profile failure keeps the draft and a successful retry is not replayed", async () => {
  const { streams, buildStreamUpdate } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, stream0_profile_control: true, stream0_encoding_control: true, stream0_gop_control: true,
    stream0: { available: true, format: "H264", profile: 1, profile_control: { matches_saved: true }, mode: "CBR", bitrate: 2_000_000,
      encoding_control: { matches_saved: true }, gop: 30, matches_saved: true } };
  const value = { stream0: { format: "H264", profile: 2, mode: "VBR", bitrate: 3_000_000, gop: 60 } };
  const requests: unknown[] = [];
  let failProfile = true;
  let failGop = false;
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const accepted = !(failProfile && request.stream0?.profile !== undefined) && !(failGop && request.stream0?.gop !== undefined);
    return new Response(JSON.stringify({ status: accepted ? "accepted" : "error", persistent: accepted }), { headers: { "content-type": "application/json" } });
  } });
  await assert.rejects(() => streams.save!(client, value, loaded), /profile result is unknown/i);
  assert.deepEqual(requests, [{ stream0: { profile: 2 } }]);
  assert.equal(value.stream0.profile, 2);
  assert.equal(loaded.stream0_profile_control, false);
  assert.deepEqual(buildStreamUpdate(value, loaded), {});

  loaded.stream0_profile_control = true;
  loaded.stream0_encoding_control = true;
  loaded.stream0_gop_control = true;
  failProfile = false;
  failGop = true;
  await assert.rejects(() => streams.save!(client, value, loaded), /GOP result is unknown/i);
  assert.deepEqual(requests.slice(1), [
    { stream0: { profile: 2 } },
    { stream0: { mode: "VBR", bitrate: 3_000_000 } },
    { stream0: { gop: 60 } },
  ]);
  requests.length = 0;
  assert.deepEqual(buildStreamUpdate(value, loaded), {});
});

test("Raptor H.265 streams do not emit an H.264 profile update", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, stream0_profile_control: true,
    stream0: { available: true, format: "H264", profile: 1, profile_control: { matches_saved: true } } };
  assert.deepEqual(buildStreamUpdate({ stream0: { format: "H265", profile: 2 } }, loaded), {});
  assert.match(streams.validate?.({ stream0: { format: "H264", profile: 3 } }, loaded) ?? "", /Baseline \(0\).*High \(2\)/);
});

test("Raptor stream save stops after a later unknown result and keeps completed phases", async () => {
  const { streams } = await import("../src/pages/config/media");
  const encoding = { supported: true, available: true, rc_mode: "CBR", bitrate: 2_000_000, saved_rc_mode: "CBR", saved_bitrate: 2_000_000, matches_saved: true };
  const loaded = { stream_full_controls: false, stream0_gop_control: true, stream0_encoding_control: true,
    stream0: { available: true, gop: 30, matches_saved: true, mode: "CBR", bitrate: 2_000_000, encoding_control: encoding } };
  const value = { stream0: { gop: 60, mode: "VBR", bitrate: 3_000_000 } };
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const accepted = requests.length === 1;
    return new Response(JSON.stringify({ status: accepted ? "accepted" : "error", persistent: accepted }), { headers: { "content-type": "application/json" } });
  } });
  await assert.rejects(() => streams.save!(client, value, loaded), /GOP result is unknown/);
  assert.deepEqual(requests, [{ stream0: { mode: "VBR", bitrate: 3_000_000 } }, { stream0: { gop: 60 } }]);
  assert.deepEqual(value, { stream0: { gop: 60, mode: "VBR", bitrate: 3_000_000 } });
  assert.equal((loaded.stream0.encoding_control as Record<string, unknown>).matches_saved, true);
  assert.equal(loaded.stream0.mode, "VBR");
  assert.equal(loaded.stream0_gop_control, false);
});

test("FPS decoder preserves unknown, pending and recovery instead of accepting a success shape", async () => {
  const { decodeRaptorFps } = await import("../src/api/decode");
  assert.deepEqual(decodeRaptorFps(fpsReceipt(), 0), fpsReceipt());
  assert.equal(decodeRaptorFps(fpsReceipt(1, { status: "error", persisted: null, persistence_pending: true }), 1).persisted, null);
  assert.equal(decodeRaptorFps(fpsReceipt(0, { status: "error", available: false, recovery_required: true, live_applied: null, persisted: null, fps: null }), 0).recovery_required, true);
  for (const change of [{ stream_id: 1 }, { fps: 0 }, { fps: 1.5 }, { fps: null }, { persisted: "true" }, { recovery_required: true }, { monitoring: {} }, { available: false }]) assert.throws(() => decodeRaptorFps(fpsReceipt(0, change), 0));
  assert.deepEqual(decodeRaptorFps({ supported: false, available: false }, 0), { supported: false, available: false });
});

test("FPS save keeps the exact persistence receipt and retries only its stream", async () => {
  const { buildStreamUpdate, streams } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, stream0_fps_control: true, stream1_fps_control: true, stream0_gop_control: true,
    stream0: { available: true, fps: 20, gop: 30, matches_saved: false, fps_control: fpsReceipt() }, stream1: { fps: 20, fps_control: fpsReceipt(1) } };
  const values = { stream0: { fps: 15, gop: 30 }, stream1: { fps: 20 } };
  assert.deepEqual(buildStreamUpdate(values, loaded), { stream0: { gop: 30, fps: 15 } });
  const requests: unknown[] = [];
  let pending = true;
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    requests.push(request);
    const fpsName = Object.keys(request).find((name) => request[name].fps !== undefined);
    if (!fpsName) return new Response(JSON.stringify({ status: "accepted", persistent: true }), { headers: { "content-type": "application/json" } });
    const id = Number(fpsName.slice(-1));
    const fps = request[fpsName].fps;
    const receipt = fpsReceipt(id, { fps, status: pending ? "error" : "ok", persistence_pending: pending, persisted: pending ? null : true });
    return new Response(JSON.stringify({ status: pending ? "error" : "accepted", persistent: !pending, fps_result: receipt }), { headers: { "content-type": "application/json" } });
  } });
  await assert.rejects(() => streams.save!(client, values, loaded), /Retry FPS persistence/);
  assert.deepEqual(values.stream0, { fps: 15, gop: 30 });
  assert.equal(typeof streams.saveLabel === "function" && streams.saveLabel(loaded), "Retry FPS persistence and save changes");
  const changed = { stream0: { fps: 15, gop: 90 }, stream1: { fps: 10 } };
  assert.deepEqual(buildStreamUpdate(changed, loaded), { stream0: { gop: 90, fps: 15 }, stream1: { fps: 10 } });
  assert.match(streams.validate?.({ stream0: { fps: 16, gop: 30 } }, loaded) ?? "", /same FPS/);
  pending = false;
  await streams.save!(client, changed, loaded);
  assert.deepEqual(requests, [{ stream0: { fps: 15 } }, { stream0: { fps: 15 } }, { stream1: { fps: 10 } }, { stream0: { gop: 90 } }]);
});

test("FPS transport or recovery failure disables another write and keeps the draft", async () => {
  const { streams, buildStreamUpdate } = await import("../src/pages/config/media");
  for (const recovery of [false, true]) {
    const loaded = { stream_full_controls: false, stream0_fps_control: true, stream0: { fps: 20, fps_control: fpsReceipt() } };
    const value = { stream0: { fps: 15 } };
    const client = new ApiClient({ fetchImpl: async () => {
      if (!recovery) throw new Error("disconnected");
      return new Response(JSON.stringify({ status: "error", persistent: false, fps_result: fpsReceipt(0, { status: "error", available: false, recovery_required: true, live_applied: null, persisted: null, fps: null }) }));
    } });
    await assert.rejects(() => streams.save!(client, value, loaded), recovery ? /recovery is required/ : /result is unknown/);
    assert.equal(value.stream0.fps, 15);
    assert.equal(loaded.stream0_fps_control, false);
    assert.deepEqual(buildStreamUpdate(value, loaded), {});
  }
});

test("FPS persistence retry runs before another stream draft", async () => {
  const { streams } = await import("../src/pages/config/media");
  const loaded = { stream_full_controls: false, fps_retry_stream: "stream1", fps_retry_value: 18,
    stream0_fps_control: true, stream1_fps_control: true,
    stream0: { available: true, fps: 20, fps_control: fpsReceipt() },
    stream1: { available: true, fps: 18, fps_control: fpsReceipt(1, { status: "error", persistence_pending: true, persisted: null, fps: 18 }) } };
  const value = { stream0: { fps: 15 }, stream1: { fps: 18 } };
  const requests: unknown[] = [];
  const client = new ApiClient({ fetchImpl: async (_url, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ status: "error", persistent: false,
      fps_result: fpsReceipt(1, { status: "error", persistence_pending: true, persisted: null, fps: 18 }) }),
    { headers: { "content-type": "application/json" } });
  } });
  await assert.rejects(() => streams.save!(client, value, loaded), /saving is unconfirmed/);
  assert.deepEqual(requests, [{ stream1: { fps: 18 } }]);
  assert.deepEqual(value, { stream0: { fps: 15 }, stream1: { fps: 18 } });
});
