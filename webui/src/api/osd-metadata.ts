import type {
  JsonObject,
  OsdEntry,
  RaptorOsdMetadataConfig,
  RaptorOsdMetadataEntry,
} from "./contracts";

const types = new Set<OsdEntry["type"]>([
  "text",
  "gain",
  "hostname",
  "ipaddress",
  "timestamp",
  "uptime",
]);
const encoder = new TextEncoder();

function object(value: unknown, fields: string[], label: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} is incomplete.`);
  }
  const result = value as JsonObject;
  if (Object.keys(result).length !== fields.length || fields.some((field) => !(field in result))) {
    throw new TypeError(`${label} is incomplete.`);
  }
  return result;
}

function boundedText(value: unknown, maximum: number, label: string): string {
  const invalid =
    typeof value !== "string" ||
    value.length === 0 ||
    encoder.encode(value).length > maximum ||
    /[\u0000-\u001f\u007f-\u009f]/u.test(value);
  if (invalid) {
    throw new TypeError(`${label} is invalid.`);
  }
  return value;
}

function validPercentFormat(type: OsdEntry["type"], format: string): boolean {
  if (type === "text") return true;
  let replacements = 0;
  for (let offset = 0; offset < format.length; offset += 1) {
    if (format[offset] !== "%") continue;
    if (format[offset + 1] === "%") {
      offset += 1;
      continue;
    }
    if (["gain", "hostname", "ipaddress"].includes(type)) {
      if (format[offset + 1] !== "s") return false;
      replacements += 1;
      offset += 1;
    } else if (type === "timestamp") {
      if (!"FTYmdHMSZzjabB".includes(format[offset + 1] ?? "")) return false;
      offset += 1;
    } else {
      const token = format.slice(offset + 1, offset + 5).startsWith("02lu")
        ? "02lu"
        : format.slice(offset + 1, offset + 3);
      if (token !== "lu" && token !== "02lu") return false;
      replacements += 1;
      offset += token.length;
    }
  }
  return type === "timestamp" || replacements === (type === "uptime" ? 3 : 1);
}

function entry(value: unknown, readback: boolean): RaptorOsdMetadataEntry {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("OSD metadata entry is incomplete.");
  }
  const source = value as JsonObject;
  const type = source.type;
  const expected = readback ? (type === "gain" ? 6 : 5) : 4;
  if (
    Object.keys(source).length !== expected ||
    typeof type !== "string" ||
    !types.has(type as OsdEntry["type"])
  ) {
    throw new TypeError("OSD metadata entry is incomplete.");
  }
  const name = boundedText(source.name, 31, "OSD metadata name");
  if (!/^[A-Za-z0-9_.-]+$/u.test(name)) {
    throw new TypeError("OSD metadata name is invalid.");
  }
  const format = boundedText(source.format, 95, "OSD metadata format");
  if (!validPercentFormat(type as OsdEntry["type"], format)) {
    throw new TypeError("OSD metadata format is invalid.");
  }
  if (typeof source.position !== "string" || !/^-?\d+,-?\d+$/u.test(source.position)) {
    throw new TypeError("OSD metadata position is invalid.");
  }
  const coordinates = source.position.split(",").map(Number);
  if (
    coordinates.length !== 2 ||
    coordinates.some((coordinate) => !Number.isInteger(coordinate) || Math.abs(coordinate) > 8192)
  ) {
    throw new TypeError("OSD metadata position is invalid.");
  }
  if (readback && typeof source.available !== "boolean") {
    throw new TypeError("OSD metadata availability is incomplete.");
  }
  if (
    type === "gain" &&
    readback &&
    (source.available !== false || source.unavailable_reason !== "gain producer missing")
  ) {
    throw new TypeError("OSD gain availability is incomplete.");
  }
  if (type !== "gain" && readback && (source.available !== true || "unavailable_reason" in source)) {
    throw new TypeError("OSD metadata availability is incomplete.");
  }
  return {
    name,
    type: type as OsdEntry["type"],
    format,
    position: source.position,
    available: readback ? (source.available as boolean) : true,
    ...(type === "gain" && readback
      ? { unavailable_reason: "gain producer missing" as const }
      : {}),
  };
}

function id(value: unknown, allowEmpty: boolean): string {
  if (
    typeof value !== "string" ||
    ((!allowEmpty || value !== "") && !/^[0-9a-fA-F]{16}$/u.test(value))
  ) {
    throw new TypeError("OSD metadata ID is invalid.");
  }
  return value;
}

export function decodeRaptorOsdMetadata(value: unknown): RaptorOsdMetadataConfig {
  const root = object(
    value,
    ["source", "persistent", "supported", "confirmed", "saved", "published"],
    "OSD metadata state",
  );
  if (
    root.source !== "raptor" ||
    root.persistent !== true ||
    root.supported !== true ||
    typeof root.confirmed !== "boolean"
  ) {
    throw new TypeError("OSD metadata state is incomplete.");
  }
  const saved = object(root.saved, ["available", "id", "enabled", "entries"], "Saved OSD metadata");
  if (
    typeof saved.available !== "boolean" ||
    (saved.available ? typeof saved.enabled !== "boolean" : saved.enabled !== null) ||
    !Array.isArray(saved.entries) ||
    saved.entries.length > 16
  ) {
    throw new TypeError("Saved OSD metadata is incomplete.");
  }
  const entries = saved.entries.map((item) => entry(item, true));
  if (!saved.available && entries.length !== 0) throw new TypeError("Saved OSD metadata is incomplete.");
  if (new Set(entries.map((item) => item.name)).size !== entries.length) {
    throw new TypeError("OSD metadata names must be unique.");
  }
  const published = object(
    root.published,
    ["id", "generation", "status", "fresh", "matches_saved"],
    "Published OSD metadata",
  );
  if (
    !Number.isSafeInteger(published.generation) ||
    (published.generation as number) < 0 ||
    !Number.isSafeInteger(published.status) ||
    typeof published.fresh !== "boolean" ||
    typeof published.matches_saved !== "boolean"
  ) {
    throw new TypeError("Published OSD metadata is incomplete.");
  }
  const savedId = id(saved.id, !saved.available);
  const publishedId = id(published.id, true);
  const confirmed =
    saved.available &&
    published.fresh &&
    published.matches_saved &&
    published.status === 0 &&
    savedId === publishedId;
  if (root.confirmed !== confirmed || (!saved.available && published.matches_saved)) {
    throw new TypeError("OSD metadata confirmation is inconsistent.");
  }
  return {
    source: "raptor",
    persistent: true,
    supported: true,
    confirmed: root.confirmed,
    saved: {
      available: saved.available,
      id: savedId,
      enabled: saved.enabled as boolean | null,
      entries,
    },
    published: {
      id: publishedId,
      generation: published.generation as number,
      status: published.status as number,
      fresh: published.fresh,
      matches_saved: published.matches_saved,
    },
  };
}

export function buildRaptorOsdMetadataUpdate(
  enabled: boolean,
  values: Array<Pick<RaptorOsdMetadataEntry, "name" | "type" | "format" | "position">>,
): JsonObject {
  if (typeof enabled !== "boolean" || !Array.isArray(values) || values.length > 16) {
    throw new TypeError("OSD metadata settings are invalid.");
  }
  const entries = values.map((value) => {
    const parsed = entry(value, false);
    return { name: parsed.name, type: parsed.type, format: parsed.format, position: parsed.position };
  });
  if (new Set(entries.map((item) => item.name)).size !== entries.length) {
    throw new TypeError("Each OSD entry needs a unique non-empty name.");
  }
  return { enabled, entries };
}
