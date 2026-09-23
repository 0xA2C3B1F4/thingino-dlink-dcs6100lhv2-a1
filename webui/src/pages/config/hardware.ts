import {
  buildRaptorAutomation,
  buildRaptorAutomaticOutputs,
  buildRaptorConfirmation,
  buildRaptorInitialMode,
  buildRaptorIr850Policy,
  buildRaptorLoglevel,
  buildRaptorSchedule,
  buildRaptorSun,
  buildRaptorThresholds,
  buildRaptorTiming,
  decodeRaptorAutomation,
  decodeRaptorAutomaticOutputs,
  decodeRaptorConfirmation,
  decodeRaptorInitialMode,
  decodeRaptorIr850Policy,
  decodeRaptorLoglevel,
  decodeRaptorSchedule,
  decodeRaptorSun,
  decodeRaptorThresholds,
  decodeRaptorTiming,
  raptorAutomationFields,
  raptorAutomaticOutputFields,
  raptorConfirmationFields,
  raptorInitialModeFields,
  raptorIr850PolicyFields,
  raptorLoglevelFields,
  raptorScheduleFields,
  raptorSunFields,
  raptorThresholdFields,
  raptorTimingFields,
} from "./raptor-daynight";
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
  load: async (client) => decodeDayNightForPage(await client.json<unknown>(routes.config.domain("daynight"))),
  save: async (client, value, loaded) => {
    const response = await client.postJson<unknown>(routes.config.domain("daynight"), value);
    if (loaded.source === "raptor" && (typeof response !== "object" || response === null || !("persistent" in response) || response.persistent !== true)) {
      throw new TypeError("Day/night saving was not confirmed. Reload before retrying.");
    }
  },
  fields: [
    { ...bool("service_enabled", "Day/night service enabled", "Loaded at service startup. When disabled, physical controls are unavailable while OSD gain observations can continue. This is separate from pausing automatic switching."), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
    { ...select("mode", "Day/night mode", ["auto", "day", "night"]), visibleWhen: { path: "daynight_full_controls", equals: false }, enabledWhen: { path: "daynight_policy_control", equals: true } },
    { ...text("saved_mode", "Saved day/night mode"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
    { ...text("state", "Reported day/night state"), readOnly: true, description: "Reports ISP and control outputs; filter position is not measured.", visibleWhen: { path: "daynight_full_controls", equals: false } },
    { ...bool("matches_saved", "Day/night mode matches saved setting"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
    ...raptorAutomationFields,
    ...raptorAutomaticOutputFields,
    ...raptorIr850PolicyFields,
    ...raptorInitialModeFields,
    ...raptorThresholdFields,
    ...raptorTimingFields,
    ...raptorConfirmationFields,
    ...raptorScheduleFields,
    ...raptorSunFields,
    ...raptorLoglevelFields,
    bool("enabled", "Enable automatic day / night switching"),
    { path: "initial_mode", label: "Initial mode", type: "select", options: [{ label: "Automatic", value: "" }, { label: "Day", value: "day" }, { label: "Night", value: "night" }], visibleWhen: { path: "daynight_full_controls", equals: true } },
    { path: "force_mode", label: "Force mode", type: "select", options: [{ label: "Do not force", value: "" }, { label: "Day", value: "day" }, { label: "Night", value: "night" }], visibleWhen: { path: "daynight_full_controls", equals: true } },
    number("night_threshold", "Night threshold", 0, 100),
    number("day_threshold", "Day threshold", 0, 100),
    number("night_count_threshold", "Night confirmation samples", 1, 255),
    number("day_count_threshold", "Day confirmation samples", 1, 255),
    { ...number("sample_interval_ms", "Sample interval in milliseconds", 100, 60_000), visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...number("transition_delay_s", "Transition delay in seconds", 0, 300), visibleWhen: { path: "daynight_full_controls", equals: true } },
    select("loglevel", "Log level", ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"]),
    bool("controls.color", "Control color mode"),
    bool("controls.ircut", "Control IR filter"),
    bool("controls.ir850", "Control 850 nm IR LED"),
    { ...bool("schedule.enabled", "Use schedule"), visibleWhen: { path: "daynight_full_controls", equals: true } },
    { path: "schedule.start_at", label: "Day starts", type: "time", visibleWhen: { path: "daynight_full_controls", equals: true } },
    { path: "schedule.stop_at", label: "Day ends", type: "time", visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...bool("sun.enabled", "Use sunrise and sunset schedule"), visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...number("sun.latitude", "Latitude", -90, 90), step: 0.000001, visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...number("sun.longitude", "Longitude", -180, 180), step: 0.000001, visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...number("sun.sunrise_offset", "Sunrise offset (minutes)", -1_440, 1_440), visibleWhen: { path: "daynight_full_controls", equals: true } },
    { ...number("sun.sunset_offset", "Sunset offset (minutes)", -1_440, 1_440), visibleWhen: { path: "daynight_full_controls", equals: true } },
  ],
  saveTransform: buildDayNightUpdate,
  validate: (value) => {
    if (typeof value.mode === "string" && Object.keys(value).every((key) => ["mode", "thresholds", "timing", "schedule", "sun", "ir850_at_night", "initial_mode", "automation", "automatic_outputs", "confirmation", "loglevel"].includes(key))) {
      const schedule = value.schedule as JsonObject | undefined;
      const sun = value.sun as JsonObject | undefined;
      if (schedule?.enabled === true && sun?.enabled === true) return "Choose either the fixed-time schedule or sunrise/sunset, not both.";
      return undefined;
    }
    const schedule = value.schedule;
    if (typeof schedule !== "object" || schedule === null || Array.isArray(schedule)) return "Day / night schedule is incomplete.";
    if (schedule.enabled === true && (![schedule.start_at, schedule.stop_at].every((entry) => typeof entry === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(entry)))) {
      return "Enabled schedules require valid start and stop times.";
    }
    return undefined;
  },
};

for (const field of daynight.fields) {
  if (!field.readOnly && !field.enabledWhen) field.enabledWhen = { path: "daynight_full_controls", equals: true };
}

export function decodeDayNightForPage(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || !("source" in value) || value.source !== "raptor") {
    return { ...decodeDayNight(value), daynight_full_controls: true };
  }
  const root = value as JsonObject;
  const serviceEnabled = root.service_enabled ?? true;
  if (typeof serviceEnabled !== "boolean" || root.service_enabled === null) {
    throw new TypeError("Day/night service state is incomplete.");
  }
  for (const key of ["persistent", "supported", "available", "matches_saved"]) {
    if (typeof root[key] !== "boolean") throw new TypeError("Day/night state is incomplete.");
  }
  if (!serviceEnabled) {
    for (const key of ["thresholds", "timing", "schedule", "sun", "startup", "automation", "automatic_outputs", "confirmation", "loglevel"]) {
      const observation = root[key];
      if (observation && typeof observation === "object" && !Array.isArray(observation) && observation.available === true) {
        throw new TypeError("Disabled Day/night service cannot report available controls.");
      }
    }
  }
  const mode = (value: unknown): boolean => typeof value === "string" && ["auto", "day", "night"].includes(value);
  if (!mode(root.mode) || (root.saved_mode !== null && !mode(root.saved_mode)) ||
      (!serviceEnabled && root.available === true) ||
      (root.available ? (root.supported !== true || typeof root.state !== "string" || !["day", "night"].includes(root.state) || (root.mode !== "auto" && root.mode !== root.state)) : root.state !== null) ||
      root.matches_saved !== (root.available && root.mode === root.saved_mode)) {
    throw new TypeError("Day/night observation is inconsistent.");
  }
  const policy = {
    ...root,
    service_enabled: serviceEnabled,
    daynight_full_controls: false,
    daynight_policy_control: serviceEnabled && root.supported === true && root.persistent === true,
  };
  const automation = decodeRaptorAutomation(policy);
  const outputs = decodeRaptorAutomaticOutputs(automation);
  const thresholds = decodeRaptorThresholds(outputs);
  const timing = decodeRaptorTiming(thresholds);
  const confirmation = decodeRaptorConfirmation(timing);
  const schedule = decodeRaptorSchedule(confirmation);
  const sun = decodeRaptorSun(schedule);
  const ir850 = decodeRaptorIr850Policy(sun);
  return decodeRaptorLoglevel(decodeRaptorInitialMode(ir850));
}

export function buildDayNightUpdate(value: JsonObject, loaded?: JsonObject): JsonObject {
  if (value.source === "raptor") {
    if (value.service_enabled === false || loaded?.service_enabled === false) {
      throw new TypeError("Day/night service is disabled. These controls are unavailable.");
    }
    if (value.daynight_policy_control !== true || typeof value.mode !== "string" || !["auto", "day", "night"].includes(value.mode)) {
      throw new TypeError("Choose an explicit day/night mode before saving.");
    }
    const thresholds = buildRaptorThresholds(value);
    const timing = buildRaptorTiming(value);
    const schedule = buildRaptorSchedule(value);
    const sun = buildRaptorSun(value);
    const ir850AtNight = buildRaptorIr850Policy(value);
    const initialMode = buildRaptorInitialMode(value);
    const automation = buildRaptorAutomation(value);
    const automaticOutputs = buildRaptorAutomaticOutputs(value);
    const confirmation = buildRaptorConfirmation(value);
    const loglevel = buildRaptorLoglevel(value);
    if (schedule?.enabled === true && sun?.enabled === true) throw new TypeError("Choose either the fixed-time schedule or sunrise/sunset, not both.");
    const sameLoaded = (candidate: unknown, path: string): boolean => {
      if (loaded?.source !== "raptor") return false;
      const parts = path.split(".");
      let observed: unknown = loaded;
      for (const part of parts) {
        if (typeof observed !== "object" || observed === null || Array.isArray(observed)) return false;
        observed = (observed as JsonObject)[part];
      }
      const equal = (left: unknown, right: unknown): boolean => {
        if (left === right) return true;
        if (typeof left !== "object" || left === null || Array.isArray(left) ||
            typeof right !== "object" || right === null || Array.isArray(right)) return false;
        const leftObject = left as JsonObject;
        const rightObject = right as JsonObject;
        const keys = Object.keys(leftObject);
        return keys.length === Object.keys(rightObject).length && keys.every((key) => equal(leftObject[key], rightObject[key]));
      };
      return equal(candidate, observed);
    };
    const needsRetry = (path: string): boolean => !sameLoaded(true, path);
    const initialModeChanged = initialMode !== undefined &&
      (!sameLoaded(initialMode, "startup.configured") || needsRetry("startup.matches_saved"));
    const thresholdsChanged = thresholds !== undefined &&
      (!sameLoaded(thresholds.trigger, "thresholds.trigger") ||
       !sameLoaded(thresholds.values, "thresholds.values") ||
       needsRetry("thresholds.matches_saved"));
    return {
      mode: value.mode,
      ...(automation && (!sameLoaded(automation.paused, "automation.paused") || needsRetry("automation.matches_saved")) ? { automation } : {}),
      ...(automaticOutputs && ((!sameLoaded(automaticOutputs.color, "automatic_outputs.color") || !sameLoaded(automaticOutputs.ircut, "automatic_outputs.ircut")) || needsRetry("automatic_outputs.matches_saved")) ? { automatic_outputs: automaticOutputs } : {}),
      ...(confirmation && (!sameLoaded(confirmation, "confirmation.values") || needsRetry("confirmation.matches_saved")) ? { confirmation } : {}),
      ...(loglevel !== undefined && (!sameLoaded(loglevel, "loglevel.value") || needsRetry("loglevel.matches_saved")) ? { loglevel } : {}),
      ...(initialModeChanged ? { initial_mode: initialMode } : {}),
      ...(thresholdsChanged ? { thresholds } : {}),
      ...(timing && (!sameLoaded(timing, "timing.values") || needsRetry("timing.matches_saved")) ? { timing } : {}),
      ...(schedule && (!sameLoaded(schedule, "schedule.values") || needsRetry("schedule.matches_saved")) ? { schedule } : {}),
      ...(sun && (!sameLoaded(sun, "sun.values") || needsRetry("sun.matches_saved")) ? { sun } : {}),
      ...(ir850AtNight === undefined ||
          (sameLoaded(ir850AtNight, "ir850_at_night") && !needsRetry("ir850_at_night_matches_saved"))
        ? {}
        : { ir850_at_night: ir850AtNight }),
    };
  }
  const update = structuredClone(value);
  delete update.daynight_full_controls;
  delete update.daynight_policy_control;
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
