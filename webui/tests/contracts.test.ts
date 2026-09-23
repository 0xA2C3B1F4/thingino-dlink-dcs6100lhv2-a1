import assert from "node:assert/strict";
import test from "node:test";
import { decodeRecorder, decodeHealth, decodeHeartbeat, decodeMotionRuntime, decodeRuntimeMedia, decodeSession } from "../src/api/decode";
import { dayNightSelection } from "../src/pages/preview";

const heartbeat = {
  time_now: 1787248800, uptime: 100, daynight_brightness: null, total_gain: 1.5,
  daynight_mode: "unknown", rec_ch0: false, rec_ch1: true, timelapse_enabled: false,
  motion_enabled: true, motion_active: false, motion_ingress_ready: true, privacy_enabled: false, color_mode: null, mic_enabled: true,
  spk_enabled: false, daynight_enabled: true, ircut_state: null, ir850_state: 0,
  ir940_state: null, white_state: null,
};

test("experimental Raptor metadata uses the existing health and media decoders", () => {
  const health = {
    control_api: { name: "Thingino Control", version: 1 }, status: "ok", healthy: true,
    backend: { name: "raptor", available: true },
    checks: { system: { streamer_running: true }, streaming: { running: true, healthy: true, streams_enabled: 2 } },
  };
  assert.deepEqual(decodeHealth(health), health);
  const ch0 = { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=0" };
  const ch1 = { available: false, enabled: true, snapshot_url: null };
  assert.deepEqual(decodeRuntimeMedia({
    streams: { ch0: { ...ch0, width: 1920, height: 1080, format: "H264", fps: 15 }, ch1 },
    rtsp: { port: 8554 },
  }), { streams: { ch0: { ...ch0, width: 1920, height: 1080, format: "H264", fps: 15 }, ch1 }, rtsp: { port: 8554 } });
  assert.throws(() => decodeHealth({ ...health, backend: { name: "raptor" } }), /available/);
  assert.throws(() => decodeRuntimeMedia({ streams: { ch0, ch1: { snapshot_url: null } } }), /available/);
});

test("session decoder requires the public session metadata", () => {
  assert.deepEqual(decodeSession({ authenticated: false, username: null, is_default_password: false, client_ip: "127.0.0.1", control_api: { name: "Thingino Control", version: 1 } }), {
    authenticated: false, username: null, is_default_password: false, client_ip: "127.0.0.1", control_api: { name: "Thingino Control", version: 1 },
  });
  assert.throws(() => decodeSession({ authenticated: false }), /session/);
});

test("heartbeat decoder accepts null physical states and rejects missing fields", () => {
  assert.deepEqual(decodeHeartbeat(heartbeat), heartbeat);
  const incomplete = { ...heartbeat };
  delete (incomplete as Partial<typeof incomplete>).timelapse_enabled;
  assert.throws(() => decodeHeartbeat(incomplete), /boolean state/);
  assert.throws(() => decodeHeartbeat({ ...heartbeat, daynight_mode: "auto" }), /day\/night/);
});

test("day/night selection distinguishes automatic policy from forced modes", () => {
  assert.equal(dayNightSelection({ daynight_enabled: true, daynight_mode: "day" }), "auto");
  assert.equal(dayNightSelection({ daynight_enabled: false, daynight_mode: "day" }), "day");
  assert.equal(dayNightSelection({ daynight_enabled: false, daynight_mode: "night" }), "night");
  assert.equal(dayNightSelection({ daynight_enabled: false, daynight_mode: "unknown" }), null);
});

test("motion runtime decoder keeps bounded queue and sink counters", () => {
  const runtime = {
    version: 1, ingress_ready: true, monitoring: true, active: false, channel: 0,
    producer_pid: 42, producer_sequence: 7, last_observation_monotonic_ms: 1234,
    last_transition_unix_ms: null, queue: { capacity: 32, depth: 0, high_water: 2, dropped: 1, coalesced: 3 },
    received: 10, rejected: 0, accepted: 10, duplicate_or_replayed: 0, producer_restarts: 0, transitions: 2, sink: { queued: 2, coalesced: 0, dropped: 0, disabled: 1 },
    speaker: { queued: 0, dropped: 0 }, clips: { capacity: 4, pending: 0, requested: 0, ready: 0, dropped: 0, recovered: 0, manifest_errors: 0 }, unsupported_destination_events: 0,
  };
  assert.deepEqual(decodeMotionRuntime(runtime), runtime);
  assert.throws(() => decodeMotionRuntime({ ...runtime, channel: 3 }), /channel/);
  assert.throws(() => decodeMotionRuntime({ ...runtime, queue: { depth: 0 } }), /queue/);
});


test("heartbeat recording metadata validates availability and bounded reasons", () => {
  assert.equal(decodeHeartbeat({ ...heartbeat, rec_ch0: false, rec_ch0_available: false, rec_ch0_reason: "no_sd" }).rec_ch0, false);
  assert.throws(() => decodeHeartbeat({ ...heartbeat, rec_ch0_available: "yes" }), /recording availability/);
  assert.throws(() => decodeHeartbeat({ ...heartbeat, rec_ch1_reason: "unknown" }), /recording reason/);
  assert.throws(() => decodeHeartbeat({ ...heartbeat, rec_ch1_reason: ["no_sd"] }), /recording reason/);
  assert.throws(() => decodeHeartbeat({ ...heartbeat, rec_ch0_file_closed: "yes" }), /recording closure/);
});


test("Raptor recorder separates selected policy, saved state and unavailable storage", () => {
  const video = { autostart: false, cleanup_enabled: true, duration: 300, limit: 15, min_free_mb: 100, check_interval: 60, channel: 1, mount: "/mnt/mmcblk0p1", device_path: "raptor/stream1", filename: "%Y-%m-%d/%H-%M-%S" };
  const data = { source: "raptor", persistent: true, video, saved_video: { ...video }, matches_saved: true, runtime: null, storage_available: false, free_mb: null, boot_pending: false, cleanup_ok: false, timelapse: null, timelapse_supported: false, mounts: [video.mount] };
  assert.deepEqual(decodeRecorder({ ok: true, data }), { ok: true, data });
  for (const patch of [{ free_mb: 0 }, { saved_video: null }, { saved_video: { ...video, duration: 600 } }, { video: { ...video, device_path: "raptor/stream0" } }, { timelapse: { enabled: false } }]) {
    assert.throws(() => decodeRecorder({ ok: true, data: { ...data, ...patch } }));
  }
  const unavailable = decodeRecorder({ ok: true, data: { ...data, saved_video: null, matches_saved: false } }).data;
  assert("source" in unavailable);
  assert.equal(unavailable.saved_video, null);
});


test("Recorder stale storage observation remains explicit", () => {
  assert.equal(decodeHeartbeat({ ...heartbeat, rec_ch0_reason: "storage_unknown", rec_ch0_available: false }).rec_ch0_reason, "storage_unknown");
});


test("heartbeat preserves all timelapse policy observation states", () => {
  for (const timelapse_enabled of [true, false, null]) {
    assert.equal(decodeHeartbeat({ ...heartbeat, timelapse_enabled }).timelapse_enabled, timelapse_enabled);
  }
  for (const timelapse_enabled of [0, "false", {}, undefined]) {
    assert.throws(() => decodeHeartbeat({ ...heartbeat, timelapse_enabled }), /boolean state/);
  }
});
