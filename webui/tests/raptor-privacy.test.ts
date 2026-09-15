import assert from "node:assert/strict";
import test from "node:test";
import type { ApiClient } from "../src/api/client";
import { raptorPrivacy, decodeRaptorPrivacy, buildRaptorPrivacyUpdate } from "../src/pages/config/motion-privacy";

const state = { source: "raptor", persistent: true, supported: true, available: true,
  enabled: true, saved_enabled: false, matches_saved: false };

test("privacy keeps live protection, disk state and unknown values separate", () => {
  assert.deepEqual(decodeRaptorPrivacy(state), state);
  const unknown = decodeRaptorPrivacy({ ...state, available: false, enabled: null, saved_enabled: null });
  assert.throws(() => buildRaptorPrivacyUpdate(unknown, unknown), /explicit privacy/);
  assert.deepEqual(buildRaptorPrivacyUpdate({ enabled: false }, unknown), { privacy: { enabled: false } });
  assert.deepEqual(buildRaptorPrivacyUpdate(state, state), { privacy: { enabled: true } });
  for (const bad of [null, { ...state, persistent: false }, { ...state, available: false },
    { ...state, saved_enabled: "false" }, { ...state, supported: false }, { ...state, matches_saved: true }]) {
    assert.throws(() => decodeRaptorPrivacy(bad), /Privacy/);
  }
});

test("privacy retry saves an explicit unchanged choice and needs application plus persistence", async () => {
  for (const reply of [null, { status: "accepted" }, { persistent: true }, { persistent: true, applied: false }]) {
    const client = { postJson: async () => reply } as unknown as ApiClient;
    await assert.rejects(raptorPrivacy.save!(client, buildRaptorPrivacyUpdate(state, state), state), /not confirmed/);
  }
  const client = { postJson: async (route: string, body: unknown) => {
    assert.equal(route, "/api/v1/prudynt");
    assert.deepEqual(body, { privacy: { enabled: true } });
    return { status: "accepted", applied: true, persistent: true };
  } } as unknown as ApiClient;
  await raptorPrivacy.save!(client, buildRaptorPrivacyUpdate(state, state), state);
});
