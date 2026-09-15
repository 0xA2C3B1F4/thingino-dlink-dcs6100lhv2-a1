import assert from "node:assert/strict";
import test from "node:test";
import { decodeRaptorTimelapse } from "../src/api/decode";
import { routes } from "../src/api/routes";

function response() {
  const policy = { enabled: false, mount: "/mnt/mmcblk0p1", filepath: "raptor/timelapse", filename: "unix-seconds-sequence.jpg", interval: 1, keep_days: 7, preset_enabled: false, presets: { ircut: false, ir850: false, color: false } };
  return { ok: true, data: { source: "raptor", domain: "timelapse", persistent: true, available: true, timelapse: policy, saved_timelapse: structuredClone(policy), matches_saved: true, mounts: [policy.mount], runtime: { phase: "disabled", last_error: null as string | null, preset_restore: "not_used", successes: 0, last_success: null, next_due: null, cleanup_blocked: false } } };
}

test("Timelapse query and checked saved policy decode independently of video recorder", () => {
  assert.equal(routes.recorderTimelapse, "/api/v1/recorder?domain=timelapse");
  assert.equal(decodeRaptorTimelapse(response()).data.matches_saved, true);
  const changed = response(); changed.data.saved_timelapse.interval = 2;
  assert.throws(() => decodeRaptorTimelapse(changed), /saved state differs/);
  changed.data.matches_saved = false;
  assert.equal(decodeRaptorTimelapse(changed).data.matches_saved, false);
});

test("Timelapse rejects mutable storage roots, invalid ranges and unknown errors", () => {
  for (const field of ["mount", "filepath", "filename"] as const) {
    const value = response(); value.data.timelapse[field] = "other";
    assert.throws(() => decodeRaptorTimelapse(value));
  }
  for (const interval of [0, 1441, 1.5]) {
    const value = response(); value.data.timelapse.interval = interval;
    assert.throws(() => decodeRaptorTimelapse(value));
  }
  const value = response(); value.data.runtime.last_error = "arbitrary upstream text";
  assert.throws(() => decodeRaptorTimelapse(value));
  value.data.runtime.last_error = "cleanup_failed";
  value.data.runtime.cleanup_blocked = true;
  assert.equal(decodeRaptorTimelapse(value).data.runtime.cleanup_blocked, true);
});
