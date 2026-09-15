import type { JsonObject } from "../../api/contracts";
import type { FieldSpec } from "../../app/forms";
import { bool, number, select, text } from "./common";

type Threshold = readonly [string, string, number, number];
const groups: Record<string, readonly Threshold[]> = {
  luma: [["night_luma", "Night luminance threshold", 0, 255], ["night_gain", "Night gain threshold", 0, 2147483647], ["day_gain_pct", "Day gain percentage", 1, 100]],
  gain: [["day_threshold", "Day gain threshold", 0, 2147483647], ["night_threshold", "Night gain threshold", 0, 2147483647]],
  adc: [["adc_night", "Night ADC threshold", 0, 2147483647], ["adc_day", "Day ADC threshold", 0, 2147483647]],
  photo: [["photo_ev_day", "Day exposure threshold", 0, 2147483647], ["photo_ev_night", "Night exposure threshold", 0, 2147483647], ["photo_ev_deep", "Deep-night exposure threshold", 0, 2147483647]],
};

function validateValues(trigger: string, value: unknown): JsonObject {
  const fields = Object.hasOwn(groups, trigger) ? groups[trigger] : undefined;
  if (!fields || typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).length !== fields.length) {
    throw new TypeError("Day/night thresholds are incomplete.");
  }
  const values = value as JsonObject;
  let previous = -1;
  for (const [key, , min, max] of fields) {
    const number = values[key];
    if (typeof number !== "number" || !Number.isInteger(number) || number < min || number > max || (trigger !== "luma" && number <= previous)) {
      throw new TypeError("Day/night thresholds must be whole numbers in range and ascending where required.");
    }
    previous = number;
  }
  return structuredClone(values);
}

export function decodeRaptorThresholds(root: JsonObject): JsonObject {
  if (root.thresholds === undefined) return { ...root, daynight_threshold_control: false };
  const value = root.thresholds;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Day/night thresholds are incomplete.");
  for (const key of ["supported", "available", "matches_saved"]) {
    if (typeof value[key] !== "boolean") throw new TypeError("Day/night thresholds are incomplete.");
  }
  if (typeof value.trigger !== "string" || (value.supported && !Object.hasOwn(groups, value.trigger)) || (value.available && !value.supported)) {
    throw new TypeError("Day/night threshold capability is inconsistent.");
  }
  const values = value.available ? validateValues(value.trigger, value.values) : null;
  if (!value.available && value.values !== null) throw new TypeError("Day/night thresholds are incomplete.");
  const saved = value.saved_values === null ? null : validateValues(value.trigger, value.saved_values);
  const matches = value.available === true && saved !== null && groups[value.trigger]!.every(([key]) => values![key] === saved[key]);
  if (value.matches_saved !== matches) throw new TypeError("Day/night threshold saved-state comparison is inconsistent.");
  return { ...root, thresholds: { ...value, values, saved_values: saved }, daynight_threshold_control: value.available === true && root.persistent === true,
    daynight_thresholds_luma: value.supported === true && value.trigger === "luma",
    daynight_thresholds_gain: value.supported === true && value.trigger === "gain",
    daynight_thresholds_adc: value.supported === true && value.trigger === "adc",
    daynight_thresholds_photo: value.supported === true && value.trigger === "photo" };
}

export function buildRaptorThresholds(root: JsonObject): JsonObject | undefined {
  if (root.daynight_threshold_control !== true) return undefined;
  const value = root.thresholds as JsonObject;
  if (typeof value?.trigger !== "string") throw new TypeError("Day/night thresholds are incomplete.");
  return { trigger: value.trigger, values: validateValues(value.trigger, value.values) };
}

function timingValues(value: unknown, transitionDelaySupported: boolean): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Day/night timing is incomplete.");
  }
  const timing = value as JsonObject;
  const expectedKeys = transitionDelaySupported ? 2 : 1;
  if (Object.keys(timing).length !== expectedKeys ||
      typeof timing.sample_interval_ms !== "number" || !Number.isInteger(timing.sample_interval_ms) ||
      timing.sample_interval_ms < 50 || timing.sample_interval_ms > 10_000 ||
      (transitionDelaySupported &&
        (typeof timing.transition_delay_s !== "number" || !Number.isInteger(timing.transition_delay_s) ||
          timing.transition_delay_s < 1 || timing.transition_delay_s > 300))) {
    throw new TypeError("Day/night timing must use whole numbers in range.");
  }
  return structuredClone(timing);
}

