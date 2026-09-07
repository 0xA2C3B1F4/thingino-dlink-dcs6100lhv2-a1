import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeDayNight, decodeGpio } from "../../api/decode";
import { element } from "../../app/dom";
import { renderConfigForm, type ConfigFormSpec } from "../../app/forms";
import type { GpioHardwareIo } from "../../api/contracts";
import { bool, text, number, select } from "./common";

export const daynight: ConfigFormSpec = {
  eyebrow: "Settings / day and night",
  title: "Day and night automation",
  description: "Thresholds, schedule and physical controls used when switching camera mode.",
  endpoint: routes.config.domain("daynight"),
  decode: decodeDayNight,
  fields: [
    bool("enabled", "Enable automatic day / night switching"),
    { path: "initial_mode", label: "Initial mode", type: "select", options: [{ label: "Automatic", value: "" }, { label: "Day", value: "day" }, { label: "Night", value: "night" }] },
    { path: "force_mode", label: "Force mode", type: "select", options: [{ label: "Do not force", value: "" }, { label: "Day", value: "day" }, { label: "Night", value: "night" }] },
    number("night_threshold", "Night threshold", 0, 100),
    number("day_threshold", "Day threshold", 0, 100),
    number("night_count_threshold", "Night confirmation samples", 1, 255),
    number("day_count_threshold", "Day confirmation samples", 1, 255),
    number("sample_interval_ms", "Sample interval in milliseconds", 100, 60_000),
    number("transition_delay_s", "Transition delay in seconds", 0, 300),
    select("loglevel", "Log level", ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"]),
    bool("controls.color", "Control color mode"),
    bool("controls.ircut", "Control IR filter"),
    bool("controls.ir850", "Control 850 nm IR LED"),
    bool("schedule.enabled", "Use schedule"),
    { path: "schedule.start_at", label: "Day starts", type: "time" },
    { path: "schedule.stop_at", label: "Day ends", type: "time" },
    bool("sun.enabled", "Use sunrise and sunset schedule"),
    { ...number("sun.latitude", "Latitude", -90, 90), step: 0.000001 },
    { ...number("sun.longitude", "Longitude", -180, 180), step: 0.000001 },
    number("sun.sunrise_offset", "Sunrise offset (minutes)", -1_440, 1_440),
    number("sun.sunset_offset", "Sunset offset (minutes)", -1_440, 1_440),
  ],
  saveTransform: buildDayNightUpdate,
  validate: (value) => {
    const schedule = value.schedule;
    if (typeof schedule !== "object" || schedule === null || Array.isArray(schedule)) return "Day / night schedule is incomplete.";
    if (schedule.enabled === true && (![schedule.start_at, schedule.stop_at].every((entry) => typeof entry === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(entry)))) {
      return "Enabled schedules require valid start and stop times.";
    }
    return undefined;
  },
};

export function buildDayNightUpdate(value: JsonObject): JsonObject {
  const update = structuredClone(value);
  const schedule = update.schedule;
  if (typeof schedule !== "object" || schedule === null || Array.isArray(schedule)) throw new TypeError("Day / night schedule is incomplete.");
  if (schedule.enabled !== true) {
    delete schedule.start_at;
    delete schedule.stop_at;
  }
  return update;
}

function gpioSpec(onLoaded: (value: JsonObject) => void): ConfigFormSpec {
  return {
    eyebrow: "Settings / GPIO",
    title: "Hardware controls",
    description: "D-Link A1 board assignments. Pin numbers are fixed and system-owned lines are read-only.",
    endpoint: routes.config.domain("gpio"),
    decode: decodeGpio,
    fields: [
      { ...text("ircut_pins", "IR-cut GPIOs"), readOnly: true, description: "Fixed board outputs managed by Day / Night and Preview controls." },
      { ...text("ir850_pin", "850 nm IR LED GPIO"), readOnly: true, description: "Fixed board output managed by Day / Night and Preview controls." },
      { path: "startup_indicator", label: "Startup indicator", type: "select", options: [
        { label: "Off", value: "off" },
        { label: "Green", value: "green" },
        { label: "Red", value: "red" },
      ] },
    ],
    loadTransform: (value) => {
      const ledValue = value.led as JsonObject;
      return { ...structuredClone(value), ircut_pins: "GPIO49 / GPIO50", ir850_pin: "GPIO61", startup_indicator: ledValue.startup_indicator ?? "off" };
    },
    saveTransform: buildGpioUpdate,
    onLoaded,
  };
}

export function buildGpioUpdate(value: JsonObject): JsonObject {
  const startupIndicator = value.startup_indicator;
  if (!new Set(["off", "green", "red"]).has(String(startupIndicator))) throw new TypeError("Startup indicator must be Off, Green or Red.");
  return { startup_indicator: startupIndicator as "off" | "green" | "red" };
}

function renderGpioHardwareIo(container: HTMLElement, value: JsonObject): void {
  const entries = value.hardware_io as unknown as GpioHardwareIo[];
  const table = element("table", { className: "tools-table" });
  const head = element("tr");
  for (const label of ["GPIO", "Function", "Direction", "Owner", "Access"]) head.append(element("th", { text: label, attrs: { scope: "col" } }));
  const thead = element("thead"); thead.append(head);
  const body = element("tbody");
  for (const entry of entries) {
    const row = element("tr");
    for (const text of [`GPIO${entry.gpio}`, entry.function, entry.direction, entry.owner, "System-owned · read-only"]) row.append(element("td", { text }));
    body.append(row);
  }
  table.append(thead, body);
  container.replaceChildren(
    element("h2", { text: "Hardware I/O map" }),
    element("p", { text: "The complete verified D-Link A1 map includes boot-critical and system-owned lines. Pin assignments cannot be changed here; physical controls remain on their dedicated Preview, Day / Night, audio and system paths." }),
    element("div", { className: "tools-wide", children: [table] }),
    element("p", { className: "tools-muted", text: "No microphone GPIO is listed because an exact microphone pin has not been verified." }),
  );
}

export function renderGpioPage(client: ApiClient): { node: HTMLElement; cleanup: () => void } {
  const hardware = element("section", { className: "card tools-panel", attrs: { "aria-live": "polite" } });
  hardware.append(element("p", { text: "Loading verified Hardware I/O map…" }));
  const rendered = renderConfigForm(client, gpioSpec((value) => renderGpioHardwareIo(hardware, value)));
  rendered.node.append(hardware);
  return rendered;
}
