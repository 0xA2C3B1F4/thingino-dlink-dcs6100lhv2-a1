import type { GpioHardwareIo, JsonObject, OsdConfig, OsdEntry } from "../api/contracts";
import { ApiClient } from "../api/client";
import { ControlApi } from "../api/control";
import { routes } from "../api/routes";
import {
  decodeAccess, decodeAdmin, decodeAudio, decodeDayNight, decodeGpio, decodeHomeAssistant, decodeImage, decodeImagingRuntime,
  decodeMotion, decodeNetwork, decodeOsd, decodePrivacy, decodeRecorder,
  decodeRemoteLogging, decodeStream, decodeTime, decodeWebui, decodeWifiScan,
} from "../api/decode";
import { button, element, setMessage, statusMessage, switchField } from "../app/dom";
import { renderConfigForm, type ConfigFormSpec, type FieldOption, type FieldSpec } from "../app/forms";
import { applyThemePreference, type ThemePreference } from "../app/shell";
import type { PageId } from "../app/navigation";

const bool = (path: string, label: string, description?: string): FieldSpec => ({ path, label, type: "checkbox", ...(description ? { description } : {}) });
const text = (path: string, label: string, required = false): FieldSpec => ({ path, label, type: "text", required });
const number = (path: string, label: string, min?: number, max?: number): FieldSpec => ({ path, label, type: "number", ...(min !== undefined ? { min } : {}), ...(max !== undefined ? { max } : {}) });
const select = (path: string, label: string, values: readonly (string | number)[], valueType: "number" | "string" = "string"): FieldSpec => ({
  path, label, type: "select", valueType, options: values.map((value) => ({ label: String(value), value: String(value) })),
});
const secret = (path: string, label: string, description: string): FieldSpec => ({ path, label, type: "password", writeOnlySecret: true, description });
const section = (name: string, fields: FieldSpec[]): FieldSpec[] => fields.map((field, index) => index === 0 ? { ...field, section: name } : field);

const primaryTimezoneRegions = new Set([
  "Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic",
  "Australia", "Europe", "Indian", "Pacific",
]);

export function visibleTimezoneOptions(options: FieldOption[], currentValue: unknown): FieldOption[] {
  const current = typeof currentValue === "string" ? currentValue : "";
  return options.filter(({ value }) => {
    if (value === current || value === "Etc/UTC") return true;
    const [region, city] = value.split("/");
    return Boolean(city) && primaryTimezoneRegions.has(region!);
  });
}

export function resolveTimezoneSearch(query: unknown, options: unknown): string | undefined {
  if (typeof query !== "string" || !Array.isArray(options)) return undefined;
  const normalized = query.trim().toLocaleLowerCase("en-US");
  if (!normalized) return undefined;
  const names = options.flatMap((option) => {
    if (typeof option === "string") return [option];
    if (typeof option !== "object" || option === null || Array.isArray(option)) return [];
    const name = (option as { name?: unknown }).name;
    return typeof name === "string" ? [name] : [];
  });
  const exact = names.filter((name) => {
    const folded = name.toLocaleLowerCase("en-US");
    return folded === normalized || folded.split("/").at(-1) === normalized;
  });
  if (exact.length === 1) return exact[0];
  const partial = names.filter((name) => name.toLocaleLowerCase("en-US").includes(normalized));
  return partial.length === 1 ? partial[0] : undefined;
}
const prudyntLoad = async (
  client: ApiClient,
  domains: ReadonlyArray<[string, (value: unknown) => JsonObject]>,
): Promise<JsonObject> => {
  const values = await Promise.all(domains.map(async ([domain, decode]) => decode(await client.json<unknown>(routes.prudynt.domain(domain)))));
  const result: JsonObject = {};
  domains.forEach(([domain], index) => { result[domain] = values[index]!; });
  return result;
};

const network: ConfigFormSpec = {
  eyebrow: "Settings / network",
  title: "Network settings",
  description: "Local addresses, wireless credentials and interface policy. Saving may interrupt this browser connection.",
  endpoint: routes.config.network,
  decode: decodeNetwork,
  groupSections: true,
  fields: [
    ...section("Wi-Fi network", [
      text("wifi.ssid", "Network name (SSID)"),
      { ...text("wifi.bssid", "Preferred access point (BSSID)"), pattern: "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|" },
      secret("wifi.password", "Wi-Fi password", "Leave blank to keep the stored password. The camera never returns or masks the current secret."),
    ]),
    ...section("Wireless addressing", [
      bool("interfaces.wlan0.enabled", "Enable Wi-Fi"),
      bool("interfaces.wlan0.dhcp", "Obtain an IPv4 address automatically (DHCP)"),
      { ...text("interfaces.wlan0.address", "IPv4 address"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.netmask", "Netmask"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.gateway", "Gateway"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.broadcast", "Broadcast address"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.mac", "Wi-Fi MAC address"), readOnly: true },
      { ...bool("interfaces.wlan0.link_up", "Wi-Fi link active"), readOnly: true },
    ]),
    ...section("Device identity and DNS", [
      { ...text("hostname", "Hostname", true), pattern: "[A-Za-z0-9][A-Za-z0-9.-]{0,62}", maxLength: 63 },
      text("dns.primary", "Primary DNS"),
      text("dns.secondary", "Secondary DNS"),
    ]),
  ],
  saveTransform: (value) => {
    const body = structuredClone(value);
    const wifi = body.wifi as JsonObject;
    delete wifi.password_set;
    const wifiAp = body.wifi_ap as JsonObject;
    const interfaces = body.interfaces as JsonObject;
    const wlan0 = interfaces.wlan0 as JsonObject;
    return {
      ...body,
      wifi_ap: { ...wifiAp, enabled: false },
      interfaces: { ...interfaces, wlan0: { ...wlan0, ipv6: false } },
    };
  },
};

const time: ConfigFormSpec = {
  eyebrow: "Settings / time",
  title: "Time and timezone",
  description: "Choose the camera timezone. The local clock is read back after every load and save so the change is visible without a reboot.",
  endpoint: routes.config.time,
  decode: decodeTime,
  groupSections: true,
  loadTransform: loadTimeConfig,
  fields: [
    ...section("Timezone", [
      {
        path: "timezone",
        label: "Timezone",
        type: "search",
        optionsFrom: "timezone_options",
        optionsFilter: visibleTimezoneOptions,
        placeholder: "Search by city, for example Helsinki",
        required: true,
        description: "Search by city or choose a Region/City IANA timezone. Technical fixed-offset aliases stay hidden unless one is currently selected.",
      },
      { ...text("current_local_time", "Camera local time", true), readOnly: true, description: "Read-only time reported by the camera after the last load." },
    ]),
    ...section("Advanced", [
      bool("dhcp_ignore_timezone", "Ignore DHCP timezone"),
      text("ntp_server_0", "NTP server 1"),
      text("ntp_server_1", "NTP server 2"),
      text("ntp_server_2", "NTP server 3"),
      text("ntp_server_3", "NTP server 4"),
    ]),
  ],
  saveTransform: buildTimeUpdate,
  validate: (value) => {
    const servers = [value.ntp_server_0, value.ntp_server_1, value.ntp_server_2, value.ntp_server_3];
    return servers.some((server) => typeof server === "string" && server.trim()) ? undefined : "Configure at least one NTP server.";
  },
};

