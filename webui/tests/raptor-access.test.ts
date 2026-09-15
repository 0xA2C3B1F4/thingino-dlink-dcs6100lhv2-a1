import assert from "node:assert/strict";
import test from "node:test";
import { access, buildAccessUpdate, validateAccessUpdate } from "../src/pages/config/access";
import { decodeAccess } from "../src/api/decode";
import { ApiClient } from "../src/api/client";
import { streams, buildStreamUpdate } from "../src/pages/config/media";

const loaded = {
  source: "raptor", auth_enabled: true, username: "viewer", password: null,
  password_set: true, rtsp_port: 9554, rtsp_ch0: "front-door", rtsp_ch1: "stream1",
  rtsp_mic: "audio", onvif_port: null, onvif_enabled: null, onvif_ingress: null,
};

test("Raptor access retry retains live values and excludes unsupported controls", () => {
  const value = { ...loaded, password: "test-password" };
  assert.deepEqual(buildAccessUpdate(value, loaded), {
    username: "viewer", password: "test-password", rtsp_port: 9554,
    rtsp_ch0: "front-door", rtsp_ch1: "stream1",
  });
  assert.equal(validateAccessUpdate(value, loaded), undefined);
  assert.equal(access.loadTransform!(loaded).microphone_path_editable, false);
  assert.equal(decodeAccess(loaded).onvif_enabled, null);
  assert.equal(buildAccessUpdate({ ...value, password: "" }, loaded).password, undefined);
});

test("Raptor access rejects reserved ports, aliases and invalid credentials", () => {
  for (const update of [
    { rtsp_port: 8080 }, { rtsp_port: 8555 }, { rtsp_port: 1.5 },
    { rtsp_ch0: "audio" }, { rtsp_ch0: "stream1" }, { rtsp_ch1: "front-door" },
    { username: " viewer" }, { password: "short" }, { password: "long #password" },
  ]) {
    assert.equal(typeof validateAccessUpdate({ ...loaded, ...update }, loaded), "string");
  }
  assert.equal(typeof validateAccessUpdate({ ...loaded, password: "" }, { ...loaded, auth_enabled: false }), "string");
  assert.throws(() => decodeAccess({ ...loaded, onvif_enabled: true }), /capabilities/);
  assert.throws(() => decodeAccess({ ...loaded, rtsp_port: null }), /rtsp_port/);
  assert.throws(() => decodeAccess({ ...loaded, password_set: false }), /inconsistent/);
});

const checkedPaths = { ...loaded, saved_rtsp_ch0: "front-door", saved_rtsp_ch1: "stream1", rtsp_paths_match_saved: true };
const reply = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });

function streamClient(access: unknown = checkedPaths): ApiClient {
  return new ApiClient({ fetchImpl: async (url) => {
    if (String(url).endsWith("/config/access")) return reply(access);
    const id = Number(String(url).slice(-1));
    return reply({ source: "raptor", persistent: true, stream_id: id,
      supported: true, available: true, gop: 30, saved_gop: 30, matches_saved: true });
  } });
}

test("RTSP saved-path observations distinguish defaults, unknown values and false matches", () => {
  assert.equal(decodeAccess(checkedPaths).rtsp_paths_match_saved, true);
  assert.equal(decodeAccess({ ...checkedPaths, saved_rtsp_ch0: null, rtsp_paths_match_saved: false }).saved_rtsp_ch0, null);
  assert.throws(() => decodeAccess({ ...checkedPaths, saved_rtsp_ch0: null }), /saved-path observation/);
  assert.throws(() => decodeAccess({ ...checkedPaths, saved_rtsp_ch1: undefined }), /saved_rtsp_ch1/);
});

test("Streams save path-only changes through checked access without resending completed GOP", async () => {
  const state = await streams.load!(streamClient());
  assert.equal(state.stream0_rtsp_control, true);
  assert.deepEqual(buildStreamUpdate(structuredClone(state), state), {});
  const value = structuredClone(state);
  Object.assign(value.stream0!, { gop: 60, rtsp_endpoint: "garden" });
  const calls: { path: string; body: unknown }[] = [];
  let fail = true;
  const client = new ApiClient({ fetchImpl: async (url, init) => {
    const path = String(url);
    if (init?.method === "POST") {
      calls.push({ path, body: JSON.parse(String(init.body)) });
      return reply({ status: "accepted", persistent: !(path.endsWith("/config/access") && fail) });
    }
    return reply({ ...checkedPaths, rtsp_ch0: "garden", saved_rtsp_ch0: "garden" });
  } });
  await assert.rejects(() => streams.save!(client, value, state), /RTSP path save is unconfirmed/);
  assert.deepEqual(buildStreamUpdate(value, state), { stream0: { rtsp_endpoint: "garden" } });
  fail = false;
  await streams.save!(client, value, state);
  assert.deepEqual(calls, [
    { path: "/api/v1/prudynt", body: { stream0: { gop: 60 } } },
    { path: "/api/v1/config/access", body: { rtsp_ch0: "garden" } },
    { path: "/api/v1/config/access", body: { rtsp_ch0: "garden" } },
  ]);
  assert.deepEqual(buildStreamUpdate(value, state), {});
});

test("Streams retain explicit same-path persistence retry after reload and reject aliases before any save", async () => {
  const state = await streams.load!(streamClient({ ...checkedPaths, saved_rtsp_ch0: "older", rtsp_paths_match_saved: false }));
  assert.deepEqual(buildStreamUpdate(structuredClone(state), state), { stream0: { rtsp_endpoint: "front-door" } });
  const value = structuredClone(state);
  Object.assign(value.stream0!, { gop: 60, rtsp_endpoint: "stream1" });
  let requests = 0;
  const client = new ApiClient({ fetchImpl: async () => { requests++; return reply({}); } });
  await assert.rejects(() => streams.save!(client, value, state), /distinct stream paths/);
  assert.equal(requests, 0);
});

test("Streams do not confirm a path when final saved or authenticated state is missing", async () => {
  for (const observed of [
    { ...checkedPaths, rtsp_ch0: "garden", rtsp_paths_match_saved: false },
    { ...checkedPaths, rtsp_ch0: "garden", saved_rtsp_ch0: "garden", auth_enabled: false, password_set: false, username: "" },
    {},
  ]) {
    const state = await streams.load!(streamClient());
    const value = structuredClone(state);
    Object.assign(value.stream0!, { rtsp_endpoint: "garden" });
    const client = new ApiClient({ fetchImpl: async (_url, init) => reply(
      init?.method === "POST" ? { status: "accepted", persistent: true } : observed,
    ) });
    await assert.rejects(() => streams.save!(client, value, state), /RTSP path save is unconfirmed/);
    assert.deepEqual(buildStreamUpdate(value, state), { stream0: { rtsp_endpoint: "garden" } });
  }
});

test("Missing RTSP owner, old metadata and missing credentials do not disable encoder settings", async () => {
  for (const access of [
    {}, loaded,
    { ...checkedPaths, auth_enabled: false, password_set: false, username: "" },
  ]) {
    const state = await streams.load!(streamClient(access));
    assert.equal(state.stream0_rtsp_control, false);
    assert.equal(state.stream1_rtsp_control, false);
    assert.equal(state.stream0_gop_control, true);
    assert.equal(state.stream1_gop_control, true);
  }
});
