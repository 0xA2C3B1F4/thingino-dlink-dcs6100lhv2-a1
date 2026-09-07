import assert from "node:assert/strict";
import type { FieldSpec } from "../../src/app/forms";
import type { JsonObject, JsonValue } from "../../src/api/contracts";

export const AUDIO_FORMATS = ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"] as const;
export const STREAM_FORMATS = ["H264", "H265"] as const;
export const STREAM_MODES = ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"] as const;
export const OSD_ENTRY_TYPES = ["text", "gain", "hostname", "ipaddress", "timestamp", "uptime"] as const;

const SECRET_KEYS = new Set(["password", "token", "client_secret", "refresh_token", "api_key", "secret", "bot_token"]);

export function getPath(value: JsonObject, path: string): JsonValue | undefined {
  let current: JsonValue | undefined = value;
  for (const part of path.split(".")) {
    if (typeof current !== "object" || current === null || Array.isArray(current)) return undefined;
    current = current[part];
  }
  return current;
}

export function setPath(value: JsonObject, path: string, nextValue: JsonValue): void {
  const parts = path.split(".");
  let current: JsonObject = value;
  for (const part of parts.slice(0, -1)) {
    const child = current[part];
    if (typeof child !== "object" || child === null || Array.isArray(child)) current[part] = {};
    current = current[part] as JsonObject;
  }
  current[parts.at(-1)!] = nextValue;
}

export function cloneJson<T extends JsonObject>(value: T): T {
  return structuredClone(value);
}

/** Build the expected full-model request used by a config form test. */
export function buildFullPayload(
  loaded: JsonObject,
  changes: ReadonlyMap<string, JsonValue | undefined>,
  fields: readonly FieldSpec[],
): JsonObject {
  const payload = cloneJson(loaded);
  for (const field of fields) {
    if (field.readOnly) continue;
    const value = changes.get(field.path);
    if (field.writeOnlySecret && (value === undefined || value === "" || value === null)) {
      const parts = field.path.split(".");
      let current: JsonObject = payload;
      for (const part of parts.slice(0, -1)) {
        const child = current[part];
        if (typeof child !== "object" || child === null || Array.isArray(child)) break;
        current = child as JsonObject;
      }
      delete current[parts.at(-1)!];
      continue;
    }
    if (value !== undefined) setPath(payload, field.path, value);
  }
  return payload;
}

export function collectPaths(value: JsonValue, prefix = ""): string[] {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return prefix ? [prefix] : [];
  return Object.entries(value).flatMap(([key, child]) => collectPaths(child, prefix ? `${prefix}.${key}` : key));
}

export function assertSecretSemantics(value: JsonObject): void {
  function visit(current: JsonValue, path: string): void {
    if (typeof current !== "object" || current === null) return;
    if (Array.isArray(current)) {
      current.forEach((entry, index) => visit(entry, `${path}[${index}]`));
      return;
    }
    for (const [key, child] of Object.entries(current)) {
      const childPath = path ? `${path}.${key}` : key;
      if (SECRET_KEYS.has(key)) {
        assert.equal(child, null, `${childPath} must be redacted in a GET response`);
        const marker: JsonValue | undefined = current[`${key}_set`];
        assert.equal(typeof marker, "boolean", `${childPath} needs a boolean ${key}_set marker`);
      }
      visit(child, childPath);
    }
  }
  visit(value, "");
}

export function assertNetworkShape(value: JsonObject): void {
  assert.equal(typeof value.hostname, "string");
  assert.deepEqual(Object.keys(value.interfaces as JsonObject).sort(), ["eth0", "usb0", "wlan0"]);
  for (const name of ["eth0", "wlan0", "usb0"]) {
    const iface = (value.interfaces as JsonObject)[name] as JsonObject;
    for (const field of ["address", "netmask", "gateway", "broadcast"]) assert.equal(typeof iface[field], "string", `${name}.${field}`);
    assert.equal(typeof iface.enabled, "boolean", `${name}.enabled`);
    assert.equal(typeof iface.dhcp, "boolean", `${name}.dhcp`);
    assert.equal(typeof iface.ipv6, "boolean", `${name}.ipv6`);
    assert.equal(typeof iface.mac, "string", `${name}.mac`);
    assert.equal(typeof iface.link_up, "boolean", `${name}.link_up`);
  }
  assert.equal((value.wifi_ap as JsonObject).enabled, false, "D-Link setup AP is fixed off");
  assert.equal(((value.wifi as JsonObject).password), null, "Wi-Fi password must never be returned");
  assert.equal(typeof (value.wifi as JsonObject).password_set, "boolean");
}

export function assertRanges(): void {
  assert.deepEqual(AUDIO_FORMATS, ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"]);
  assert.deepEqual(STREAM_FORMATS, ["H264", "H265"]);
  assert.deepEqual(STREAM_MODES, ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"]);
  assert.deepEqual(OSD_ENTRY_TYPES, ["text", "gain", "hostname", "ipaddress", "timestamp", "uptime"]);
}

export function assertUnchangedPaths(before: JsonObject, after: JsonObject, changedPaths: readonly string[]): void {
  const changed = new Set(changedPaths);
  for (const path of collectPaths(before)) {
    if (changed.has(path) || [...changed].some((candidate) => candidate.startsWith(`${path}.`))) continue;
    assert.deepEqual(getPath(after, path), getPath(before, path), `${path} changed unexpectedly`);
  }
}