/**
 * Build the canonical time mutation from the visible form model.  The
 * read-only camera clock and any response-only metadata are never sent back.
 * The backend remains the authority for the IANA -> /etc/TZ mapping.
 */
export function buildTimeUpdate(value: JsonObject): JsonObject {
  const next = structuredClone(value);
  const timezone = resolveTimezoneSearch(next.timezone, next.timezone_options);
  delete next.current_local_time;
  delete next.timezone_options;
  delete next.current_unix_time;
  delete next.current_unix_ms;
  delete next.local_time;
  delete next.sync_status_raw_base64;
  delete next.tz_name;
  delete next.tz_data;
  if (!timezone) throw new TypeError("Choose one supported timezone from the suggestions.");
  next.timezone = timezone;
  return { ...next, action: "update" };
}

function unixMilliseconds(value: JsonObject): number {
  if (typeof value.current_unix_ms === "number") return value.current_unix_ms;
  if (typeof value.current_unix_time === "number") return value.current_unix_time * 1000;
  throw new TypeError("Camera did not return a current clock value.");
}

export function cameraLocalTime(value: JsonObject): string {
  const timezone = value.timezone;
  if (typeof timezone !== "string" || !timezone) throw new TypeError("Camera timezone is missing.");
  let formatter: Intl.DateTimeFormat;
  try {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
      timeZoneName: "short",
    });
  } catch {
    throw new TypeError(`Unsupported camera timezone: ${timezone}`);
  }
  const parts = Object.fromEntries(formatter.formatToParts(new Date(unixMilliseconds(value))).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} ${parts.timeZoneName ?? ""}`.trim();
}

export function loadTimeConfig(value: JsonObject): JsonObject {
  const next = structuredClone(value);
  next.current_local_time = cameraLocalTime(next);
  return next;
}

const access: ConfigFormSpec = {
  eyebrow: "Settings / RTSP and ONVIF",
  title: "Media access",
  description: "Credentials and endpoints for RTSP clients and ONVIF discovery.",
  endpoint: routes.config.access,
  decode: decodeAccess,
  fields: [
    text("username", "RTSP / ONVIF username"),
    secret("password", "New RTSP / ONVIF password", "Updates RTSP and ONVIF access together. Leave blank to keep the current password."),
    number("rtsp_port", "RTSP port", 1, 65535),
    { ...text("rtsp_ch0", "Main stream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...text("rtsp_ch1", "Substream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...text("rtsp_mic", "Microphone stream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...number("onvif_port", "ONVIF port", 1, 65535), readOnly: true },
    { ...bool("onvif_enabled", "ONVIF active through same-origin ingress"), readOnly: true },
  ],
  saveTransform: buildAccessUpdate,
};

export function buildAccessUpdate(value: JsonObject): JsonObject {
  const update: JsonObject = { onvif_port: 80, onvif_enabled: true };
  for (const field of ["username", "rtsp_ch0", "rtsp_ch1", "rtsp_mic"] as const) {
    if (typeof value[field] === "string" && value[field].trim()) update[field] = value[field];
  }
  if (typeof value.password === "string" && value.password) update.password = value.password;
  if (typeof value.rtsp_port === "number") update.rtsp_port = value.rtsp_port;
  return update;
}

const webui: ConfigFormSpec = {
  eyebrow: "Settings / Web interface",
  title: "Web interface",
  description: "Session policy and interface preferences shared by browsers using this camera.",
  endpoint: routes.config.webui,
  decode: decodeWebui,
  onLoaded: (value) => window.dispatchEvent(new CustomEvent("thingino:webui-config", { detail: {
    track_focus: value.track_focus === true,
    focus_timeout: typeof value.focus_timeout === "number" ? value.focus_timeout : 0,
  } })),
  fields: [
    { ...text("username", "Management username"), readOnly: true },
    { path: "theme", label: "Default theme", type: "select", options: [
      { label: "Light", value: "light" },
      { label: "Dark", value: "dark" },
      { label: "Automatic", value: "auto" },
    ] },
    text("auth_bypass_ips", "Trusted / bypass IP addresses"),
    bool("paranoid", "Require authentication for trusted LAN clients"),
    bool("track_focus", "Track Preview window focus"),
    number("focus_timeout", "Stop Preview after losing focus (seconds)", 0, 300),
  ],
  saveTransform: buildWebuiUpdate,
};

export function buildWebuiUpdate(value: JsonObject): JsonObject {
  const update = { ...value };
  delete update.username;
  return update;
}

const admin: ConfigFormSpec = {
  eyebrow: "Settings / admin profile",
  title: "Admin profile",
  description: "Contact values used by local notification and service integrations.",
  endpoint: routes.config.admin,
  decode: decodeAdmin,
  fields: [text("name", "Name"), text("email", "Email"), text("telegram", "Telegram"), text("discord", "Discord")],
};

const logging: ConfigFormSpec = {
  eyebrow: "Settings / remote logging",
  title: "Remote logging",
  description: "Forward local system logs to a server on the trusted network.",
  endpoint: routes.config.remoteLogging,
  decode: decodeRemoteLogging,
  fields: [bool("enabled", "Forward logs to the remote server"), text("host", "Log server"), number("port", "Port", 1, 65535), bool("file", "Keep a local log file")],
};

const daynight: ConfigFormSpec = {
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

const audio: ConfigFormSpec = {
  eyebrow: "Settings / audio",
  title: "Audio",
  description: "Microphone processing, speaker level and audio stream state.",
  endpoint: routes.prudynt.domain("audio"),
  saveEndpoint: routes.prudynt.command,
  load: (client) => prudyntLoad(client, [["audio", decodeAudio]]),
  fields: [
    bool("audio.mic_enabled", "Enable microphone input"),
    select("audio.mic_format", "Microphone codec", ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"]),
    number("audio.mic_vol", "Microphone volume", -30, 120),
    number("audio.mic_gain", "Microphone gain", 0, 31),
    number("audio.mic_alc_gain", "Microphone ALC gain", 0, 7),
    number("audio.mic_noise_suppression", "Noise suppression", 0, 3),
    bool("audio.mic_agc_enabled", "Enable automatic gain control"),
    number("audio.mic_agc_compression_gain_db", "AGC compression gain dB", 0, 90),
    number("audio.mic_agc_target_level_dbfs", "AGC target level", 0, 31),
    bool("audio.mic_high_pass_filter", "Enable microphone high-pass filter"),
    bool("audio.mic_is_digital", "Use digital microphone input"),
    bool("audio.force_stereo", "Force stereo output"),
    number("audio.buffer_warn_frames", "Buffer warning threshold (frames)", 10, 1_000),
    number("audio.buffer_cap_frames", "Buffer capacity (frames)", 10, 1_000),
    bool("audio.tap_enabled", "Write microphone tap for local diagnostics"),
    { ...text("audio.tap_path", "Microphone tap path"), enabledWhen: { path: "audio.tap_enabled", equals: true } },
    bool("audio.spk_enabled", "Enable speaker output"),
    number("audio.spk_vol", "Speaker volume", -30, 120),
    number("audio.spk_gain", "Speaker gain", 0, 31),
  ],
};

const imaging: ConfigFormSpec = {
  eyebrow: "Streamer / image",
  title: "Image quality",
  description: "Shared sensor and ISP settings applied before both stream encoders. Stream-specific resolution, frame rate and encoding remain on Video streams.",
  endpoint: routes.imaging,
  load: async (client) => {
    const image = decodeImage(await client.json<unknown>(routes.prudynt.domain("image")));
    const imagingRuntime = decodeImagingRuntime(await client.json<unknown>(routes.imaging));
    return { image, imaging_runtime: imagingRuntime };
  },
  loadTransform: (value) => {
    const view = structuredClone(value);
    const image = view.image as JsonObject;
    const runtime = ((view.imaging_runtime as JsonObject).fields ?? {}) as JsonObject;
    for (const [runtimeName, configName] of Object.entries(imagingFieldMap)) {
      const field = runtime[runtimeName];
      if (typeof field === "object" && field !== null && !Array.isArray(field) && typeof field.value === "number") {
        image[configName] = field.value;
      }
    }
    return view;
  },
  save: saveImaging,
  refreshStreamerPreview: true,
  fields: [
    { ...number("image.brightness", "Brightness"), rangeFrom: "imaging_runtime.fields.brightness" },
    { ...number("image.contrast", "Contrast"), rangeFrom: "imaging_runtime.fields.contrast" },
    { ...number("image.sharpness", "Sharpness"), rangeFrom: "imaging_runtime.fields.sharpness" },
    { ...number("image.saturation", "Saturation"), rangeFrom: "imaging_runtime.fields.saturation" },
    { ...number("image.backlight_compensation", "Backlight compensation"), rangeFrom: "imaging_runtime.fields.backlight" },
    { ...number("image.drc_strength", "Dynamic range strength"), rangeFrom: "imaging_runtime.fields.wide_dynamic_range" },
    { ...number("image.highlight_depress", "Highlight tone"), rangeFrom: "imaging_runtime.fields.tone" },
    { ...number("image.defog_strength", "Defog strength"), rangeFrom: "imaging_runtime.fields.defog" },
    { ...number("image.sinter_strength", "Noise reduction"), rangeFrom: "imaging_runtime.fields.noise_reduction" },
    number("image.hue", "Hue", 0, 255), number("image.dpc_strength", "Defective-pixel correction", 0, 255),
    {
      path: "image.core_wb_mode", label: "White-balance mode", type: "select", valueType: "number",
      options: [
        ["Automatic", 0], ["Manual", 1], ["Daylight", 2], ["Cloudy", 3], ["Incandescent", 4],
        ["Fluorescent", 5], ["Twilight", 6], ["Shade", 7], ["Warm fluorescent", 8], ["Custom", 9],
      ].map(([label, value]) => ({ label: String(label), value: String(value) })),
    }, number("image.wb_bgain", "White-balance blue gain", 0, 1024),
    number("image.wb_rgain", "White-balance red gain", 0, 1024), number("image.ae_compensation", "Exposure compensation", 0, 255),
    bool("image.hflip", "Flip image horizontally"), bool("image.vflip", "Flip image vertically"),
  ],
};

const imagingFieldMap: Record<string, string> = {
  brightness: "brightness",
  contrast: "contrast",
  sharpness: "sharpness",
  saturation: "saturation",
  backlight: "backlight_compensation",
  wide_dynamic_range: "drc_strength",
  tone: "highlight_depress",
  defog: "defog_strength",
  noise_reduction: "sinter_strength",
};

export function buildImagingRequests(value: JsonObject, loaded: JsonObject): { live: JsonObject; persist: JsonObject } {
  const image = value.image;
  const metadata = ((loaded.imaging_runtime as JsonObject).fields ?? {}) as JsonObject;
  if (typeof image !== "object" || image === null || Array.isArray(image)) throw new TypeError("Image configuration is incomplete.");
  const live: JsonObject = {};
  for (const [runtimeName, configName] of Object.entries(imagingFieldMap)) {
    const field = metadata[runtimeName];
    const next = image[configName];
    if (typeof field === "object" && field !== null && !Array.isArray(field) && field.supported === true && typeof next === "number") {
      live[runtimeName] = next;
    }
  }
  return { live, persist: { image } };
}

async function saveImaging(client: ApiClient, value: JsonObject, loaded: JsonObject): Promise<void> {
  const requests = buildImagingRequests(value, loaded);
  if (Object.keys(requests.live).length) decodeImagingRuntime(await client.postJson<unknown>(routes.imaging, requests.live));
  await client.postJson<unknown>(routes.prudynt.command, requests.persist);
}

const streams: ConfigFormSpec = {
  eyebrow: "Streamer / streams",
  title: "Video streams",
  description: "Main and substream encoding. Save only values supported by the current media runtime.",
  endpoint: routes.prudynt.domain("stream0"),
  saveEndpoint: routes.prudynt.command,
  refreshStreamerPreview: true,
  groupSections: true,
  cardGroups: true,
  cardFocusStreams: true,
  cardState: (card, value) => {
    const stream = value[card];
    if (typeof stream !== "object" || stream === null || Array.isArray(stream) || stream.enabled !== true) return { label: "Disabled", state: "disabled" };
    if (typeof stream.fps !== "number" || stream.fps <= 0 || stream.buffers === -1) return { label: "On, no frames", state: "inactive" };
    return { label: "Active", state: "enabled" };
  },
  cardSummary: (card, value) => {
    const stream = value[card];
    if (typeof stream !== "object" || stream === null || Array.isArray(stream)) return "Unavailable";
    const width = stream.width;
    const height = stream.height;
    const format = stream.format === "H264" ? "H.264" : stream.format === "H265" ? "H.265" : String(stream.format ?? "Video");
    const fps = typeof stream.fps === "number" ? stream.fps : null;
    let activity = "Disabled";
    if (stream.enabled === true && (fps === null || fps <= 0)) activity = "Frame rate is 0; set 1–30 fps to start";
    else if (stream.enabled === true && stream.buffers === -1) activity = "Buffers are disabled; set 1 or more to start";
    else if (stream.enabled === true) activity = `${fps} fps`;
    return `${width} × ${height} · ${format} · ${activity}`;
  },
  load: (client) => prudyntLoad(client, [["stream0", (value) => decodeStream(value, "stream0")], ["stream1", (value) => decodeStream(value, "stream1")]]),
  fields: [
    { ...bool("stream0.enabled", "Enable stream", "An enabled stream requires 1–30 fps and at least one buffer."), card: "stream0", section: "Codec and resolution" },
    { ...number("stream0.width", "Width", 1, 16_384), card: "stream0" },
    { ...number("stream0.height", "Height", 1, 16_384), card: "stream0" },
    { ...select("stream0.format", "Codec", ["H264", "H265"]), card: "stream0" },
    { ...number("stream0.fps", "Frames per second", 0, 30), description: "Use 1–30 while enabled; 0 is reserved for a disabled stream.", card: "stream0", section: "Frame rate and rate control" },
    { ...select("stream0.mode", "Bitrate mode", ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"]), card: "stream0" },
    { ...number("stream0.bitrate", "Bitrate", 1, 100_000_000), card: "stream0" },
    { ...number("stream0.gop", "GOP", 1, 65_535), card: "stream0", section: "GOP, profile and buffers" },
    { ...number("stream0.max_gop", "Maximum GOP", 1, 65_535), card: "stream0" },
    { ...select("stream0.profile", "Profile", [0, 1, 2], "number"), card: "stream0" },
    { ...number("stream0.buffers", "Buffers", -1, 65_535), description: "Use 1–65535 while enabled; -1 is reserved for a disabled stream.", card: "stream0" },
    { ...text("stream0.rtsp_endpoint", "RTSP path"), card: "stream0", section: "RTSP and audio" },
    { ...bool("stream0.audio_enabled", "Include audio"), card: "stream0" },
    { ...bool("stream1.enabled", "Enable stream", "An enabled stream requires 1–30 fps and at least one buffer."), card: "stream1", section: "Codec and resolution" },
    { ...number("stream1.width", "Width", 1, 16_384), card: "stream1" },
    { ...number("stream1.height", "Height", 1, 16_384), card: "stream1" },
    { ...select("stream1.format", "Codec", ["H264", "H265"]), card: "stream1" },
    { ...number("stream1.fps", "Frames per second", 0, 30), description: "Use 1–30 while enabled; 0 is reserved for a disabled stream.", card: "stream1", section: "Frame rate and rate control" },
    { ...select("stream1.mode", "Bitrate mode", ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"]), card: "stream1" },
    { ...number("stream1.bitrate", "Bitrate", 1, 100_000_000), card: "stream1" },
    { ...number("stream1.gop", "GOP", 1, 65_535), card: "stream1", section: "GOP, profile and buffers" },
    { ...number("stream1.max_gop", "Maximum GOP", 1, 65_535), card: "stream1" },
    { ...select("stream1.profile", "Profile", [0, 1, 2], "number"), card: "stream1" },
    { ...number("stream1.buffers", "Buffers", -1, 65_535), description: "Use 1–65535 while enabled; -1 is reserved for a disabled stream.", card: "stream1" },
    { ...text("stream1.rtsp_endpoint", "RTSP path"), card: "stream1", section: "RTSP and audio" },
    { ...bool("stream1.audio_enabled", "Include audio"), card: "stream1" },
  ],
  validate: (value) => {
    for (const name of ["stream0", "stream1"] as const) {
      const stream = value[name];
      if (typeof stream !== "object" || stream === null || Array.isArray(stream)) return `${name} configuration is incomplete.`;
      if (stream.buffers === 0) return `${name} buffers must be -1 or between 1 and 65535.`;
      if (stream.enabled === true && (typeof stream.fps !== "number" || stream.fps < 1)) return `${name} must use at least 1 fps when enabled.`;
      if (stream.enabled === true && stream.buffers === -1) return `${name} buffers cannot be disabled while the stream is enabled.`;
    }
    return undefined;
  },
};

const motionPrivacy: ConfigFormSpec = {
  eyebrow: "Streamer / detection",
  title: "Motion and privacy",
  description: "Detection and privacy state for the camera streams. Quick toggles also appear in Preview.",
  endpoint: routes.prudynt.domain("motion"),
  saveEndpoint: routes.prudynt.command,
  refreshStreamerPreview: true,
  load: (client) => prudyntLoad(client, [["motion", decodeMotion], ["privacy", decodePrivacy]]),
  fields: [
    ...section("Detection", [
      bool("motion.enabled", "Enable motion detection"), number("motion.sensitivity", "Motion sensitivity", 1, 8),
      select("motion.monitor_stream", "Monitor stream", [0, 1], "number"), bool("motion.playonspeaker", "Play event sound on speaker"),
      number("motion.frame_width", "Detection frame width", 0, 16_384), number("motion.frame_height", "Detection frame height", 0, 16_384),
      number("motion.ivs_polling_timeout", "Detector polling timeout (ms)", 100, 10_000), number("motion.motor_settle_ms", "Motor settle time (ms)", 0, 10_000),
    ]),
    ...section("Timing and capture", [
      number("motion.cooldown_time", "Cooldown time", 1, 60), number("motion.debounce_time", "Debounce time", 0),
      number("motion.init_time", "Initialization time", 0), number("motion.min_time", "Minimum event time", 0),
      number("motion.post_time", "Post-event time", 0), number("motion.skip_frame_count", "Frames skipped between checks", 0),
      number("motion.video_length", "Event video length", 0),
    ]),
    ...section("Region of interest", [
      number("motion.roi_0_x", "ROI left", 0, 16_384), number("motion.roi_0_y", "ROI top", 0, 16_384),
      number("motion.roi_1_x", "ROI right", 0, 16_384), number("motion.roi_1_y", "ROI bottom", 0, 16_384),
      number("motion.roi_count", "ROI cell count", 1, 52),
    ]),
    ...section("Event destinations", [
      bool("motion.send2email", "Route motion to email"), bool("motion.send2ftp", "Route motion to FTP"),
      bool("motion.send2gotify", "Route motion to Gotify"), bool("motion.send2mqtt", "Route motion to MQTT"),
      bool("motion.send2ntfy", "Route motion to ntfy"), bool("motion.send2storage", "Route motion to storage"),
      bool("motion.send2telegram", "Route motion to Telegram"), bool("motion.send2webhook", "Route motion to webhook"),
    ]),
    ...section("Privacy", [bool("privacy.enabled", "Mask every enabled camera stream")]),
  ],
  saveTransform: buildMotionPrivacyUpdate,
};

export function buildMotionPrivacyUpdate(value: JsonObject): JsonObject {
  const privacy = value.privacy;
  if (typeof privacy !== "object" || privacy === null || Array.isArray(privacy) || typeof privacy.enabled !== "boolean") {
    throw new TypeError("Privacy configuration is incomplete.");
  }
  return {
    ...value,
    privacy: { enabled: privacy.enabled, stream0_enabled: privacy.enabled, stream1_enabled: privacy.enabled },
  };
}

const homeAssistant: ConfigFormSpec = {
  eyebrow: "Services / Home Assistant",
  title: "Home Assistant",
  description: "MQTT discovery and the camera entities published to Home Assistant.",
  endpoint: routes.config.homeAssistant,
  decode: decodeHomeAssistant,
  groupSections: true,
  fields: [
    ...section("Service", [
      bool("enabled", "Enable native Home Assistant service", "Starts one bounded Control worker and one persistent MQTT connection."),
    ]),
    ...section("Broker", [
      text("mqtt.host", "MQTT host", true), number("mqtt.port", "MQTT port", 1, 65535),
    text("mqtt.username", "MQTT username"), secret("mqtt.password", "MQTT password", "Leave blank to keep the current secret."),
    text("mqtt.client_id_prefix", "MQTT client ID prefix"),
    bool("mqtt.use_ssl", "Use TLS for MQTT"), { ...bool("mqtt.tls_skip_verify", "Skip MQTT TLS certificate verification", "Available only for explicitly trusted private brokers."), enabledWhen: { path: "mqtt.use_ssl", equals: true } },
    ]),
    ...section("Identity and intervals", [
      text("device_name", "Device name"), text("device_model", "Device model"), text("discovery_prefix", "Discovery prefix"),
    number("state_interval", "State interval", 1, 604800), number("discovery_interval", "Discovery interval", 1, 604800), number("camera_interval", "Camera preview interval (seconds)", 5, 604800), number("ota_check_interval", "OTA check interval", 1, 604800),
    ]),
    ...section("D-Link A1 entities", [
    bool("enable_motion", "Publish motion"), bool("enable_live_view", "Publish camera preview", "Publishes a fresh stream 1 JPEG over MQTT; this is not an RTSP video stream."), bool("enable_daynight", "Publish day / night"),
    bool("enable_privacy", "Publish privacy"), bool("enable_snapshot", "Publish snapshot action"), bool("enable_ircut", "Publish IR filter"), bool("enable_ir850", "Publish 850 nm IR LED"),
    bool("enable_motion_guard", "Publish motion guard"), bool("enable_color", "Publish color mode"),
    bool("enable_gain", "Publish gain"), bool("enable_rssi", "Publish Wi-Fi signal"), bool("enable_reboot", "Publish reboot action"),
    bool("enable_firmware_version", "Publish firmware version"), bool("enable_firmware_timestamp", "Publish firmware timestamp"),
    ]),
    ...section("Unsupported on this build", [
      { ...bool("doorbell_supported", "Doorbell entity is unsupported", "No verified D-Link A1 doorbell input is published."), readOnly: true },
      { ...bool("ota_supported", "OTA entity is unsupported", "Native HTTPS release checks are not implemented; no curl fallback is used."), readOnly: true },
    ]),
  ],
  saveTransform: buildHomeAssistantUpdate,
};

export function buildHomeAssistantUpdate(value: JsonObject): JsonObject {
  const update = structuredClone(value);
  const mqtt = update.mqtt;
  if (typeof mqtt !== "object" || mqtt === null || Array.isArray(mqtt)) throw new TypeError("Home Assistant MQTT configuration is incomplete.");
  delete mqtt.password_set;
  delete update.doorbell_supported;
  delete update.ota_supported;
  update.enable_doorbell = false;
  update.enable_ota = false;
  delete update.enable_ir940;
  delete update.enable_white_light;
  return update;
}

function addHomeAssistantRuntime(client: ApiClient, rendered: { node: HTMLElement }): () => void {
  const api = new ControlApi(client);
  const card = element("section", { className: "card tools-panel", attrs: { "aria-live": "polite" } });
  const message = statusMessage();
  const summary = element("div", { className: "status-grid" });
  const actions = element("div", { className: "form-actions" });
  const reconnect = button("Reconnect", "button secondary");
  const discovery = button("Republish discovery", "button secondary");
  const state = button("Publish state", "button secondary");
  const actionButtons = [reconnect, discovery, state];
  actions.append(...actionButtons);
  card.append(
    element("h2", { text: "Native HA runtime" }),
    element("p", { text: "Connection state and bounded worker counters. Actions enqueue work; this browser request never opens an MQTT connection." }),
    message,
    summary,
    actions,
  );
  rendered.node.append(card);
  let cancelled = false;
  let timer: number | undefined;

  const cell = (label: string, value: string, detail: string): HTMLElement => {
    const node = element("div", { className: "status-cell" });
    node.append(element("span", { text: label }), element("strong", { text: value }), element("small", { text: detail }));
    return node;
  };
  const timestamp = (value: number | null): string => value === null ? "Never" : new Date(value * 1000).toLocaleString();

  async function load(): Promise<void> {
    try {
      const runtime = await api.homeAssistantRuntime();
      if (cancelled) return;
      summary.replaceChildren(
        cell("Connection", runtime.connected ? "Online" : runtime.state, runtime.last_error ?? "No reported error"),
        cell("Last connected", timestamp(runtime.last_connect_unix), runtime.reconnect_in_ms === null ? "No reconnect scheduled" : `Reconnect in ${runtime.reconnect_in_ms} ms`),
        cell("Queue", `${runtime.queue_depth} / 64`, `High-water ${runtime.queue_high_water_mark}`),
        cell("Messages", `${runtime.published_messages} published`, `${runtime.received_commands} commands · ${runtime.rejected_commands} rejected · ${runtime.dropped_messages} dropped`),
      );
      setMessage(message);
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to read Home Assistant runtime.", "error");
    } finally {
      if (!cancelled) timer = window.setTimeout(() => void load(), 5_000);
    }
  }

  const run = async (action: "reconnect" | "republish_discovery" | "publish_state", label: string): Promise<void> => {
    actionButtons.forEach((control) => { control.disabled = true; });
    setMessage(message, `${label} queued…`);
    try {
      await api.homeAssistantAction(action);
      if (!cancelled) {
        setMessage(message, `${label} accepted by the HA worker.`, "success");
        if (timer !== undefined) window.clearTimeout(timer);
        await load();
      }
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : `Unable to queue ${label.toLowerCase()}.`, "error");
    } finally {
      if (!cancelled) actionButtons.forEach((control) => { control.disabled = false; });
    }
  };
  reconnect.addEventListener("click", () => void run("reconnect", "Reconnect"));
  discovery.addEventListener("click", () => void run("republish_discovery", "Discovery publication"));
  state.addEventListener("click", () => void run("publish_state", "State publication"));
  void load();
  return () => {
    cancelled = true;
    if (timer !== undefined) window.clearTimeout(timer);
  };
}

const unwrapRecorder = (value: JsonObject): JsonObject =>
  typeof value.data === "object" && value.data !== null && !Array.isArray(value.data)
    ? value.data as JsonObject
    : value;

const recorder: ConfigFormSpec = {
  eyebrow: "Services / recorder",
  title: "Recorder",
  description: "Continuous clip storage and cleanup policy. Live start and stop controls are in Preview.",
  endpoint: routes.recorder,
  decode: decodeRecorder,
  fields: [
    bool("video.autostart", "Start recorder at boot"), { path: "video.mount", label: "Storage mount", type: "select", optionsFrom: "mounts" }, text("video.device_path", "Device path"), text("video.filename", "Filename template"),
    select("video.channel", "Stream", [0, 1], "number"), number("video.duration", "Clip duration in seconds", 1, 86400), bool("video.cleanup_enabled", "Clean up old clips"),
    number("video.limit", "Maximum clips", 1, 100000), number("video.min_free_mb", "Minimum free space in MiB", 1, 1000000), number("video.check_interval", "Cleanup check interval", 1, 86400),
  ],
  loadTransform: unwrapRecorder,
  saveTransform: (value) => ({ video: value.video ?? {} }),
};

const timelapse: ConfigFormSpec = {
  eyebrow: "Services / timelapse",
  title: "Timelapse",
  description: "Periodic snapshots stored with the camera recordings.",
  endpoint: routes.recorder,
  decode: decodeRecorder,
  fields: [
    bool("timelapse.enabled", "Enable timelapse"), { path: "timelapse.mount", label: "Storage mount", type: "select", optionsFrom: "mounts" }, text("timelapse.filepath", "Folder"), text("timelapse.filename", "Filename template"),
    number("timelapse.interval", "Interval in minutes", 1, 1440), number("timelapse.keep_days", "Keep days", 0, 365), bool("timelapse.preset_enabled", "Apply snapshot preset"),
    bool("timelapse.presets.ircut", "Preset IR filter"), bool("timelapse.presets.ir850", "Preset 850 nm IR LED"), bool("timelapse.presets.color", "Preset color mode"),
  ],
  loadTransform: unwrapRecorder,
  saveTransform: (value) => {
    const domain = structuredClone(value.timelapse ?? {}) as JsonObject;
    const presets = domain.presets as JsonObject;
    delete presets.ir940;
    delete presets.white;
    return { timelapse: domain };
  },
};

export function composeRgba(rgb: string, alpha: number): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(rgb) || !Number.isInteger(alpha) || alpha < 0 || alpha > 255) {
    throw new TypeError("OSD color must use #RRGGBB and an alpha value from 0 to 255.");
  }
  return `${rgb.toLowerCase()}${alpha.toString(16).padStart(2, "0")}`;
}

export function buildOsdUpdate(
  loaded: OsdConfig & JsonObject,
  burnin: OsdConfig["burnin"],
  seiEnabled: boolean,
  entries: Record<string, OsdEntry>,
): JsonObject {
  const next = structuredClone(loaded);
  const previousEntries = next.sei.entries;
  const mergedEntries: Record<string, OsdEntry> = {};
  for (const [name, entry] of Object.entries(entries)) {
    mergedEntries[name] = { ...(previousEntries[name] ?? {} as OsdEntry), ...entry };
  }
  next.burnin = { ...next.burnin, ...burnin };
  next.sei = { ...next.sei, enabled: seiEnabled, entries: mergedEntries };
  return next;
}

export function splitRgba(value: string): { rgb: string; alpha: number } {
  const match = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})$/.exec(value);
  if (!match) throw new TypeError("OSD color must use #RRGGBBAA.");
  return { rgb: `#${match[1]!.toLowerCase()}`, alpha: Number.parseInt(match[2]!, 16) };
}

