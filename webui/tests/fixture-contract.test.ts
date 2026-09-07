import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { domainFixtures } from "./support/fixtures";
import { assertSecretSemantics } from "./support/contracts";

test("fixture server exposes the complete host-side parity surface", async () => {
  const source = await readFile("fixtures/server.mjs", "utf8");
  assert.ok(source.includes("audio|image|stream0|stream1|osd|motion|privacy"));
  assert.match(source, /diagnosticQueries = \["crontab", "onvif", "prudynt", "thingino", "logcat", "logread", "dmesg", "lsmod", "netstat", "release", "top", "status", "system"\]/);
  assert.match(source, /wifi-scan.*frequency/);
  assert.match(source, /function recorderEnvelope/);
  assert.ok(source.includes("crontab requires an empty GET or a non-empty POST"));
  assert.ok(source.includes("sensor metadata is read-only"));
  assert.ok(source.includes("time sync requires an empty POST"));
  assert.ok(source.includes("reboot requires an empty POST"));
  assert.doesNotMatch(source, /\/x\/[^\s"']*\.cgi/i);
  assert.doesNotMatch(source, /agent\.cgi/i);
});

test("all domain fixtures are safe to use in tests and contain no raw secrets", () => {
  for (const fixture of Object.values(domainFixtures)) assertSecretSemantics(fixture);
});

test("time fixture exposes only the user-facing IANA timezone contract", () => {
  const time = domainFixtures.time;
  assert.equal(time.timezone, "Europe/Helsinki");
  assert.ok(Array.isArray(time.timezone_options));
  assert.equal(typeof time.current_unix_time, "number");
  assert.equal(Object.hasOwn(time, "tz_name"), false);
  assert.equal(Object.hasOwn(time, "tz_data"), false);
});
