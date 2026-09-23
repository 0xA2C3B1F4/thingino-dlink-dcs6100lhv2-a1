import assert from "node:assert/strict";
import test from "node:test";
import { buildRaptorOsdMetadataUpdate, decodeRaptorOsdMetadata } from "../src/api/osd-metadata";

const response = {
  source: "raptor", persistent: true, supported: true, confirmed: true,
  saved: { available: true, id: "0123456789abcdef", enabled: true, entries: [
    { name: "room", type: "text", format: "Keittiö", position: "1,2", available: true },
    { name: "gain", type: "gain", format: "%s", position: "-1,3", available: false, unavailable_reason: "gain producer missing" },
  ] },
  published: { id: "0123456789abcdef", generation: 2, status: 0, fresh: true, matches_saved: true },
};

test("named OSD metadata keeps saved and publication observations separate", () => {
  const decoded = decodeRaptorOsdMetadata(response);
  assert.equal(decoded.saved.enabled, true);
  assert.equal(decoded.saved.entries[1]!.available, false);
  assert.equal(decoded.published.fresh, true);
  assert.equal(decoded.published.matches_saved, true);
  const replacement = decoded.saved.entries.map(({ name, type, format, position }) => ({
    name,
    type,
    format,
    position,
  }));
  assert.deepEqual(buildRaptorOsdMetadataUpdate(false, replacement), {
    enabled: false,
    entries: [
      { name: "room", type: "text", format: "Keittiö", position: "1,2" },
      { name: "gain", type: "gain", format: "%s", position: "-1,3" },
    ],
  });
  assert.doesNotThrow(() => buildRaptorOsdMetadataUpdate(true, [
    { name: "clock", type: "timestamp", format: "%%q %F", position: "1,2" },
    { name: "up", type: "uptime", format: "%%lu %lu:%02lu:%02lu", position: "1,3" },
  ]));
});

test("named OSD metadata rejects unknown, duplicate and malformed values", () => {
  assert.throws(() => decodeRaptorOsdMetadata({ ...response, extra: true }), /incomplete/);
  assert.throws(
    () => decodeRaptorOsdMetadata({
      ...response,
      saved: { ...response.saved, available: false, enabled: null },
    }),
    /incomplete/,
  );
  assert.throws(
    () => decodeRaptorOsdMetadata({
      ...response,
      saved: {
        ...response.saved,
        entries: [{ ...response.saved.entries[1], available: true }],
      },
    }),
    /gain availability/,
  );
  assert.throws(
    () => decodeRaptorOsdMetadata({
      ...response,
      confirmed: true,
      published: { ...response.published, fresh: false },
    }),
    /confirmation is inconsistent/,
  );
  assert.throws(() => buildRaptorOsdMetadataUpdate(true, [
    { name: "same", type: "text", format: "one", position: "1,2" },
    { name: "same", type: "text", format: "two", position: "1,3" },
  ]), /unique/);
  for (const entry of [
    { name: "bad name", type: "text", format: "x", position: "1,2" },
    { name: "x", type: "gain", format: "%n", position: "1,2" },
    { name: "x", type: "uptime", format: "%lu", position: "1,2" },
    { name: "x", type: "uptime", format: "%%lu %lu:%02lu", position: "1,2" },
    { name: "x", type: "text", format: "x", position: "8193,2" },
  ] as const) assert.throws(() => buildRaptorOsdMetadataUpdate(true, [entry]));
});
