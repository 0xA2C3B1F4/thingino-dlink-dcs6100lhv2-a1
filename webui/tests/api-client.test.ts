import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient, ApiRequestError } from "../src/api/client";
import { ControlApi } from "../src/api/control";

test("API client sends same-origin credentials and JSON accept header", async () => {
  let captured: RequestInit | undefined;
  const client = new ApiClient({
    fetchImpl: async (_input, init) => {
      captured = init;
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    },
  });
  assert.deepEqual(await client.json<{ ok: boolean }>("/api/v1/health"), { ok: true });
  assert.equal(captured?.credentials, "same-origin");
  assert.equal(new Headers(captured?.headers).get("Accept"), "application/json");
});

test("API client preserves the Control error envelope and signals 401", async () => {
  let unauthorized = 0;
  const client = new ApiClient({
    fetchImpl: async () => new Response(JSON.stringify({ status: "error", error: { code: "unauthorized", message: "authentication required" } }), { status: 401 }),
    onUnauthorized: () => { unauthorized += 1; },
  });
  await assert.rejects(
    client.json("/api/v1/runtime/system"),
    (failure: unknown) => failure instanceof ApiRequestError && failure.status === 401 && failure.code === "unauthorized" && failure.message === "authentication required",
  );
  assert.equal(unauthorized, 1);
});

test("API client normalizes a timeout without depending on window", async () => {
  const client = new ApiClient({
    fetchImpl: async (_input, init) => await new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }),
    timeoutMs: 5,
  });
  await assert.rejects(
    client.json("/api/v1/runtime/system"),
    (failure: unknown) => failure instanceof ApiRequestError && failure.code === "client_timeout" && failure.retryable,
  );
});

test("API client reports a disconnected Control request as retryable", async () => {
  const api = new ApiClient({ fetchImpl: async () => { throw new TypeError("fetch failed"); } });
  await assert.rejects(
    api.json("/api/v1/runtime/system"),
    (error: unknown) => error instanceof ApiRequestError && error.code === "network_error" && error.retryable && /fetch failed/.test(error.message),
  );
});

test("empty actions and snapshots do not advertise HTML redirects", async () => {
  const headers: Array<string | null> = [];
  const client = new ApiClient({ fetchImpl: async (_input, init) => {
    headers.push(new Headers(init?.headers).get("Accept"));
    return headers.length === 1 ? new Response(null, { status: 204 }) : new Response("jpeg", { status: 200 });
  } });
  await client.empty("/api/v1/actions/time/sync", { method: "POST" });
  await client.blob("/api/v1/actions/snapshot?stream_id=0");
  assert.deepEqual(headers, ["application/json", "image/jpeg"]);
});

test("every Preview control maps to one canonical Control request", async () => {
  const requests: Array<{ path: string; body: unknown }> = [];
  const client = new ApiClient({ fetchImpl: async (input, init) => {
    requests.push({ path: String(input), body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
  const api = new ControlApi(client);
  await api.setLiveControl({ kind: "color", enabled: true });
  await api.setLiveControl({ kind: "ircut", enabled: false });
  await api.setLiveControl({ kind: "ir850", enabled: true });
  await api.setLiveControl({ kind: "microphone", enabled: false });
  await api.setLiveControl({ kind: "speaker", enabled: true });
  await api.setLiveControl({ kind: "motion", enabled: false });
  await api.setLiveControl({ kind: "privacy", enabled: true });
  await api.setLiveControl({ kind: "recording", enabled: true, stream: 0 });
  await api.setLiveControl({ kind: "recording", enabled: false, stream: 1 });
  await api.setLiveControl({ kind: "daynight", mode: "night" });
  assert.deepEqual(requests, [
    { path: "/api/v1/actions/control", body: { cmd: "color", val: 1 } },
    { path: "/api/v1/actions/control", body: { cmd: "ircut", val: 0 } },
    { path: "/api/v1/actions/control", body: { cmd: "ir850", val: 1 } },
    { path: "/api/v1/actions/control", body: { audio: { mic_enabled: false } } },
    { path: "/api/v1/actions/control", body: { audio: { spk_enabled: true } } },
    { path: "/api/v1/actions/control", body: { motion: { enabled: false } } },
    { path: "/api/v1/actions/control", body: { privacy: { enabled: true } } },
    { path: "/api/v1/actions/control", body: { mp4: { start: { channel: 0 } } } },
    { path: "/api/v1/actions/control", body: { mp4: { stop: { channel: 1 } } } },
    { path: "/api/v1/actions/daynight", body: { mode: "night" } },
  ]);
});

test("mutations keep exact bodies and media types and carry the CSRF guard", async () => {
  const requests: Array<{ path: string; method: string; type: string | null; requestedWith: string | null; body: BodyInit | null | undefined }> = [];
  const client = new ApiClient({ fetchImpl: async (input, init) => {
    requests.push({
      path: String(input),
      method: init?.method ?? "GET",
      type: new Headers(init?.headers).get("Content-Type"),
      requestedWith: new Headers(init?.headers).get("X-Requested-With"),
      body: init?.body,
    });
    return new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
  await client.empty("/api/v1/auth/logout", { method: "POST" });
  await client.postJson("/api/v1/diagnostics", {});
  await client.postForm("/api/v1/network/probe", new URLSearchParams({ action: "resolve", target: "camera.local" }));
  await client.postText("/api/v1/files/text?file=%2Fmnt%2Fnotes.txt", "notes\n");
  await client.empty("/api/v1/files?rm=%2Fmnt%2Fnotes.txt", { method: "POST" });
  await client.empty("/api/v1/webui/api-key", { method: "DELETE" });
  assert.deepEqual(requests.map(({ path, method, type, requestedWith, body }) => ({ path, method, type, requestedWith, body: body?.toString() ?? null })), [
    { path: "/api/v1/auth/logout", method: "POST", type: null, requestedWith: "Thingino-WebUI", body: null },
    { path: "/api/v1/diagnostics", method: "POST", type: "application/json", requestedWith: "Thingino-WebUI", body: "{}" },
    { path: "/api/v1/network/probe", method: "POST", type: "application/x-www-form-urlencoded", requestedWith: "Thingino-WebUI", body: "action=resolve&target=camera.local" },
    { path: "/api/v1/files/text?file=%2Fmnt%2Fnotes.txt", method: "POST", type: "text/plain; charset=utf-8", requestedWith: "Thingino-WebUI", body: "notes\n" },
    { path: "/api/v1/files?rm=%2Fmnt%2Fnotes.txt", method: "POST", type: null, requestedWith: "Thingino-WebUI", body: null },
    { path: "/api/v1/webui/api-key", method: "DELETE", type: null, requestedWith: "Thingino-WebUI", body: null },
  ]);
});