export function withOsdTimezoneToken(format: string, enabled: boolean): string {
  const withoutToken = format.replace(/\s*%Z\b/g, "").replace(/\s{2,}/g, " ").trim();
  if (!enabled) return withoutToken;
  return withoutToken ? `${withoutToken} %Z` : "%Z";
}

interface OsdColorEditor {
  node: HTMLElement;
  input: HTMLInputElement;
  set(value: string): void;
  setDisabled(disabled: boolean): void;
  value(): string;
}

function osdColorEditor(label: string, id: string): OsdColorEditor {
  const row = element("div", { className: "field osd-color-field" });
  const color = element("input", { className: "osd-color-picker", attrs: { type: "color", id: `${id}-picker`, "aria-label": `${label} RGB color` } });
  const alpha = element("input", { className: "osd-alpha", attrs: { type: "range", min: "0", max: "255", step: "1", id: `${id}-alpha`, "aria-label": `${label} alpha` } });
  const input = element("input", { className: "input osd-rgba", attrs: { pattern: "#[0-9A-Fa-f]{8}", id, required: "", "aria-label": `${label} RGBA value` } });
  const alphaValue = element("output", { className: "osd-alpha-value", attrs: { for: alpha.id } });
  const syncFromParts = (): void => {
    input.value = composeRgba(color.value, Number(alpha.value));
    alphaValue.value = alpha.value;
  };
  color.addEventListener("input", syncFromParts);
  alpha.addEventListener("input", syncFromParts);
  input.addEventListener("input", () => {
    if (!input.checkValidity()) return;
    const next = splitRgba(input.value);
    color.value = next.rgb;
    alpha.value = String(next.alpha);
    alphaValue.value = alpha.value;
  });
  const controls = element("div", { className: "osd-color-controls" });
  controls.append(color, alpha, alphaValue, input);
  row.append(element("label", { text: label, attrs: { for: color.id } }), controls);
  return {
    node: row,
    input,
    set(value) {
      const next = splitRgba(value);
      color.value = next.rgb;
      alpha.value = String(next.alpha);
      alphaValue.value = alpha.value;
      input.value = composeRgba(next.rgb, next.alpha);
    },
    setDisabled(disabled) {
      color.disabled = disabled;
      alpha.disabled = disabled;
      input.disabled = disabled;
      row.classList.toggle("is-disabled", disabled);
    },
    value() {
      if (!input.checkValidity()) throw new TypeError(`${label} must use #RRGGBBAA.`);
      return composeRgba(color.value, Number(alpha.value));
    },
  };
}

