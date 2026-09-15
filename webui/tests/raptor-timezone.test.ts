import assert from "node:assert/strict";
import test from "node:test";
import type { ApiClient } from "../src/api/client";
import { buildTimeUpdate, time } from "../src/pages/config/network-time";
import { timeFixture } from "./support/fixtures";

const state = { ...timeFixture, source: "raptor", timezone_reload_supported: true, timezone_applied: false };

test("Raptor time form separates persisted timezone from daemon application", () => {
  const loaded = time.decode!(state);
  assert.equal(loaded.time_control, true);
  assert.equal(loaded.timezone_apply_status, "Not confirmed; reload and retry saving");
  assert.deepEqual(buildTimeUpdate(loaded), buildTimeUpdate(timeFixture));
  assert.deepEqual(buildTimeUpdate(time.decode!(timeFixture)), buildTimeUpdate(timeFixture));
  assert.equal(buildTimeUpdate({ ...loaded, timezone: "helsinki" }).timezone, "Europe/Helsinki");
  assert.throws(() => time.decode!({ ...state, timezone_applied: null }), /inconsistent/);
  assert.throws(() => time.decode!({ ...state, timezone_reload_supported: false, timezone_applied: true }), /inconsistent/);
  const unavailable = time.decode!({ ...state, timezone_reload_supported: false });
  assert.equal(unavailable.time_control, false);
  assert.equal(unavailable.timezone_apply_status, "A required media service could not be verified. Timezone changes are unavailable.");
  assert.throws(() => buildTimeUpdate(unavailable), /unavailable/);
  for (const field of time.fields.filter((field) => !field.readOnly)) {
    assert.deepEqual(field.enabledWhen, { path: "time_control", equals: true });
  }
});

test("Raptor time save requires both persistence and application acknowledgement", async () => {
  const loaded = time.decode!(state);
  for (const reply of [null, { status: "ok" }, { persistent: true, applied: false }, { persistent: false, applied: true }]) {
    const client = { postJson: async () => reply } as unknown as ApiClient;
    await assert.rejects(time.save!(client, buildTimeUpdate(loaded), loaded), /not confirmed/);
  }
  const client = { postJson: async (route: string, body: unknown) => {
    assert.equal(route, "/api/v1/config/time");
    assert.deepEqual(body, buildTimeUpdate(timeFixture));
    return { status: "ok", persistent: true, applied: true };
  } } as unknown as ApiClient;
  await time.save!(client, buildTimeUpdate(loaded), loaded);
  const legacy = { postJson: async () => ({ status: "ok" }) } as unknown as ApiClient;
  await time.save!(legacy, buildTimeUpdate(timeFixture), time.decode!(timeFixture));
});
