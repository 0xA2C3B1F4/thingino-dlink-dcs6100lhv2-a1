import assert from "node:assert/strict";
import test from "node:test";
import { decodeHeartbeat, decodeMotionRuntime, decodeSession } from "../src/api/decode";
import { dayNightSelection } from "../src/pages/preview";

const heartbeat = {
  time_now: 1787248800, uptime: 100, daynight_brightness: null, total_gain: 1.5,
  daynight_mode: "unknown", rec_ch0: false, rec_ch1: true, timelapse_enabled: false,
  motion_enabled: true, motion_active: false, motion_ingress_ready: true, privacy_enabled: false, color_mode: null, mic_enabled: true,
  spk_enabled: false, daynight_enabled: true, ircut_state: null, ir850_state: 0,
  ir940_state: null, white_state: null,
};

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
