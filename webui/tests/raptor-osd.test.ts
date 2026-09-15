import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { decodeOsdForPage, buildRaptorOsdUpdate } from "../src/pages/config/osd";
import { osdFixture } from "./support/fixtures";
const state = { source: "raptor", persistent: true, enabled: true, available: true, matches_saved: false,
  format: "%H:%M:%S", fill_color: "#ffffffff", outline_color: "#000000ff",
  fields: { format: true, fill_color: true, outline_color: false } };

test("Published OSD contract includes every implemented text setting and the tested size bounds", () => {
  const contract = JSON.parse(readFileSync(new URL("../../contracts/thingino-control-api-v1.json", import.meta.url), "utf8"));
  const route = contract.routes.find((entry: { id: string }) => entry.id === "prudynt.config");
  const schema = route.request_schema.raptor_osd;
  assert.ok(schema, "The published contract must describe Raptor OSD fields");
  const loaded = decodeOsdForPage({ ...state, font_size: 48, fields: {
    format: true, fill_color: true, outline_color: true, fill_alpha: true, outline_alpha: true,
    font_size: true, enabled: true, background_color: true,
  }, background_color: "#0000ff80" });
  const body = buildRaptorOsdUpdate(loaded, "%T", "#ffffff80", "#00000000", 48, false, "#0000ffff");
  assert.deepEqual(Object.keys(schema.fields).sort(), Object.keys(body.osd as object).sort());
  assert.deepEqual(schema.fields.font_size, { type: "integer", minimum: 16, maximum: 48 });
  assert.deepEqual(schema.fields.enabled, { type: "boolean" });
  assert.deepEqual(schema.fields.background_color, { type: "string", pattern: "^#[0-9a-fA-F]{8}$" });
  const response = route.response_schema.oneOf.find((entry: { domain?: string }) => entry.domain === "osd");
  assert.ok(response.required.includes("font_size"));
  assert.doesNotMatch(route.request_schema.semantics["raptor.osd"], /Toggle, font size.*unavailable/);
});

test("Raptor text visibility remains editable while hidden and is capability gated", () => {
  const loaded = decodeOsdForPage({ ...state, enabled: false, fields: { ...state.fields, enabled: true } });
  assert.equal(loaded.burnin.enabled, false);
  for (const enabled of [false, true]) {
    assert.deepEqual(buildRaptorOsdUpdate(loaded, "%T", "#ffffffff", "#000000ff", undefined, enabled),
      { osd: { enabled, format: "%T", fill_color: "#ffffffff" } });
  }
  assert.throws(() => buildRaptorOsdUpdate(loaded, "%T", "#ffffffff", "#000000ff"), /enabled/);
  const legacyCapability = decodeOsdForPage(state);
  assert.equal((buildRaptorOsdUpdate(legacyCapability, "%T", "#ffffffff", "#000000ff", undefined, false).osd as Record<string, unknown>).enabled, undefined);
  assert.throws(() => decodeOsdForPage({ ...state, fields: { ...state.fields, enabled: "yes" } }), /visibility/);
});
test("Raptor OSD saves only supported text settings and preserves saved/live distinction", () => {
  const loaded = decodeOsdForPage(state);
  assert.equal(loaded.matches_saved, false);
  assert.deepEqual(buildRaptorOsdUpdate(loaded, "%F %T", "#ff0000ff", "#00ff00ff"),
    { osd: { format: "%F %T", fill_color: "#ff0000ff" } });
  assert.throws(() => buildRaptorOsdUpdate(decodeOsdForPage({ ...state, available: false }), "", "", ""), /unavailable/);
  assert.throws(() => buildRaptorOsdUpdate(decodeOsdForPage({ ...state, persistent: false }), "", "", ""), /unavailable/);
});
test("Raptor OSD rejects incomplete capability and color observations", () => {
  for (const change of [{ fields: {} }, { matches_saved: null }, { fill_color: "#ffffff" }, { format: null },
    { fields: { ...state.fields, background_color: true } }, { fields: { ...state.fields, background_color: "yes" } }]) {
    assert.throws(() => decodeOsdForPage({ ...state, ...change }));
  }
  assert.deepEqual(decodeOsdForPage(osdFixture), osdFixture);
});

test("Raptor OSD accepts independent fill, outline and background alpha", () => {
  const alphaState = decodeOsdForPage({ ...state, outline_color: "#00ff0080", background_color: "#0000ff00",
    fields: { ...state.fields, outline_color: true, fill_alpha: true, outline_alpha: true, background_color: true } });
  assert.equal(alphaState.burnin.outline_color, "#00ff0080");
  assert.equal(alphaState.burnin.background_color, "#0000ff00");
  assert.deepEqual(buildRaptorOsdUpdate(alphaState, "%T", "#ffffff00", "#00ff0080", undefined, undefined, "#0000ffff"), {
    osd: { format: "%T", fill_color: "#ffffff00", outline_color: "#00ff0080", background_color: "#0000ffff" },
  });
});

test("Raptor font size is numeric, bounded and capability gated", () => {
  const sized = { ...state, font_size: 48, fields: { ...state.fields, font_size: true } };
  const loaded = decodeOsdForPage(sized);
  assert.equal(loaded.font_size, 48);
  assert.deepEqual(buildRaptorOsdUpdate(loaded, "%F %T", "#ffffffff", "#000000ff", 32),
    { osd: { format: "%F %T", fill_color: "#ffffffff", font_size: 32 } });
  for (const fontSize of [15, 49, 24.5, NaN, undefined]) {
    assert.throws(() => buildRaptorOsdUpdate(loaded, "%T", "#ffffffff", "#000000ff", fontSize), /font size/);
    assert.throws(() => decodeOsdForPage({ ...sized, font_size: fontSize }), /font size/);
  }
  assert.throws(() => decodeOsdForPage({ ...sized, font_size: "24" }), /font size/);
  const old = decodeOsdForPage(state);
  assert.equal(old.font_size, null);
  assert.equal((buildRaptorOsdUpdate(old, "%T", "#ffffffff", "#000000ff", 32).osd as Record<string, unknown>).font_size, undefined);
});