const DEFAULT_OSD_FORMAT = "%F %T %Z";

function renderOsdPage(client: ApiClient): { node: HTMLElement; cleanup: () => void } {
  const page = element("section", { className: "page" });
  const header = element("header", { className: "page-heading" });
  header.append(element("span", { className: "eyebrow", text: "Streamer / OSD" }), element("h1", { text: "On-screen display" }), element("p", { text: "Burned-in overlay styling and named structured metadata entries." }));
  const message = statusMessage();
  const form = element("form", { className: "card form-card" });
  const burnEnabled = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-burn-enabled" } });
  const burnFormat = element("input", { className: "input", attrs: { id: "osd-burn-format" } });
  const burnScale = element("select", { className: "input", attrs: { id: "osd-burn-scale" } });
  for (let scale = 0; scale <= 10; scale += 1) burnScale.append(element("option", { text: String(scale), attrs: { value: String(scale) } }));
  const burnTimezone = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-burn-timezone" } });
  const seiEnabled = element("input", { className: "switch-input", attrs: { type: "checkbox", id: "osd-sei-enabled" } });
  const entries = element("div", { className: "osd-entry-list" });
  const addEntry = button("Add entry", "button secondary button-compact");
  const reload = button("Reload", "button secondary");
  const save = element("button", { className: "button primary", text: "Save settings", attrs: { type: "submit" } });
  save.disabled = true;
  const field = (label: string, control: HTMLElement): HTMLElement => {
    const row = element("div", { className: "field" });
    row.append(element("label", { text: label, attrs: { for: control.id } }), control);
    return row;
  };
  const actions = element("div", { className: "form-actions" });
  actions.append(reload, save);
  let fill!: OsdColorEditor;
  let outline!: OsdColorEditor;
  let background!: OsdColorEditor;
  fill = osdColorEditor("Fill color", "osd-fill");
  outline = osdColorEditor("Outline color", "osd-outline");
  background = osdColorEditor("Background color", "osd-background");
  const burnSettings = element("div", { className: "osd-burn-settings" });
  burnSettings.append(
    field("Text format", burnFormat),
    switchField("Include short timezone (%Z)", burnTimezone, "Append the camera's abbreviated timezone to the burn-in text."),
    field("Text scale", burnScale),
    fill.node, outline.node, background.node,
  );
  burnFormat.addEventListener("input", () => {
    burnTimezone.checked = burnFormat.value.includes("%Z");
  });
  burnTimezone.addEventListener("change", () => {
    burnFormat.value = withOsdTimezoneToken(burnFormat.value, burnTimezone.checked);
  });
  const burnToggle = switchField("Burn-in overlay", burnEnabled, "Render OSD text directly into the video.");
  burnToggle.classList.add("osd-burn-toggle");
  const seiToggle = switchField("Publish structured metadata", seiEnabled, "Expose named metadata entries through the video stream.");
  const seiHeading = element("div", { className: "form-section-heading" });
  seiHeading.append(element("h2", { className: "form-section-title", text: "Structured SEI entries" }), addEntry);
  form.append(
    burnToggle,
    burnSettings,
    seiHeading, seiToggle, entries,
    actions,
  );
  page.append(header, message, form);
  let loaded: ReturnType<typeof decodeOsd> | null = null;
  let cancelled = false;
  let entrySerial = 0;

  const addEntryRow = (name = "", type = "text", format = "", position = "10,10"): void => {
    const row = element("fieldset", { className: "osd-entry card" });
    const serial = ++entrySerial;
    const nameInput = element("input", { className: "input osd-entry-name", attrs: { id: `osd-entry-${serial}-name`, value: name, required: "" } });
    const typeInput = element("select", { className: "input osd-entry-type", attrs: { id: `osd-entry-${serial}-type` } });
    for (const option of ["text", "gain", "hostname", "ipaddress", "timestamp", "uptime"]) typeInput.append(element("option", { text: option, attrs: { value: option } }));
    typeInput.value = type;
    const formatInput = element("input", { className: "input osd-entry-format", attrs: { id: `osd-entry-${serial}-format`, value: format } });
    const positionInput = element("input", { className: "input osd-entry-position", attrs: { id: `osd-entry-${serial}-position`, value: position, required: "" } });
    const remove = button("Remove entry", "button danger");
    remove.addEventListener("click", () => row.remove());
    const entryField = (label: string, control: HTMLElement): HTMLElement => {
      const field = element("div", { className: "field" });
      field.append(element("label", { text: label, attrs: { for: control.id } }), control);
      return field;
    };
    row.append(
      element("legend", { text: "Structured entry" }),
      entryField("Name", nameInput),
      entryField("Type", typeInput),
      entryField("Format", formatInput),
      entryField("Position", positionInput),
      remove,
    );
    entries.append(row);
  };

  const setBurnEnabled = (): void => {
    const disabled = !burnEnabled.checked;
    burnSettings.classList.toggle("is-disabled", disabled);
    burnFormat.disabled = disabled;
    burnTimezone.disabled = disabled;
    burnScale.disabled = disabled;
    fill.setDisabled(disabled);
    outline.setDisabled(disabled);
    background.setDisabled(disabled);
  };
  burnEnabled.addEventListener("change", setBurnEnabled);
  setBurnEnabled();

  async function load(): Promise<boolean> {
    save.disabled = true;
    setMessage(message, "Loading OSD settings…");
    try {
      loaded = decodeOsd(await client.json<unknown>(routes.prudynt.domain("osd")));
      if (cancelled) return false;
      burnEnabled.checked = loaded.burnin.enabled;
      burnFormat.value = loaded.burnin.format ?? DEFAULT_OSD_FORMAT;
      burnTimezone.checked = burnFormat.value.includes("%Z");
      burnScale.value = String(loaded.burnin.scale);
      fill.set(loaded.burnin.fill_color);
      outline.set(loaded.burnin.outline_color);
      background.set(loaded.burnin.background_color);
      seiEnabled.checked = loaded.sei.enabled;
      entries.replaceChildren();
      for (const [name, entry] of Object.entries(loaded.sei.entries)) addEntryRow(name, entry.type, entry.format, entry.position);
      setBurnEnabled();
      save.disabled = false;
      setMessage(message);
      return true;
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load OSD settings.", "error");
      return false;
    }
  }
  addEntry.addEventListener("click", () => addEntryRow());
  reload.addEventListener("click", () => void load());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!loaded || !form.checkValidity()) return form.reportValidity();
    const nextEntries: Record<string, OsdEntry> = {};
    const names = new Set<string>();
    for (const row of entries.querySelectorAll<HTMLElement>(".osd-entry")) {
      const name = row.querySelector<HTMLInputElement>(".osd-entry-name")!.value.trim();
      if (!name || names.has(name)) {
        setMessage(message, "Each OSD entry needs a unique non-empty name.", "error");
        return;
      }
      names.add(name);
      nextEntries[name] = {
        type: row.querySelector<HTMLSelectElement>(".osd-entry-type")!.value as OsdEntry["type"],
        format: row.querySelector<HTMLInputElement>(".osd-entry-format")!.value,
        position: row.querySelector<HTMLInputElement>(".osd-entry-position")!.value,
      };
    }
    const next = buildOsdUpdate(
      loaded,
      { enabled: burnEnabled.checked, format: burnFormat.value, scale: Number(burnScale.value), fill_color: fill.value(), outline_color: outline.value(), background_color: background.value() },
      seiEnabled.checked,
      nextEntries,
    );
    save.disabled = true;
    setMessage(message, "Saving OSD settings…");
    try {
      await client.postJson<unknown>(routes.prudynt.command, { osd: next });
      if (await load() && !cancelled) {
        window.dispatchEvent(new CustomEvent("thingino:config-saved", { detail: { endpoint: routes.prudynt.command, refreshStreamerPreview: true } }));
        setMessage(message, "OSD settings saved.", "success");
      }
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to save OSD settings.", "error");
    } finally {
      save.disabled = loaded === null;
    }
  });
  void load();
  return { node: page, cleanup: () => { cancelled = true; } };
}

