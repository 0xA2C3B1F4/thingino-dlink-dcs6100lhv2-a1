import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient } from "../src/api/client";
import type { JsonObject } from "../src/api/contracts";
import { createFixtureFetch } from "./support/fixture-fetch";
import { assertNetworkShape, assertSecretSemantics } from "./support/contracts";
import { audioFixture, imageFixture, motionFixture, networkFixture, osdFixture, privacyFixture, stream0Fixture, stream1Fixture } from "./support/fixtures";

test("in-memory Control fetch serves typed redacted GET fixtures and records no CGI traffic", async () => {
  const fixture = createFixtureFetch();
  const api = new ApiClient({ fetchImpl: fixture.fetchImpl });
  const network = await api.json<JsonObject>("/api/v1/config/network");
  assertNetworkShape(network);
  assertSecretSemantics(network);
  assert.deepEqual(fixture.requests[0], { method: "GET", path: "/api/v1/config/network", body: {} });
  assert.ok(fixture.requests.every((request) => !request.path.includes(".cgi")));
});

test("in-memory Control fetch captures the complete save payload", async () => {
  const fixture = createFixtureFetch();
  const api = new ApiClient({ fetchImpl: fixture.fetchImpl });
  const payload = structuredClone(networkFixture);
  payload.hostname = "dcs6100-lab";
  payload.interfaces.eth0.address = "192.0.2.20";
  await api.postJson("/api/v1/config/network", payload);
  const request = fixture.requests.at(-1)!;
  assert.equal(request.method, "POST");
  assert.deepEqual(request.body, payload);
  const interfaces = (request.body as Record<string, unknown>).interfaces as Record<string, Record<string, unknown>>;
  for (const name of ["eth0", "wlan0", "usb0"]) {
    const iface = interfaces[name]!;
    for (const field of ["address", "netmask", "gateway", "broadcast"]) assert.equal(typeof iface[field], "string", `${name}.${field}`);
  }
});

test("Prudynt page saves use one typed domain envelope per request", async () => {
  const fixture = createFixtureFetch();
  const api = new ApiClient({ fetchImpl: fixture.fetchImpl });
  const domains = { audio: audioFixture, image: imageFixture, stream0: stream0Fixture, stream1: stream1Fixture, osd: osdFixture, motion: motionFixture, privacy: privacyFixture };
  for (const [domain, value] of Object.entries(domains)) await api.postJson("/api/v1/prudynt", { [domain]: value });
  const requests = fixture.requests.filter((request) => request.path === "/api/v1/prudynt");
  assert.equal(requests.length, Object.keys(domains).length);
  requests.forEach((request, index) => assert.deepEqual(Object.keys(request.body as object), [Object.keys(domains)[index]]));
});
