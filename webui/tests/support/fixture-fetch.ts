import type { JsonObject, JsonValue } from "../../src/api/contracts";
import {
  audioFixture, daynightFixture, gpioFixture, homeAssistantFixture, homeAssistantRuntimeFixture, imageFixture, imagingRuntimeFixture,
  motionFixture, networkFixture, osdFixture, privacyFixture, recorderFixture,
  rsyslogFixture, sendFixture, stream0Fixture, stream1Fixture, timeFixture, webuiFixture,
} from "./fixtures";

export interface FixtureRequest {
  method: string;
  path: string;
  body: unknown;
}

function clone(value: JsonValue): JsonValue {
  return structuredClone(value);
}

function jsonResponse(value: JsonValue, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function bodyOf(init?: RequestInit): unknown {
  if (typeof init?.body !== "string" || !init.body) return {};
  try { return JSON.parse(init.body); } catch { return init.body; }
}

/**
 * An in-memory same-origin fetch implementation for page payload tests.
 * It records the exact method/path/body and returns redacted Control-shaped
 * fixtures, so tests never need CGI routes or a running camera.
 */
export function createFixtureFetch(): {
  fetchImpl: typeof fetch;
  requests: FixtureRequest[];
} {
  const requests: FixtureRequest[] = [];
  const domains: Record<string, JsonObject> = {
    "/api/v1/config/network": networkFixture,
    "/api/v1/config/time": timeFixture,
    "/api/v1/config/webui": webuiFixture,
    "/api/v1/config/rsyslog": rsyslogFixture,
    "/api/v1/config/daynight": daynightFixture,
    "/api/v1/config/gpio": gpioFixture,
    "/api/v1/config/ha": homeAssistantFixture,
    "/api/v1/prudynt/audio": audioFixture,
    "/api/v1/prudynt/image": imageFixture,
    "/api/v1/prudynt/stream0": stream0Fixture,
    "/api/v1/prudynt/stream1": stream1Fixture,
    "/api/v1/prudynt/osd": osdFixture,
    "/api/v1/prudynt/motion": motionFixture,
    "/api/v1/prudynt/privacy": privacyFixture,
    "/api/v1/services/send/config": sendFixture,
  };

  const fetchImpl: typeof fetch = async (input, init = {}) => {
    const url = new URL(String(input), "http://fixture.local");
    const method = init.method?.toUpperCase() ?? "GET";
    const body = bodyOf(init);
    requests.push({ method, path: `${url.pathname}${url.search}`, body });

    if (url.pathname === "/api/v1/auth/session") return jsonResponse({ authenticated: true, username: "root", is_default_password: false, client_ip: "127.0.0.1", control_api: { name: "Thingino Control", version: 1 } });
    if (method === "GET" && url.pathname === "/api/v1/recorder") return jsonResponse(clone(recorderFixture));
    if (method === "GET" && url.pathname === "/api/v1/network/wifi-scan") return jsonResponse({ networks: [{ ssid: "Quiet Grid Lab", bssid: "02:00:00:00:00:11", frequency: 2412, signal: -47, security: "WPA2" }] });
    if (method === "GET" && url.pathname === "/api/v1/imaging") return jsonResponse(clone(imagingRuntimeFixture));
    if (method === "GET" && url.pathname === "/api/v1/runtime/ha") return jsonResponse(clone(homeAssistantRuntimeFixture));
    const domainFixture = domains[url.pathname];
    if (method === "GET" && domainFixture) return jsonResponse(clone(domainFixture));

    if (method === "POST" && url.pathname === "/api/v1/recorder") return jsonResponse(clone(recorderFixture));
    if (method === "POST" && url.pathname === "/api/v1/imaging") return jsonResponse(clone(imagingRuntimeFixture));
    if (method === "POST" && url.pathname === "/api/v1/actions/ha") return jsonResponse({ status: "accepted", action: (body as JsonObject).action ?? "" });
    if (method === "POST" && (url.pathname === "/api/v1/prudynt" || url.pathname.startsWith("/api/v1/config/") || url.pathname === "/api/v1/services/send/config")) return jsonResponse({ status: "ok" });
    if (method === "POST" && url.pathname.startsWith("/api/v1/actions/")) return jsonResponse({ status: "ok" });
    return jsonResponse({ status: "error", error: { code: "not_found", message: "fixture route not found" } }, 404);
  };

  return { fetchImpl, requests };
}