const specs: Partial<Record<PageId, ConfigFormSpec>> = {
  network, time, audio, access, webui, admin, logging, daynight,
  imaging, streams, "motion-privacy": motionPrivacy,
  "home-assistant": homeAssistant, recorder, timelapse,
};

function addTimeSync(client: ApiClient, rendered: { node: HTMLElement }): void {
  const sync = button("Sync time now", "button secondary");
  rendered.node.querySelector(".form-actions")?.prepend(sync);
  const message = rendered.node.querySelector<HTMLElement>(".message")!;
  sync.addEventListener("click", async () => {
    sync.disabled = true;
    setMessage(message, "Synchronizing time…");
    try {
      await client.empty(routes.actions.syncTime, { method: "POST" });
      setMessage(message, "Time synchronized.", "success");
    } catch (error) {
      setMessage(message, error instanceof Error ? error.message : "Time synchronization failed.", "error");
    } finally {
      sync.disabled = false;
    }
  });
}

function addWifiScan(client: ApiClient, rendered: { node: HTMLElement }): void {
  const scan = button("Scan Wi-Fi networks", "button secondary");
  const results = element("div", { className: "wifi-results", attrs: { "aria-live": "polite" } });
  results.hidden = true;
  const toolbar = element("div", { className: "section-toolbar" });
  toolbar.append(scan);
  const wifiSection = rendered.node.querySelector(".form-section-card");
  const firstField = wifiSection?.querySelector(".field") ?? null;
  if (wifiSection) {
    wifiSection.insertBefore(toolbar, firstField);
    wifiSection.append(results);
  } else {
    rendered.node.querySelector(".form-actions")?.prepend(scan);
    rendered.node.querySelector(".form-card")?.append(results);
  }
  const message = rendered.node.querySelector<HTMLElement>(".message")!;
  scan.addEventListener("click", async () => {
    scan.disabled = true;
    setMessage(message, "Scanning for Wi-Fi networks…");
    try {
      const response = decodeWifiScan(await client.json<unknown>(routes.network.wifiScan));
      results.replaceChildren();
      results.hidden = false;
      if (response.networks.length === 0) {
        results.append(element("p", { text: "No Wi-Fi networks were found. Move the camera closer to the access point and scan again." }));
        setMessage(message, "Scan completed; no networks were found.");
        return;
      }
      const networks = [...response.networks].sort((left, right) => (right.signal ?? -999) - (left.signal ?? -999));
      const selector = element("select", { className: "input", attrs: { "aria-label": "Available Wi-Fi networks" } });
      const currentSsid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.ssid"]')?.value ?? "";
      const currentBssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.bssid"]')?.value.toLowerCase() ?? "";
      let currentIndex = 0;
      networks.forEach((network, index) => {
        const current = (network.bssid?.toLowerCase() === currentBssid && currentBssid !== "") || (network.ssid === currentSsid && currentBssid === "");
        if (current) currentIndex = index;
        const details = [network.security, network.signal === undefined ? undefined : `${network.signal} dBm`].filter(Boolean).join(" · ");
        selector.append(element("option", { text: `${network.ssid || "Hidden network"}${details ? ` — ${details}` : ""}${current ? " — current" : ""}`, attrs: { value: String(index) } }));
      });
      selector.value = String(currentIndex);
      const useSelected = button("Use selected network", "button primary");
      useSelected.addEventListener("click", () => {
        const network = networks[Number(selector.value)];
        if (!network) return;
        const ssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.ssid"]');
        const bssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.bssid"]');
        if (ssid) { ssid.value = network.ssid; ssid.dispatchEvent(new Event("input", { bubbles: true })); }
        if (bssid) { bssid.value = network.bssid ?? ""; bssid.dispatchEvent(new Event("input", { bubbles: true })); }
        setMessage(message, `${network.ssid || "Hidden network"} selected. Enter its password if needed, then save settings.`, "success");
      });
      const count = networks.length;
      results.append(
        element("strong", { text: `${count} Wi-Fi network${count === 1 ? "" : "s"} found` }),
        element("p", { text: "Choose a network from the list. Nothing changes on the camera until you select it and save the form." }),
        selector,
        useSelected,
      );
      setMessage(message, `Scan completed; ${count} network${count === 1 ? "" : "s"} found.`);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Wi-Fi scan failed.";
      results.hidden = false;
      results.replaceChildren(element("p", { className: "tools-warning", text }));
      setMessage(message, text, "error");
    } finally {
      scan.disabled = false;
    }
  });
}