export function decodeRaptorTiming(root: JsonObject): JsonObject {
  if (root.timing === undefined) {
    return { ...root, daynight_timing_control: false, daynight_transition_delay_control: false };
  }
  const value = root.timing;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Day/night timing is incomplete.");
  }
  for (const key of ["supported", "available", "transition_delay_supported", "matches_saved"]) {
    if (typeof value[key] !== "boolean") throw new TypeError("Day/night timing is incomplete.");
  }
  if ((value.available && !value.supported) || (value.transition_delay_supported && !value.supported)) {
    throw new TypeError("Day/night timing capability is inconsistent.");
  }
  const transitionDelaySupported = value.transition_delay_supported === true;

  let values: JsonObject | null = null;
  if (value.available) {
    if (typeof value.values !== "object" || value.values === null || Array.isArray(value.values)) {
      throw new TypeError("Day/night timing is incomplete.");
    }
    const live = value.values as JsonObject;
    if (transitionDelaySupported) {
      values = timingValues(live, true);
    } else {
      if (live.transition_delay_s !== null) throw new TypeError("Day/night timing capability is inconsistent.");
      const { transition_delay_s: _ignored, ...sampleOnly } = live;
      values = { ...timingValues(sampleOnly, false), transition_delay_s: null };
    }
  } else if (value.values !== null) {
    throw new TypeError("Day/night timing is incomplete.");
  }

  const saved = value.saved_values === null ? null : timingValues(value.saved_values, transitionDelaySupported);
  const matches = value.available === true && saved !== null && values!.sample_interval_ms === saved.sample_interval_ms &&
    (!transitionDelaySupported || values!.transition_delay_s === saved.transition_delay_s);
  if (value.matches_saved !== matches) throw new TypeError("Day/night timing saved-state comparison is inconsistent.");

  return {
    ...root,
    timing: { ...value, values, saved_values: saved },
    daynight_timing_control: value.available === true && root.persistent === true,
    daynight_transition_delay_control: value.available === true && value.transition_delay_supported === true && root.persistent === true,
    daynight_transition_delay_supported: value.transition_delay_supported === true,
  };
}

export function buildRaptorTiming(root: JsonObject): JsonObject | undefined {
  if (root.daynight_timing_control !== true) return undefined;
  const timing = root.timing as JsonObject;
  if (typeof timing?.values !== "object" || timing.values === null || Array.isArray(timing.values)) {
    throw new TypeError("Day/night timing is incomplete.");
  }
  const values = timing.values as JsonObject;
  if (root.daynight_transition_delay_control === true) return timingValues(values, true);
  const { transition_delay_s: _ignored, ...sampleOnly } = values;
  return timingValues(sampleOnly, false);
}