function addWebuiSecurity(client: ApiClient, rendered: { node: HTMLElement }): void {
  const themeSelect = rendered.node.querySelector<HTMLSelectElement>('[name="theme"]');
  themeSelect?.addEventListener("change", () => {
    if (themeSelect.value === "auto" || themeSelect.value === "light" || themeSelect.value === "dark") {
      try { localStorage.setItem("dcs6100-theme", themeSelect.value); } catch (_) { /* storage can be unavailable */ }
      applyThemePreference(themeSelect.value as ThemePreference);
    }
  });
  const security = element("section", { className: "settings-extras" });
  const passwordCard = element("form", { className: "card compact-form" });
  const passwordMessage = statusMessage();
  const password = element("input", { className: "input", attrs: { type: "password", autocomplete: "new-password", required: "", minlength: "10", maxlength: "128", "aria-label": "New management password" } });
  const confirm = element("input", { className: "input", attrs: { type: "password", autocomplete: "new-password", required: "", minlength: "10", maxlength: "128", "aria-label": "Confirm management password" } });
  const change = element("button", { className: "button primary", text: "Change password", attrs: { type: "submit" } });
  passwordCard.append(element("h2", { text: "Management password" }), element("p", { text: "Changes the shared WebUI, RTSP and ONVIF password together. SSH remains key-only. You will sign in again after a successful change." }), password, confirm, passwordMessage, change);
  passwordCard.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!password.value || password.value !== confirm.value) {
      setMessage(passwordMessage, "Enter the same non-empty password twice.", "error");
      return;
    }
    change.disabled = true;
    try {
      await client.postJson<JsonObject>(routes.auth.password, { password: password.value });
      password.value = "";
      confirm.value = "";
      setMessage(passwordMessage, "Shared WebUI, RTSP and ONVIF password changed.", "success");
      window.location.assign("/login.html");
    } catch (error) {
      setMessage(passwordMessage, error instanceof Error ? error.message : "Password change failed.", "error");
    } finally {
      change.disabled = false;
    }
  });

  const keyCard = element("section", { className: "card compact-form" });
  const keyStatus = element("p", { text: "Checking API key…" });
  const keyValue = element("code", { className: "key-value", text: "" });
  const generate = button("Generate API key", "button secondary");
  const copy = button("Copy API key", "button secondary");
  copy.disabled = true;
  const remove = button("Delete API key", "button danger");
  const actions = element("div", { className: "form-actions" });
  actions.append(generate, copy, remove);
  keyCard.append(element("h2", { text: "Service API key" }), element("p", { text: "For explicit non-browser clients. This WebUI always uses its HttpOnly session cookie." }), keyStatus, keyValue, actions);
  async function refreshKey(): Promise<void> {
    try {
      const value = await client.json<{ exists: boolean }>(routes.webuiApiKey);
      keyStatus.textContent = value.exists ? "An API key exists." : "No API key exists.";
      keyValue.textContent = "Hidden after generation";
      remove.disabled = !value.exists;
      copy.disabled = true;
    } catch (error) {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to read API-key status.";
    }
  }
  generate.addEventListener("click", async () => {
    const value = await client.json<{ api_key: string }>(routes.webuiApiKey, { method: "POST" }).catch((error: unknown) => {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to generate API key.";
      return null;
    });
    if (value) { keyStatus.textContent = "New API key generated. Store it in the intended client."; keyValue.textContent = value.api_key; copy.disabled = false; remove.disabled = false; }
  });
  copy.addEventListener("click", async () => {
    if (!keyValue.textContent) return;
    try {
      await navigator.clipboard.writeText(keyValue.textContent);
      keyStatus.textContent = "API key copied to the clipboard.";
    } catch (_) {
      keyStatus.textContent = "Clipboard access was unavailable. Select and copy the key manually.";
    }
  });
  remove.addEventListener("click", async () => {
    if (!window.confirm("Delete the current service API key? Existing non-browser clients will stop authenticating.")) return;
    try {
      await client.empty(routes.webuiApiKey, { method: "DELETE" });
      keyStatus.textContent = "API key deleted.";
      keyValue.textContent = "";
      copy.disabled = true;
      remove.disabled = true;
    } catch (error) {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to delete API key.";
    }
  });
  security.append(passwordCard, keyCard);
  rendered.node.append(security);
  void refreshKey();
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

export function renderConfigPage(client: ApiClient, id: PageId): { node: HTMLElement; cleanup: () => void } | null {
  if (id === "osd") return renderOsdPage(client);
  if (id === "gpio") {
    const hardware = element("section", { className: "card tools-panel", attrs: { "aria-live": "polite" } });
    hardware.append(element("p", { text: "Loading verified Hardware I/O map…" }));
    const rendered = renderConfigForm(client, gpioSpec((value) => renderGpioHardwareIo(hardware, value)));
    rendered.node.append(hardware);
    return rendered;
  }
  const spec = specs[id];
  if (!spec) return null;
  const rendered = renderConfigForm(client, spec);
  if (id === "network") addWifiScan(client, rendered);
  if (id === "time") addTimeSync(client, rendered);
  if (id === "webui") addWebuiSecurity(client, rendered);
  if (id === "home-assistant") {
    const cleanupRuntime = addHomeAssistantRuntime(client, rendered);
    return { node: rendered.node, cleanup: () => { cleanupRuntime(); rendered.cleanup(); } };
  }
  return rendered;
}