export const raptorThresholdFields: FieldSpec[] = [
  { ...text("thresholds.trigger", "Automatic detection method"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  ...Object.entries(groups).flatMap(([trigger, fields]) => fields.flatMap(([key, label, min, max]) => [
    { ...number(`thresholds.values.${key}`, label, min, max), visibleWhen: { path: `daynight_thresholds_${trigger}`, equals: true }, enabledWhen: { path: "daynight_threshold_control", equals: true } },
    { ...number(`thresholds.saved_values.${key}`, `Saved ${label.toLowerCase()}`, min, max), readOnly: true, visibleWhen: { path: `daynight_thresholds_${trigger}`, equals: true } },
  ])),
  { ...bool("thresholds.matches_saved", "Detection thresholds match saved settings"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export const raptorTimingFields: FieldSpec[] = [
  { ...number("timing.values.sample_interval_ms", "Detection sample interval in milliseconds", 50, 10_000), enabledWhen: { path: "daynight_timing_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("timing.saved_values.sample_interval_ms", "Saved detection sample interval", 50, 10_000), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("timing.values.transition_delay_s", "Transition delay in seconds", 1, 300), enabledWhen: { path: "daynight_transition_delay_control", equals: true }, visibleWhen: { path: "daynight_transition_delay_supported", equals: true } },
  { ...number("timing.saved_values.transition_delay_s", "Saved transition delay", 1, 300), readOnly: true, visibleWhen: { path: "daynight_transition_delay_supported", equals: true } },
  { ...bool("timing.transition_delay_supported", "Transition delay is controlled by this detection method"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("timing.matches_saved", "Detection timing matches saved settings"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

function initialMode(value: unknown): "default" | "day" | "night" {
  if (value === "default" || value === "day" || value === "night") return value;
  throw new TypeError("Startup initial mode is invalid.");
}

export function decodeRaptorInitialMode(root: JsonObject): JsonObject {
  if (root.startup === undefined) return { ...root, daynight_initial_mode_control: false };
  const value = root.startup;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Startup initial mode is incomplete.");
  for (const key of ["supported", "available", "matches_saved"]) {
    if (typeof value[key] !== "boolean") throw new TypeError("Startup initial mode is incomplete.");
  }
  if (value.restart_required !== null && typeof value.restart_required !== "boolean") throw new TypeError("Startup initial mode is incomplete.");
  const configured = initialMode(value.configured);
  const bootActive = initialMode(value.boot_active);
  const saved = value.saved === null ? null : initialMode(value.saved);
  if ((value.available && !value.supported) ||
      value.matches_saved !== (saved !== null && configured === saved) ||
      value.restart_required !== (saved === null ? null : saved !== bootActive)) {
    throw new TypeError("Startup initial mode state is inconsistent.");
  }
  return {
    ...root,
    startup: { ...value, configured, boot_active: bootActive, saved },
    daynight_initial_mode_control: value.available === true && root.persistent === true,
  };
}

export function buildRaptorInitialMode(root: JsonObject): "default" | "day" | "night" | undefined {
  if (root.daynight_initial_mode_control !== true) return undefined;
  const startup = root.startup as JsonObject;
  return initialMode(startup?.configured);
}

export const raptorInitialModeFields: FieldSpec[] = [
  { ...select("startup.configured", "Initial mode after startup", ["default", "day", "night"]),
    options: [
      { label: "Default (Day until automation takes over)", value: "default" },
      { label: "Day", value: "day" },
      { label: "Night", value: "night" },
    ],
    description: "Applied once when RIC starts. Forced mode wins, and an active schedule may replace it immediately.",
    enabledWhen: { path: "daynight_initial_mode_control", equals: true },
    visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("startup.saved", "Saved startup initial mode"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("startup.boot_active", "Startup policy loaded by this RIC process"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("startup.matches_saved", "Startup initial mode matches saved setting"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("startup.restart_required", "RIC restart required to apply saved startup mode"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

function scheduleValues(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).length !== 3) throw new TypeError("Day/night schedule is incomplete.");
  const schedule = value as JsonObject;
  const time = (entry: unknown): entry is string => typeof entry === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(entry);
  if (typeof schedule.enabled !== "boolean" || !time(schedule.start_at) || !time(schedule.stop_at)) throw new TypeError("Day/night schedule is incomplete.");
  return structuredClone(schedule);
}

export function decodeRaptorSchedule(root: JsonObject): JsonObject {
  if (root.schedule === undefined) return { ...root, daynight_schedule_control: false };
  const value = root.schedule;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Day/night schedule is incomplete.");
  for (const key of ["supported", "available", "active", "application_ok", "matches_saved"]) if (typeof value[key] !== "boolean") throw new TypeError("Day/night schedule is incomplete.");
  if ((value.available && !value.supported) || (value.active && !value.available) || (value.active && value.target === null && value.application_ok) || (value.active && value.target !== null && value.target !== "day" && value.target !== "night") || (!value.active && value.target !== null) || (value.active && value.application_ok && root.state !== value.target)) throw new TypeError("Day/night schedule capability is inconsistent.");
  const values = scheduleValues(value.values);
  const saved = value.saved_values === null ? null : scheduleValues(value.saved_values);
  const matches = saved !== null && values.enabled === saved.enabled && values.start_at === saved.start_at && values.stop_at === saved.stop_at;
  if (value.matches_saved !== matches) throw new TypeError("Day/night schedule saved-state comparison is inconsistent.");
  return { ...root, schedule: { ...value, values, saved_values: saved }, daynight_schedule_control: value.available === true && root.persistent === true };
}

export function buildRaptorSchedule(root: JsonObject): JsonObject | undefined {
  if (root.daynight_schedule_control !== true) return undefined;
  return scheduleValues((root.schedule as JsonObject).values);
}

export const raptorScheduleFields: FieldSpec[] = [
  { ...bool("schedule.values.enabled", "Use fixed-time schedule", "Fixed-time and sunrise/sunset schedules are mutually exclusive."), enabledWhen: { path: "daynight_schedule_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { path: "schedule.values.start_at", label: "Day starts", type: "time", enabledWhen: { path: "daynight_schedule_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { path: "schedule.values.stop_at", label: "Day ends", type: "time", enabledWhen: { path: "daynight_schedule_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("schedule.target", "Scheduled target"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("schedule.active", "Schedule currently owns automatic mode"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("schedule.application_ok", "Scheduled target application confirmed"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("schedule.matches_saved", "Schedule matches saved settings"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

function sunValues(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).length !== 5) {
    throw new TypeError("Sunrise/sunset settings are incomplete.");
  }
  const sun = value as JsonObject;
  if (typeof sun.enabled !== "boolean" || typeof sun.latitude !== "number" || !Number.isFinite(sun.latitude) || sun.latitude < -90 || sun.latitude > 90 ||
      typeof sun.longitude !== "number" || !Number.isFinite(sun.longitude) || sun.longitude < -180 || sun.longitude > 180 ||
      typeof sun.sunrise_offset !== "number" || !Number.isInteger(sun.sunrise_offset) || sun.sunrise_offset < -1_440 || sun.sunrise_offset > 1_440 ||
      typeof sun.sunset_offset !== "number" || !Number.isInteger(sun.sunset_offset) || sun.sunset_offset < -1_440 || sun.sunset_offset > 1_440) {
    throw new TypeError("Sunrise/sunset coordinates or offsets are invalid.");
  }
  return structuredClone(sun);
}

export function decodeRaptorSun(root: JsonObject): JsonObject {
  if (root.sun === undefined) return { ...root, daynight_sun_control: false };
  const value = root.sun;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Sunrise/sunset state is incomplete.");
  for (const key of ["supported", "available", "active", "application_ok", "matches_saved"]) {
    if (typeof value[key] !== "boolean") throw new TypeError("Sunrise/sunset state is incomplete.");
  }
  if ((value.available && !value.supported) || (value.active && !value.available) ||
      (value.target !== null && value.target !== "day" && value.target !== "night") ||
      (value.condition !== null && !["normal", "polar_day", "polar_night"].includes(String(value.condition))) ||
      (!value.active && (value.target !== null || value.condition !== null)) ||
      (value.active && value.application_ok && value.target === null) ||
      (value.active && value.application_ok && root.state !== value.target)) {
    throw new TypeError("Sunrise/sunset capability is inconsistent.");
  }
  const values = sunValues(value.values);
  const saved = value.saved_values === null ? null : sunValues(value.saved_values);
  const matches = saved !== null && Object.keys(values).every(key => values[key] === saved[key]);
  if (value.matches_saved !== matches) throw new TypeError("Sunrise/sunset saved-state comparison is inconsistent.");
  const schedule = root.schedule as JsonObject | undefined;
  const scheduleValues = schedule?.values as JsonObject | undefined;
  if (values.enabled === true && scheduleValues?.enabled === true) throw new TypeError("Choose one automatic schedule.");
  return { ...root, sun: { ...value, values, saved_values: saved }, daynight_sun_control: value.available === true && root.persistent === true };
}

export function buildRaptorSun(root: JsonObject): JsonObject | undefined {
  if (root.daynight_sun_control !== true) return undefined;
  return sunValues((root.sun as JsonObject).values);
}

export const raptorSunFields: FieldSpec[] = [
  { ...bool("sun.values.enabled", "Use sunrise and sunset schedule", "Uses coordinates only on the camera. Fixed-time and sunrise/sunset schedules are mutually exclusive."), enabledWhen: { path: "daynight_sun_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("sun.values.latitude", "Latitude", -90, 90), step: 0.000001, enabledWhen: { path: "daynight_sun_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("sun.values.longitude", "Longitude", -180, 180), step: 0.000001, enabledWhen: { path: "daynight_sun_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("sun.values.sunrise_offset", "Sunrise offset in minutes", -1_440, 1_440), enabledWhen: { path: "daynight_sun_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...number("sun.values.sunset_offset", "Sunset offset in minutes", -1_440, 1_440), enabledWhen: { path: "daynight_sun_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("sun.target", "Solar target"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("sun.condition", "Solar condition"), readOnly: true, description: "Normal, polar day or polar night. Times are daily estimates from NOAA's short fractional-year equations, not observed horizon crossings or the separate precision calculator.", visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("sun.active", "Sunrise/sunset currently owns automatic mode"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("sun.application_ok", "Solar target application confirmed"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("sun.matches_saved", "Sunrise/sunset settings match saved settings"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export function decodeRaptorIr850Policy(root: JsonObject): JsonObject {
  if (root.ir850_at_night === undefined) return { ...root, daynight_ir850_policy_control: false };
  if (typeof root.ir850_at_night !== "boolean" ||
      (root.saved_ir850_at_night !== null && typeof root.saved_ir850_at_night !== "boolean") ||
      typeof root.ir850_at_night_matches_saved !== "boolean" ||
      root.ir850_at_night_matches_saved !== (root.saved_ir850_at_night !== null && root.ir850_at_night === root.saved_ir850_at_night)) {
    throw new TypeError("Night illumination policy is inconsistent.");
  }
  return { ...root, daynight_ir850_policy_control: root.service_enabled !== false && root.supported === true && root.persistent === true };
}

export function buildRaptorIr850Policy(root: JsonObject): boolean | undefined {
  if (root.daynight_ir850_policy_control !== true) return undefined;
  if (typeof root.ir850_at_night !== "boolean") throw new TypeError("Night illumination policy is incomplete.");
  return root.ir850_at_night;
}

export const raptorIr850PolicyFields: FieldSpec[] = [
  { ...bool("ir850_at_night", "Use 850 nm illumination in Night mode", "This is the GPIO61 policy applied by a whole Day/Night mode selection. A Preview override keeps control until a mode is selected again."), enabledWhen: { path: "daynight_ir850_policy_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("saved_ir850_at_night", "Saved 850 nm Night-mode policy"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("ir850_at_night_matches_saved", "850 nm Night-mode policy matches saved setting"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export function decodeRaptorAutomation(root: JsonObject): JsonObject {
  if (root.automation === undefined) return { ...root, daynight_automation_control: false };
  const value = root.automation;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Day/night automation pause is incomplete.");
  }
  for (const key of ["supported", "available", "paused", "matches_saved"] as const) {
    if (typeof value[key] !== "boolean") {
      throw new TypeError("Day/night automation pause is incomplete.");
    }
  }
  if (value.saved_paused !== null && typeof value.saved_paused !== "boolean") {
    throw new TypeError("Day/night automation pause is incomplete.");
  }
  const matchesSaved = value.saved_paused !== null && value.paused === value.saved_paused;
  if ((value.available && !value.supported) || value.matches_saved !== matchesSaved) {
    throw new TypeError("Day/night automation pause is inconsistent.");
  }
  return { ...root, daynight_automation_control: value.available === true && root.persistent === true };
}

export function buildRaptorAutomation(root: JsonObject): JsonObject | undefined {
  if (root.daynight_automation_control !== true) return undefined;
  const value = root.automation as JsonObject;
  if (typeof value?.paused !== "boolean") throw new TypeError("Day/night automation pause is incomplete.");
  return { paused: value.paused };
}

export const raptorAutomationFields: FieldSpec[] = [
  { ...bool("automation.paused", "Pause automatic switching", "Stops sensor, fixed-time and sunrise/sunset owners. RIC and explicit Day/Night controls remain available."), enabledWhen: { path: "daynight_automation_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("automation.saved_paused", "Saved automation pause"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("automation.matches_saved", "Automation pause matches saved setting"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export function decodeRaptorAutomaticOutputs(root: JsonObject): JsonObject {
  if (root.automatic_outputs === undefined) return { ...root, daynight_automatic_outputs_control: false };
  const value = root.automatic_outputs;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Automatic Day/Night outputs are incomplete.");
  }
  for (const key of ["supported", "available", "color", "ircut", "matches_saved"] as const) {
    if (typeof value[key] !== "boolean") {
      throw new TypeError("Automatic Day/Night outputs are incomplete.");
    }
  }
  for (const key of ["color_owner", "ircut_owner"] as const) {
    const valid = root.service_enabled === false
      ? value[key] === "disabled" && value.available === false
      : value[key] === "automatic" || value[key] === "manual";
    if (!valid) {
      throw new TypeError("Automatic Day/Night output ownership is invalid.");
    }
  }
  for (const key of ["color_state", "ircut_commanded_state"] as const) {
    if ((value[key] !== null && value[key] !== "day" && value[key] !== "night") ||
        (root.service_enabled === false && value[key] !== null)) {
      throw new TypeError("Automatic Day/Night output readback is invalid.");
    }
  }
  const saved = value.saved_values;
  if (saved !== null && (
    typeof saved !== "object" ||
    Array.isArray(saved) ||
    typeof saved.color !== "boolean" ||
    typeof saved.ircut !== "boolean"
  )) {
    throw new TypeError("Saved automatic Day/Night outputs are incomplete.");
  }
  const matches = saved !== null && value.color === saved.color && value.ircut === saved.ircut;
  if ((value.available && !value.supported) || value.matches_saved !== matches) {
    throw new TypeError("Automatic Day/Night outputs are inconsistent.");
  }
  return { ...root, daynight_automatic_outputs_control: value.available === true && root.persistent === true };
}

export function buildRaptorAutomaticOutputs(root: JsonObject): JsonObject | undefined {
  if (root.daynight_automatic_outputs_control !== true) return undefined;
  const value = root.automatic_outputs as JsonObject;
  if (typeof value?.color !== "boolean" || typeof value.ircut !== "boolean") throw new TypeError("Automatic Day/Night outputs are incomplete.");
  return { color: value.color, ircut: value.ircut };
}

export const raptorAutomaticOutputFields: FieldSpec[] = [
  { ...bool("automatic_outputs.color", "Automatic transitions control color mode", "Explicit Day and Night selections still control color mode."), enabledWhen: { path: "daynight_automatic_outputs_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("automatic_outputs.ircut", "Automatic transitions control IR filter", "Explicit Day and Night selections still control the IR filter."), enabledWhen: { path: "daynight_automatic_outputs_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("automatic_outputs.color_owner", "Current color-mode owner"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("automatic_outputs.color_state", "Reported ISP color state"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("automatic_outputs.ircut_owner", "Current IR-filter owner"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("automatic_outputs.ircut_commanded_state", "Last confirmed IR-filter command"), readOnly: true, description: "The latching filter has no position sensor.", visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("automatic_outputs.matches_saved", "Automatic output policy matches saved settings"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export function decodeRaptorConfirmation(root: JsonObject): JsonObject {
  if (root.confirmation === undefined) return { ...root, daynight_confirmation_control: false };
  const value = root.confirmation;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Day/night confirmation counts are incomplete.");
  for (const key of ["supported", "available", "matches_saved"] as const) if (typeof value[key] !== "boolean") throw new TypeError("Day/night confirmation counts are incomplete.");
  const validate = (candidate: unknown): candidate is JsonObject => {
    if (typeof candidate !== "object" || candidate === null || Array.isArray(candidate)) return false;
    const counts = candidate as JsonObject;
    return [counts.day, counts.night].every(entry =>
      typeof entry === "number" && Number.isInteger(entry) && entry >= 1 && entry <= 255
    );
  };
  if (value.available ? !validate(value.values) : value.values !== null) {
    throw new TypeError("Day/night confirmation counts are incomplete.");
  }
  if (value.saved_values !== null && !validate(value.saved_values)) {
    throw new TypeError("Saved day/night confirmation counts are incomplete.");
  }
  const values = validate(value.values) ? value.values : null;
  const saved = validate(value.saved_values) ? value.saved_values : null;
  const matches = value.available === true && saved !== null && values !== null && values.day === saved.day && values.night === saved.night;
  if ((value.available && !value.supported) || value.matches_saved !== matches) {
    throw new TypeError("Day/night confirmation counts are inconsistent.");
  }
  return { ...root, daynight_confirmation_control: value.available === true && root.persistent === true, daynight_confirmation_supported: value.supported === true };
}

export function buildRaptorConfirmation(root: JsonObject): JsonObject | undefined {
  if (root.daynight_confirmation_control !== true) return undefined;
  const value = (root.confirmation as JsonObject)?.values;
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Day/night confirmation counts are incomplete.");
  for (const entry of [value.day, value.night]) {
    if (typeof entry !== "number" || !Number.isInteger(entry) || entry < 1 || entry > 255) {
      throw new TypeError("Day/night confirmation counts must be whole numbers from 1 to 255.");
    }
  }
  return { day: value.day as number, night: value.night as number };
}

export const raptorConfirmationFields: FieldSpec[] = [
  { ...number("confirmation.values.day", "Day confirmation samples", 1, 255), enabledWhen: { path: "daynight_confirmation_control", equals: true }, visibleWhen: { path: "daynight_confirmation_supported", equals: true }, description: "Both this many fresh samples and the elapsed transition delay are required." },
  { ...number("confirmation.values.night", "Night confirmation samples", 1, 255), enabledWhen: { path: "daynight_confirmation_control", equals: true }, visibleWhen: { path: "daynight_confirmation_supported", equals: true }, description: "Both this many fresh samples and the elapsed transition delay are required." },
  { ...bool("confirmation.matches_saved", "Confirmation counts match saved settings"), readOnly: true, visibleWhen: { path: "daynight_confirmation_supported", equals: true } },
  { ...bool("confirmation.supported", "Generic confirmation counts used by this detector"), readOnly: true, description: "Photo uses its own phase, ring and anti-interference counters.", visibleWhen: { path: "daynight_full_controls", equals: false } },
];

export function decodeRaptorLoglevel(root: JsonObject): JsonObject {
  if (root.loglevel === undefined) return { ...root, daynight_loglevel_control: false };
  const value = root.loglevel;
  const valid = (entry: unknown) => typeof entry === "string" && ["fatal", "error", "warn", "info", "debug", "trace"].includes(entry);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("RIC log level is incomplete.");
  }
  if (
    typeof value.supported !== "boolean" ||
    typeof value.available !== "boolean" ||
    typeof value.matches_saved !== "boolean" ||
    !valid(value.value) ||
    (value.saved_value !== null && !valid(value.saved_value))
  ) {
    throw new TypeError("RIC log level is incomplete.");
  }
  const matchesSaved = value.saved_value !== null && value.value === value.saved_value;
  if ((value.available && !value.supported) || value.matches_saved !== matchesSaved) {
    throw new TypeError("RIC log level is inconsistent.");
  }
  return { ...root, daynight_loglevel_control: value.available === true && root.persistent === true };
}

export function buildRaptorLoglevel(root: JsonObject): string | undefined {
  if (root.daynight_loglevel_control !== true) return undefined;
  const value = (root.loglevel as JsonObject)?.value;
  if (typeof value !== "string" || !["fatal", "error", "warn", "info", "debug", "trace"].includes(value)) throw new TypeError("RIC log level is invalid.");
  return value;
}

export const raptorLoglevelFields: FieldSpec[] = [
  { ...select("loglevel.value", "RIC log level", ["fatal", "error", "warn", "info", "debug", "trace"]), enabledWhen: { path: "daynight_loglevel_control", equals: true }, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...text("loglevel.saved_value", "Saved RIC log level"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
  { ...bool("loglevel.matches_saved", "RIC log level matches saved setting"), readOnly: true, visibleWhen: { path: "daynight_full_controls", equals: false } },
];
