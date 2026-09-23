import type {
  AccessConfig, AdminConfig, ApiErrorEnvelope, AudioConfig, RaptorAudioConfig, DayNightConfig, GpioConfig, HealthResponse, HomeAssistantConfig, HomeAssistantRuntime,
  ImagingConfig, ImagingRuntimeField, ImagingRuntimeState, ImagingWhiteBalanceRuntime, JsonObject, MotionConfig, NetworkConfig, OsdConfig, PrivacyConfig,
  CrontabConfig, DayNightSensors, DiagnosticBundleResponse, DiagnosticInfoResponse, FileListResponse, MutationSuccess,
  FileRemoveResponse, FileTextResponse, FileTextWriteResponse, OverlayStatus, RecorderResponse, RaptorTimelapsePolicy, RaptorTimelapseResponse,
  MotionRuntime, RaptorMotionRuntime, NetworkInterfaceConfig, NetworkProbeMetadata, NetworkProbeResponse, RemoteLoggingConfig, RuntimeHeartbeat, RuntimeSystem, SdFormatAccepted, SdStatus, SensorIdentity, SessionState,
  RuntimeMediaResponse, RuntimeStream, StreamConfig, TimeConfig, WebuiConfig, WifiScanResponse,
} from "./contracts";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asJsonObject(value: unknown, label = "response"): JsonObject {
  if (!isRecord(value)) throw new TypeError(`${label} must be a JSON object`);
  return value as JsonObject;
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw new TypeError(`${label} must be an object`);
  return value;
}

function requiredString(value: Record<string, unknown>, field: string, label: string): void {
  if (typeof value[field] !== "string") throw new TypeError(`${label}.${field} must be a string`);
}

function requiredBoolean(value: Record<string, unknown>, field: string, label: string): void {
  if (typeof value[field] !== "boolean") throw new TypeError(`${label}.${field} must be a boolean`);
}

function requiredNumber(value: Record<string, unknown>, field: string, label: string): void {
  if (typeof value[field] !== "number" || !Number.isFinite(value[field])) throw new TypeError(`${label}.${field} must be a finite number`);
}

function numberRange(value: Record<string, unknown>, field: string, label: string, min: number, max: number): void {
  requiredNumber(value, field, label);
  const number = value[field] as number;
  if (number < min || number > max) throw new TypeError(`${label}.${field} must be between ${min} and ${max}`);
}

function typed<T>(value: Record<string, unknown>): T & JsonObject {
  return value as T & JsonObject;
}

export function decodeNetwork(value: unknown): NetworkConfig & JsonObject {
  const root = object(value, "network");
  requiredString(root, "hostname", "network");
  const dns = object(root.dns, "network.dns");
  requiredString(dns, "primary", "network.dns");
  requiredString(dns, "secondary", "network.dns");
  const wifi = object(root.wifi, "network.wifi");
  requiredString(wifi, "ssid", "network.wifi");
  requiredString(wifi, "bssid", "network.wifi");
  if (wifi.password !== null) requiredString(wifi, "password", "network.wifi");
  requiredBoolean(wifi, "password_set", "network.wifi");
  const wifiAp = object(root.wifi_ap, "network.wifi_ap");
  if (wifiAp.enabled !== false) throw new TypeError("network.wifi_ap is fixed false for the D-Link profile");
  const interfaces = object(root.interfaces, "network.interfaces");
  for (const name of ["eth0", "wlan0", "usb0"] as const) {
    const iface = object(interfaces[name], `network.interfaces.${name}`);
    requiredBoolean(iface, "enabled", `network.interfaces.${name}`);
    requiredBoolean(iface, "dhcp", `network.interfaces.${name}`);
    for (const field of ["address", "netmask", "gateway", "broadcast"] as const) requiredString(iface, field, `network.interfaces.${name}`);
    requiredBoolean(iface, "ipv6", `network.interfaces.${name}`);
    requiredString(iface, "mac", `network.interfaces.${name}`);
    requiredBoolean(iface, "link_up", `network.interfaces.${name}`);
    if (iface.ipv6 !== false) throw new TypeError(`network.interfaces.${name}.ipv6 is fixed false for the D-Link profile`);
  }
  return typed<NetworkConfig>(root);
}

export function decodeAccess(value: unknown): AccessConfig & JsonObject {
  const root = object(value, "access");
  if (root.username !== null) requiredString(root, "username", "access");
  if (root.password !== null) throw new TypeError("access.password must be null");
  requiredBoolean(root, "password_set", "access");
  if (root.rtsp_port !== null) numberRange(root, "rtsp_port", "access", 1, 65_535);
  for (const field of ["rtsp_ch0", "rtsp_ch1", "rtsp_mic"] as const) if (root[field] !== null) requiredString(root, field, "access");
  if (root.source === "raptor") {
    requiredBoolean(root, "auth_enabled", "access");
    requiredString(root, "username", "access");
    numberRange(root, "rtsp_port", "access", 1, 65_535);
    requiredString(root, "rtsp_ch0", "access");
    requiredString(root, "rtsp_ch1", "access");
    if (root.password_set !== root.auth_enabled) throw new TypeError("Raptor access authentication state is inconsistent");
    if (root.onvif_port !== null || root.onvif_enabled !== null || root.onvif_ingress !== null || root.rtsp_mic !== "audio") throw new TypeError("Raptor access capabilities are invalid");
    if (["saved_rtsp_ch0", "saved_rtsp_ch1", "rtsp_paths_match_saved"].some(key => key in root)) {
      for (const key of ["saved_rtsp_ch0", "saved_rtsp_ch1"]) if (root[key] !== null) requiredString(root, key, "access");
      requiredBoolean(root, "rtsp_paths_match_saved", "access");
      const matches = root.saved_rtsp_ch0 !== null && root.saved_rtsp_ch1 !== null &&
        root.saved_rtsp_ch0 === root.rtsp_ch0 && root.saved_rtsp_ch1 === root.rtsp_ch1;
      if (root.rtsp_paths_match_saved !== matches) throw new TypeError("Raptor RTSP saved-path observation is inconsistent");
    }
  } else if (root.onvif_port !== 80 || root.onvif_enabled !== true || root.onvif_ingress !== "same-origin") throw new TypeError("access ONVIF ingress is invalid");
  return typed<AccessConfig>(root);
}

export function decodeTime(value: unknown): TimeConfig & JsonObject {
  const root = object(value, "time");
  requiredString(root, "timezone", "time");
  if (!Array.isArray(root.timezone_options) || root.timezone_options.length === 0) throw new TypeError("time.timezone_options must be a non-empty array");
  for (const [index, optionValue] of root.timezone_options.entries()) {
    if (typeof optionValue === "string") continue;
    const option = object(optionValue, `time.timezone_options[${index}]`);
    requiredString(option, "name", `time.timezone_options[${index}]`);
    if (option.label !== undefined) requiredString(option, "label", `time.timezone_options[${index}]`);
  }
  if (root.current_unix_time === undefined && root.current_unix_ms === undefined) throw new TypeError("time.current_unix_time or current_unix_ms is required");
  if (root.current_unix_time !== undefined) numberRange(root, "current_unix_time", "time", 0, Number.MAX_SAFE_INTEGER);
  if (root.current_unix_ms !== undefined) numberRange(root, "current_unix_ms", "time", 0, Number.MAX_SAFE_INTEGER);
  requiredBoolean(root, "dhcp_ignore_timezone", "time");
  for (const field of ["ntp_server_0", "ntp_server_1", "ntp_server_2", "ntp_server_3"] as const) requiredString(root, field, "time");
  return typed<TimeConfig>(root);
}

export function decodeAdmin(value: unknown): AdminConfig & JsonObject {
  const root = object(value, "admin");
  for (const field of ["name", "email", "telegram", "discord"] as const) {
    requiredString(root, field, "admin");
  }
  return typed<AdminConfig>(root);
}

export function decodeWifiScan(value: unknown): WifiScanResponse & JsonObject {
  const root = object(value, "Wi-Fi scan");
  if (!Array.isArray(root.networks)) throw new TypeError("Wi-Fi scan.networks must be an array");
  for (const [index, entryValue] of root.networks.entries()) {
    const entry = object(entryValue, `Wi-Fi scan.networks[${index}]`);
    requiredString(entry, "ssid", `Wi-Fi scan.networks[${index}]`);
    if (entry.bssid !== undefined) requiredString(entry, "bssid", `Wi-Fi scan.networks[${index}]`);
    if (entry.signal !== undefined) requiredNumber(entry, "signal", `Wi-Fi scan.networks[${index}]`);
    if (entry.security !== undefined) requiredString(entry, "security", `Wi-Fi scan.networks[${index}]`);
  }
  return typed<WifiScanResponse>(root);
}

export function decodeNetworkProbeMetadata(value: unknown): NetworkProbeMetadata {
  const root = object(value, "network probe metadata");
  if (!Array.isArray(root.actions) || !Array.isArray(root.interfaces)) throw new TypeError("network probe metadata arrays are invalid");
  const actions = root.actions.map((entryValue, index) => {
    const entry = object(entryValue, `network probe action ${index}`);
    requiredString(entry, "id", `network probe action ${index}`);
    requiredString(entry, "label", `network probe action ${index}`);
    requiredString(entry, "description", `network probe action ${index}`);
    if (entry.id !== "resolve" && entry.id !== "connect") throw new TypeError(`network probe action ${index} is unsupported`);
    const id: "resolve" | "connect" = entry.id;
    return { id, label: entry.label as string, description: entry.description as string };
  });
  const interfaces = root.interfaces.map((entry, index) => {
    if (typeof entry !== "string") throw new TypeError(`network probe interface ${index} must be a string`);
    return entry;
  });
  const defaults = object(root.defaults, "network probe defaults");
  const limits = object(root.limits, "network probe limits");
  const packetSize = object(limits.packet_size, "network probe packet-size limits");
  const count = object(limits.count, "network probe count limits");
  if (defaults.action !== "resolve" && defaults.action !== "connect") throw new TypeError("network probe default action is invalid");
  requiredString(defaults, "interface", "network probe defaults");
  requiredNumber(defaults, "packet_size", "network probe defaults");
  requiredNumber(defaults, "count", "network probe defaults");
  for (const range of [packetSize, count]) { requiredNumber(range, "min", "network probe limits"); requiredNumber(range, "max", "network probe limits"); }
  if (defaults.count !== 1 || count.min !== 1 || count.max !== 1) throw new TypeError("network probe count contract is invalid");
  return {
    actions,
    interfaces,
    defaults: { action: defaults.action, interface: defaults.interface as string, packet_size: defaults.packet_size as number, count: 1 },
    limits: { packet_size: { min: packetSize.min as number, max: packetSize.max as number }, count: { min: 1, max: 1 } },
  };
}

export function decodeNetworkProbeResponse(value: unknown): NetworkProbeResponse {
  const root = object(value, "network probe response");
  requiredString(root, "command", "network probe response");
  requiredBoolean(root, "success", "network probe response");
  requiredString(root, "output_b64", "network probe response");
  return { command: root.command as string, success: root.success as boolean, output_b64: root.output_b64 as string };
}

export function decodeAudio(value: unknown): (AudioConfig | RaptorAudioConfig) & JsonObject {
  const root = object(value, "audio");
  if (root.source === "raptor") {
    for (const name of ["mic_enabled", "spk_enabled", "mic_muted"]) {
      if (root[name] !== null && typeof root[name] !== "boolean") throw new TypeError(`audio.${name} must be boolean or null`);
    }
    if (root.input_readback !== "owner" || root.processing_readback !== "owner") throw new TypeError("audio owner readback is missing");
    const codecs = object(root.codecs_built, "audio codecs");
    for (const name of ["PCM", "G711A", "G711U", "AAC", "OPUS"]) requiredBoolean(codecs, name, "audio codecs");
    if (root.mic_enabled === true) {
      if (typeof root.mic_format !== "string" || codecs[root.mic_format] !== true) throw new TypeError("audio codec is not built");
      if (![8000, 16000, 24000, 32000, 44100, 48000, 96000].includes(Number(root.mic_sample_rate))) throw new TypeError("audio sample rate is invalid");
    } else if (root.mic_format !== null || root.mic_sample_rate !== null) throw new TypeError("inactive audio input must not claim a codec");
    requiredBoolean(root, "effects_built", "audio");
    requiredBoolean(root, "processing_available", "audio");
    if (root.processing_available && (!root.effects_built || root.mic_enabled !== true)) throw new TypeError("audio processing is unavailable");
    for (const name of ["mic_agc_enabled", "mic_high_pass_filter"]) {
      if (root.processing_available ? typeof root[name] !== "boolean" : root[name] !== null) throw new TypeError(`audio.${name} has invalid availability`);
    }
    for (const [name, max] of [["mic_noise_suppression", 4], ["mic_agc_target_level_dbfs", 31], ["mic_agc_compression_gain_db", 90]] as const) {
      if (root.processing_available ? !Number.isInteger(root[name]) || Number(root[name]) < 0 || Number(root[name]) > max : root[name] !== null) throw new TypeError(`audio.${name} has invalid availability`);
    }
    if (root.mic_is_digital !== false || root.mic_input_basis !== "dlink-a1-profile-amic") throw new TypeError("audio microphone topology is not the D-Link A1 profile");
    if (root.force_stereo !== false || root.channel_basis !== "rad-fixed-mono") throw new TypeError("audio channel topology is not fixed mono");
    if (root.buffer_warn_frames !== null || root.buffer_cap_frames !== null || root.buffer_control !== "unsupported-prudynt-queue-policy") throw new TypeError("audio Prudynt queue controls must be unavailable");
    if (root.tap_enabled !== null || root.tap_path !== null || root.tap_control !== "unsupported") throw new TypeError("audio tap controls must be unavailable");
    const fields = object(root.levels, "audio levels");
    for (const name of ["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain"]) {
      const field = object(fields[name], `audio levels.${name}`);
      requiredBoolean(field, "supported", name);
      requiredBoolean(field, "available", name);
      const gain = name.endsWith("gain");
      if (field.min !== (gain ? 0 : -30) || field.max !== (name === "mic_alc_gain" ? 7 : gain ? 31 : 120)) throw new TypeError(`audio.${name} has invalid bounds`);
      if (field.available) {
        if (!field.supported || !Number.isInteger(field.value) || Number(field.value) < Number(field.min) || Number(field.value) > Number(field.max)) throw new TypeError(`audio.${name} has invalid readback`);
      } else if (field.value !== null) throw new TypeError(`audio.${name} unavailable value must be null`);
      if (root[name] !== field.value) throw new TypeError(`audio.${name} disagrees with readback`);
    }
    return typed<RaptorAudioConfig>(root);
  }
  for (const field of ["mic_enabled", "mic_agc_enabled", "mic_high_pass_filter", "mic_is_digital", "force_stereo", "spk_enabled", "tap_enabled"] as const) requiredBoolean(root, field, "audio");
  for (const field of ["mic_format", "tap_path"] as const) requiredString(root, field, "audio");
  for (const field of ["buffer_warn_frames", "buffer_cap_frames", "mic_vol", "mic_gain", "mic_alc_gain", "mic_noise_suppression", "mic_agc_compression_gain_db", "mic_agc_target_level_dbfs", "spk_vol", "spk_gain"] as const) requiredNumber(root, field, "audio");
  for (const field of ["buffer_warn_frames", "buffer_cap_frames"] as const) numberRange(root, field, "audio", 10, 1_000);
  for (const field of ["mic_vol", "spk_vol"] as const) numberRange(root, field, "audio", -30, 120);
  for (const field of ["mic_gain", "spk_gain"] as const) numberRange(root, field, "audio", 0, 31);
  numberRange(root, "mic_alc_gain", "audio", 0, 7);
  numberRange(root, "mic_noise_suppression", "audio", 0, 3);
  numberRange(root, "mic_agc_compression_gain_db", "audio", 0, 90);
  numberRange(root, "mic_agc_target_level_dbfs", "audio", 0, 31);
  if (!["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"].includes(String(root.mic_format))) throw new TypeError("audio.mic_format is unsupported");
  return typed<AudioConfig>(root);
}

export function decodeImage(value: unknown): ImagingConfig & JsonObject {
  const root = object(value, "image");
  for (const field of ["brightness", "contrast", "sharpness", "saturation", "hue", "backlight_compensation", "drc_strength", "defog_strength", "dpc_strength", "core_wb_mode", "wb_bgain", "wb_rgain", "ae_compensation"] as const) requiredNumber(root, field, "image");
  for (const field of ["highlight_depress", "sinter_strength"] as const) if (root[field] !== undefined) requiredNumber(root, field, "image");
  requiredBoolean(root, "hflip", "image");
  requiredBoolean(root, "vflip", "image");
  for (const field of ["brightness", "contrast", "sharpness", "saturation", "hue", "drc_strength", "defog_strength", "dpc_strength", "ae_compensation"] as const) numberRange(root, field, "image", 0, 255);
  for (const field of ["highlight_depress", "sinter_strength"] as const) if (root[field] !== undefined) numberRange(root, field, "image", 0, 255);
  numberRange(root, "backlight_compensation", "image", 0, 10);
  numberRange(root, "core_wb_mode", "image", 0, 9);
  for (const field of ["wb_bgain", "wb_rgain"] as const) numberRange(root, field, "image", 0, 1024);
  return typed<ImagingConfig>(root);
}

export function decodeImagingRuntime(value: unknown): ImagingRuntimeState & JsonObject {
  const root = object(value, "imaging runtime");
  if (root.code !== 200 || root.result !== "success") throw new TypeError("imaging runtime envelope is invalid");
  const message = object(root.message, "imaging runtime.message");
  const fields = object(message.fields, "imaging runtime.message.fields");
  const decoded: Record<string, ImagingRuntimeField> = {};
  for (const [name, raw] of Object.entries(fields)) {
    const field = object(raw, `imaging runtime field ${name}`);
    requiredBoolean(field, "supported", `imaging runtime field ${name}`);
    const result: ImagingRuntimeField = {
      supported: field.supported as boolean,
    };
    if (field.available !== undefined) {
      requiredBoolean(field, "available", `imaging runtime field ${name}`);
      result.available = field.available as boolean;
    }
    for (const property of ["min", "max", "value", "default"] as const) {
      if (field[property] === undefined || field[property] === null) continue;
      requiredNumber(field, property, `imaging runtime field ${name}`);
      result[property] = field[property] as number;
    }
    if (field.verification !== undefined) {
      if (field.verification !== "unsupported" && field.verification !== "sdk-readback" && field.verification !== "sdk-setter-config") {
        throw new TypeError(`imaging runtime field ${name} has invalid verification`);
      }
      result.verification = field.verification;
      for (const property of ["observed_value", "configured_value"] as const) {
        if (field[property] === null) result[property] = null;
        else {
          requiredNumber(field, property, `imaging runtime field ${name}`);
          result[property] = field[property] as number;
        }
      }
      if (field.saved_value !== undefined || field.matches_saved !== undefined) {
        if (name !== "anti_flicker") throw new TypeError(`imaging runtime field ${name} has unexpected saved state`);
        if (field.saved_value === null) result.saved_value = null;
        else {
          requiredNumber(field, "saved_value", `imaging runtime field ${name}`);
          result.saved_value = field.saved_value as number;
        }
        requiredBoolean(field, "matches_saved", `imaging runtime field ${name}`);
        result.matches_saved = field.matches_saved as boolean;
        const expectedMatch = result.available === true && result.value === result.configured_value && result.value === result.saved_value;
        if (result.matches_saved !== expectedMatch) throw new TypeError(`imaging runtime field ${name} has inconsistent saved state`);
      }
      if (name === "anti_flicker" && result.supported) {
        const values = [result.value, result.observed_value, result.configured_value, result.saved_value]
          .filter((candidate): candidate is number => typeof candidate === "number");
        if (result.min !== 0 || result.max !== 2 || result.matches_saved === undefined ||
            values.some(candidate => !Number.isInteger(candidate) || candidate < 0 || candidate > 2)) {
          throw new TypeError("anti-flicker observation is invalid");
        }
      }
      if ((result.verification === "unsupported" && (result.supported || result.available === true)) ||
          (result.verification === "sdk-readback" && result.available !== false && result.observed_value !== result.value) ||
          (result.verification === "sdk-setter-config" && (result.observed_value !== null || (result.available !== false && result.configured_value !== result.value)))) {
        throw new TypeError(`imaging runtime field ${name} has inconsistent verification`);
      }
    }
    if (result.supported && result.available !== false && (result.min === undefined || result.max === undefined || result.value === undefined || result.max <= result.min)) {
      throw new TypeError(`supported imaging runtime field ${name} has no valid range`);
    }
    decoded[name] = result;
  }
  let whiteBalance: ImagingWhiteBalanceRuntime | undefined;
  if (message.white_balance !== undefined) {
    const wb = object(message.white_balance, "imaging runtime.message.white_balance");
    for (const name of ["supported", "available", "gains_effective", "matches_saved"] as const) requiredBoolean(wb, name, "white balance");
    if (wb.verification !== "unsupported" && wb.verification !== "sdk-readback") throw new TypeError("white balance verification is invalid");
    if (!Array.isArray(wb.modes) || wb.modes.length !== 10 || wb.modes.some((mode, index) => mode !== index)) throw new TypeError("white balance modes are invalid");
    if (wb.gain_min !== 0 || wb.gain_max !== 1024) throw new TypeError("white balance gain range is invalid");
    const nullableInteger = (name: string, maximum: number): number | null => {
      const candidate = wb[name];
      if (candidate === null) return null;
      if (typeof candidate !== "number" || !Number.isInteger(candidate) || candidate < 0 || candidate > maximum) throw new TypeError(`white balance ${name} is invalid`);
      return candidate;
    };
    const mode = nullableInteger("mode", 9);
    const rgain = nullableInteger("rgain", 1024);
    const bgain = nullableInteger("bgain", 1024);
    const configured_mode = nullableInteger("configured_mode", 9);
    const configured_rgain = nullableInteger("configured_rgain", 1024);
    const configured_bgain = nullableInteger("configured_bgain", 1024);
    const saved_mode = nullableInteger("saved_mode", 9);
    const saved_rgain = nullableInteger("saved_rgain", 1024);
    const saved_bgain = nullableInteger("saved_bgain", 1024);
    const configuredComplete = configured_mode !== null && configured_rgain !== null && configured_bgain !== null;
    const savedComplete = saved_mode !== null && saved_rgain !== null && saved_bgain !== null;
    const configuredEmpty = configured_mode === null && configured_rgain === null && configured_bgain === null;
    const savedEmpty = saved_mode === null && saved_rgain === null && saved_bgain === null;
    const gainsEffective = wb.gains_effective === true;
    const available = wb.available === true;
    const supported = wb.supported === true;
    const expectedMatch = available && configuredComplete && savedComplete && configured_mode === saved_mode && configured_rgain === saved_rgain && configured_bgain === saved_bgain && mode === saved_mode && (mode !== 1 || (rgain === saved_rgain && bgain === saved_bgain));
    if ((!configuredComplete && !configuredEmpty) || (!savedComplete && !savedEmpty) ||
        (available ? mode === null || gainsEffective !== (mode === 1) || (mode === 1 ? rgain === null || bgain === null : rgain !== null || bgain !== null) : mode !== null || rgain !== null || bgain !== null || gainsEffective) ||
        (!supported && (available || wb.verification !== "unsupported")) || (supported && wb.verification !== "sdk-readback") || wb.matches_saved !== expectedMatch) {
      throw new TypeError("white balance observation is inconsistent");
    }
    whiteBalance = { supported, available, verification: wb.verification, modes: wb.modes as number[], gain_min: 0, gain_max: 1024, mode, gains_effective: gainsEffective, rgain, bgain, configured_mode, configured_rgain, configured_bgain, saved_mode, saved_rgain, saved_bgain, matches_saved: wb.matches_saved as boolean };
  }
  if (root.source !== "raptor") throw new TypeError("imaging runtime source is invalid");
  requiredBoolean(root, "persistent", "imaging runtime");
  return { fields: decoded, ...(whiteBalance ? { white_balance: whiteBalance } : {}), source: "raptor", persistent: root.persistent as boolean } as ImagingRuntimeState & JsonObject;
}

export function decodeStream(value: unknown, label = "stream"): StreamConfig & JsonObject {
  const root = object(value, label);
  for (const field of ["enabled", "audio_enabled"] as const) requiredBoolean(root, field, label);
  for (const field of ["width", "height", "fps", "gop", "max_gop", "bitrate", "profile", "buffers"] as const) requiredNumber(root, field, label);
  requiredString(root, "format", label);
  requiredString(root, "mode", label);
  requiredString(root, "rtsp_endpoint", label);
  if (root.format !== "H264" && root.format !== "H265") throw new TypeError(`${label}.format is unsupported`);
  if (!["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"].includes(String(root.mode))) throw new TypeError(`${label}.mode is unsupported`);
  for (const field of ["width", "height"] as const) numberRange(root, field, label, 1, 16_384);
  numberRange(root, "fps", label, 0, 30);
  for (const field of ["gop", "max_gop"] as const) numberRange(root, field, label, 1, 65_535);
  numberRange(root, "bitrate", label, 1, 100_000_000);
  numberRange(root, "profile", label, 0, 2);
  if (root.buffers !== -1 && (typeof root.buffers !== "number" || root.buffers < 1 || root.buffers > 65_535)) throw new TypeError(`${label}.buffers is invalid`);
  return typed<StreamConfig>(root);
}

function decodeRuntimeStreamState(value: unknown, label: string): RuntimeStream {
  const root = object(value, label);
  const snapshot = root.snapshot_url;
  let snapshot_url: string | null;
  if (snapshot === null) snapshot_url = null;
  else {
    if (typeof snapshot !== "string") throw new TypeError(`${label}.snapshot_url must be a string or null`);
    snapshot_url = snapshot;
  }
  const result: RuntimeStream = {
    available: readBoolean(root, "available", label),
    enabled: readBoolean(root, "enabled", label),
    snapshot_url,
  };
  for (const field of ["width", "height", "fps", "bitrate", "gop"] as const) {
    if (root[field] === undefined || root[field] === null) continue;
    numberRange(root, field, label, 0, 100_000_000);
    result[field] = root[field] as number;
  }
  if (root.format !== undefined && root.format !== null) {
    if (root.format !== "H264" && root.format !== "H265") throw new TypeError(`${label}.format is unsupported`);
    result.format = root.format;
  }
  if (root.rtsp_endpoint !== undefined && root.rtsp_endpoint !== null) {
    requiredString(root, "rtsp_endpoint", label);
    result.rtsp_endpoint = root.rtsp_endpoint as string;
  }
  return result;
}

export function decodeRuntimeMedia(value: unknown): RuntimeMediaResponse {
  const root = object(value, "runtime media");
  const streams = object(root.streams, "runtime media.streams");
  const rtsp = root.rtsp === undefined ? undefined : object(root.rtsp, "runtime media.rtsp");
  if (rtsp?.port !== undefined) numberRange(rtsp, "port", "runtime media.rtsp", 1, 65535);
  if (rtsp?.username !== undefined) requiredString(rtsp, "username", "runtime media.rtsp");
  return {
    ...(rtsp ? { rtsp: {
      ...(rtsp.port !== undefined ? { port: rtsp.port as number } : {}),
      ...(rtsp.username !== undefined ? { username: rtsp.username as string } : {}),
    } } : {}),
    streams: {
      ch0: decodeRuntimeStreamState(streams.ch0, "runtime media.streams.ch0"),
      ch1: decodeRuntimeStreamState(streams.ch1, "runtime media.streams.ch1"),
    },
  };
}

export function decodeWebui(value: unknown): WebuiConfig & JsonObject {
  const root = object(value, "webui");
  requiredString(root, "username", "webui");
  requiredString(root, "theme", "webui");
  requiredBoolean(root, "paranoid", "webui");
  requiredBoolean(root, "track_focus", "webui");
  requiredNumber(root, "focus_timeout", "webui");
  if (root.auth_bypass_ips !== undefined) requiredString(root, "auth_bypass_ips", "webui");
  if (!["light", "dark", "auto"].includes(String(root.theme))) throw new TypeError("webui.theme is unsupported");
  numberRange(root, "focus_timeout", "webui", 0, 300);
  return typed<WebuiConfig>(root);
}

export function decodeRemoteLogging(value: unknown): RemoteLoggingConfig & JsonObject {
  const root = object(value, "rsyslog");
  requiredString(root, "host", "rsyslog");
  requiredNumber(root, "port", "rsyslog");
  requiredBoolean(root, "enabled", "rsyslog");
  requiredBoolean(root, "file", "rsyslog");
  numberRange(root, "port", "rsyslog", 1, 65_535);
  return typed<RemoteLoggingConfig>(root);
}

export function decodeDayNight(value: unknown): DayNightConfig & JsonObject {
  const root = object(value, "daynight");
  requiredBoolean(root, "enabled", "daynight");
  requiredString(root, "initial_mode", "daynight");
  requiredString(root, "force_mode", "daynight");
  requiredString(root, "loglevel", "daynight");
  for (const field of ["night_threshold", "day_threshold", "night_count_threshold", "day_count_threshold", "sample_interval_ms", "transition_delay_s"] as const) requiredNumber(root, field, "daynight");
  const controls = object(root.controls, "daynight.controls");
  for (const field of ["color", "ircut", "ir850"] as const) requiredBoolean(controls, field, "daynight.controls");
  const schedule = object(root.schedule, "daynight.schedule");
  requiredBoolean(schedule, "enabled", "daynight.schedule");
  requiredString(schedule, "start_at", "daynight.schedule");
  requiredString(schedule, "stop_at", "daynight.schedule");
  const sun = object(root.sun, "daynight.sun");
  requiredBoolean(sun, "enabled", "daynight.sun");
  for (const field of ["latitude", "longitude", "sunrise_offset", "sunset_offset"] as const) requiredNumber(sun, field, "daynight.sun");
  numberRange(root, "night_threshold", "daynight", 0, 100);
  numberRange(root, "day_threshold", "daynight", 0, 100);
  numberRange(root, "night_count_threshold", "daynight", 1, 255);
  numberRange(root, "day_count_threshold", "daynight", 1, 255);
  numberRange(root, "sample_interval_ms", "daynight", 100, 60_000);
  numberRange(root, "transition_delay_s", "daynight", 0, 300);
  if (!["", "day", "night"].includes(String(root.initial_mode)) || !["", "day", "night"].includes(String(root.force_mode))) throw new TypeError("daynight mode is unsupported");
  if (!["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"].includes(String(root.loglevel))) throw new TypeError("daynight.loglevel is unsupported");
  numberRange(sun, "latitude", "daynight.sun", -90, 90);
  numberRange(sun, "longitude", "daynight.sun", -180, 180);
  numberRange(sun, "sunrise_offset", "daynight.sun", -1_440, 1_440);
  numberRange(sun, "sunset_offset", "daynight.sun", -1_440, 1_440);
  return typed<DayNightConfig>(root);
}

export function decodeGpio(value: unknown): GpioConfig & JsonObject {
  const root = object(value, "gpio");
  const gpio = object(root.gpio, "gpio.gpio");
  if (root.profile !== "D-Link DCS-6100LHV2 A1") throw new TypeError("gpio.profile is unsupported");
  if (gpio.ircut !== "50 49" || gpio.ir850 !== 61 || Object.hasOwn(gpio, "ir940") || Object.hasOwn(gpio, "white")) throw new TypeError("gpio assignments do not match D-Link A1");
  const led = object(root.led, "gpio.led");
  if (!new Set(["off", "green", "red"]).has(String(led.startup_indicator))) throw new TypeError("gpio startup indicator is unsupported");
  if (root.available_startup_indicators !== "green,red" || root.pwm_pins !== "") throw new TypeError("gpio capabilities do not match D-Link A1");
  if (!Array.isArray(root.hardware_io) || root.hardware_io.length !== 10) throw new TypeError("gpio hardware_io is incomplete");
  const expectedPins = new Set([18, 49, 50, 52, 54, 57, 59, 60, 61, 63]);
  const seen = new Set<number>();
  root.hardware_io.forEach((entryValue, index) => {
    const entry = object(entryValue, `gpio.hardware_io[${index}]`);
    requiredNumber(entry, "gpio", `gpio.hardware_io[${index}]`);
    requiredString(entry, "function", `gpio.hardware_io[${index}]`);
    requiredString(entry, "owner", `gpio.hardware_io[${index}]`);
    requiredBoolean(entry, "read_only", `gpio.hardware_io[${index}]`);
    if (!expectedPins.has(Number(entry.gpio)) || seen.has(Number(entry.gpio)) || entry.read_only !== true || !["input", "output"].includes(String(entry.direction))) throw new TypeError("gpio hardware_io entry is invalid");
    seen.add(Number(entry.gpio));
  });
  return typed<GpioConfig>(root);
}

export function decodeHomeAssistant(value: unknown): HomeAssistantConfig & JsonObject {
  const root = object(value, "Home Assistant");
  requiredBoolean(root, "enabled", "Home Assistant");
  const mqtt = object(root.mqtt, "Home Assistant.mqtt");
  requiredString(mqtt, "host", "Home Assistant.mqtt");
  requiredNumber(mqtt, "port", "Home Assistant.mqtt");
  requiredString(mqtt, "username", "Home Assistant.mqtt");
  if (mqtt.password !== null) requiredString(mqtt, "password", "Home Assistant.mqtt");
  requiredBoolean(mqtt, "password_set", "Home Assistant.mqtt");
  requiredString(mqtt, "client_id_prefix", "Home Assistant.mqtt");
  requiredBoolean(mqtt, "use_ssl", "Home Assistant.mqtt");
  requiredBoolean(mqtt, "tls_skip_verify", "Home Assistant.mqtt");
  for (const field of ["device_name", "device_model", "discovery_prefix"] as const) requiredString(root, field, "Home Assistant");
  for (const field of ["state_interval", "discovery_interval", "camera_interval", "ota_check_interval"] as const) requiredNumber(root, field, "Home Assistant");
  numberRange(mqtt, "port", "Home Assistant.mqtt", 1, 65_535);
  for (const field of ["state_interval", "discovery_interval", "camera_interval", "ota_check_interval"] as const) numberRange(root, field, "Home Assistant", 1, 604_800);
  for (const [field, fieldValue] of Object.entries(root)) if (field.startsWith("enable_") && typeof fieldValue !== "boolean") throw new TypeError(`Home Assistant.${field} must be a boolean`);
  if (root.doorbell_supported !== false || root.ota_supported !== false) throw new TypeError("Home Assistant unsupported capabilities must be false");
  return typed<HomeAssistantConfig>(root);
}

export function decodeHomeAssistantRuntime(value: unknown): HomeAssistantRuntime & JsonObject {
  const root = object(value, "Home Assistant runtime");
  requiredBoolean(root, "enabled", "Home Assistant runtime");
  requiredString(root, "state", "Home Assistant runtime");
  if (!["disabled", "connecting", "online", "backoff", "error"].includes(String(root.state))) throw new TypeError("Home Assistant runtime.state is invalid");
  requiredBoolean(root, "connected", "Home Assistant runtime");
  for (const field of ["last_connect_unix", "last_disconnect_unix", "reconnect_in_ms"] as const) {
    if (root[field] !== null) numberRange(root, field, "Home Assistant runtime", 0, Number.MAX_SAFE_INTEGER);
  }
  if (root.last_error !== null) requiredString(root, "last_error", "Home Assistant runtime");
  for (const field of ["queue_depth", "queue_high_water_mark", "published_messages", "received_commands", "rejected_commands", "dropped_messages"] as const) {
    numberRange(root, field, "Home Assistant runtime", 0, Number.MAX_SAFE_INTEGER);
  }
  if (Number(root.queue_depth) > 64 || Number(root.queue_high_water_mark) > 64) throw new TypeError("Home Assistant runtime queue counters exceed the contract");
  return typed<HomeAssistantRuntime>(root);
}

export function decodeMotion(value: unknown): MotionConfig & JsonObject {
  const root = object(value, "motion");
  requiredBoolean(root, "enabled", "motion");
  requiredNumber(root, "sensitivity", "motion");
  numberRange(root, "sensitivity", "motion", 1, 8);
  for (const field of ["playonspeaker", "send2email", "send2ftp", "send2gotify", "send2mqtt", "send2ntfy", "send2storage", "send2telegram", "send2webhook"] as const) {
    if (root[field] !== undefined) requiredBoolean(root, field, "motion");
  }
  if (root.cooldown_time !== undefined) numberRange(root, "cooldown_time", "motion", 1, 60);
  for (const field of ["debounce_time", "post_time", "init_time", "min_time", "skip_frame_count", "video_length"] as const) {
    if (root[field] !== undefined) numberRange(root, field, "motion", 0, Number.MAX_SAFE_INTEGER);
  }
  if (root.ivs_polling_timeout !== undefined) numberRange(root, "ivs_polling_timeout", "motion", 100, 10_000);
  if (root.motor_settle_ms !== undefined) numberRange(root, "motor_settle_ms", "motion", 0, 10_000);
  for (const field of ["frame_width", "frame_height", "roi_0_x", "roi_0_y", "roi_1_x", "roi_1_y"] as const) {
    if (root[field] !== undefined) numberRange(root, field, "motion", 0, 16_384);
  }
  if (root.monitor_stream !== undefined) numberRange(root, "monitor_stream", "motion", 0, 3);
  if (root.roi_count !== undefined) numberRange(root, "roi_count", "motion", 1, 52);
  return typed<MotionConfig>(root);
}

export function decodePrivacy(value: unknown): PrivacyConfig & JsonObject {
  const root = object(value, "privacy");
  for (const field of ["enabled", "stream0_enabled", "stream1_enabled"] as const) requiredBoolean(root, field, "privacy");
  return typed<PrivacyConfig>(root);
}

export function decodeOsd(value: unknown): OsdConfig & JsonObject {
  const root = object(value, "OSD");
  const burnin = object(root.burnin, "OSD.burnin");
  requiredBoolean(burnin, "enabled", "OSD.burnin");
  if (burnin.format !== undefined) requiredString(burnin, "format", "OSD.burnin");
  requiredNumber(burnin, "scale", "OSD.burnin");
  for (const field of ["fill_color", "outline_color", "background_color"] as const) requiredString(burnin, field, "OSD.burnin");
  numberRange(burnin, "scale", "OSD.burnin", 0, 10);
  for (const field of ["fill_color", "outline_color", "background_color"] as const) {
    if (!/^#[0-9a-fA-F]{8}$/.test(String(burnin[field]))) throw new TypeError(`OSD.burnin.${field} must use #RRGGBBAA`);
  }
  const sei = object(root.sei, "OSD.sei");
  requiredBoolean(sei, "enabled", "OSD.sei");
  const entries = object(sei.entries, "OSD.sei.entries");
  for (const [name, entryValue] of Object.entries(entries)) {
    const entry = object(entryValue, `OSD.sei.entries.${name}`);
    for (const field of ["type", "format", "position"] as const) requiredString(entry, field, `OSD.sei.entries.${name}`);
    if (!["text", "gain", "hostname", "ipaddress", "timestamp", "uptime"].includes(String(entry.type))) throw new TypeError(`OSD.sei.entries.${name}.type is unsupported`);
  }
  return typed<OsdConfig>(root);
}

function decodeRaptorTimelapsePolicy(value: unknown): RaptorTimelapsePolicy & JsonObject {
  const data = object(value, "timelapse");
  if (Object.keys(data).length !== 8 || data.mount !== "/mnt/mmcblk0p1" || data.filepath !== "raptor/timelapse" || data.filename !== "unix-seconds-sequence.jpg") throw new TypeError("Timelapse storage paths must be fixed");
  requiredBoolean(data, "enabled", "timelapse");
  requiredBoolean(data, "preset_enabled", "timelapse");
  numberRange(data, "interval", "timelapse", 1, 1440);
  numberRange(data, "keep_days", "timelapse", 0, 365);
  if (!Number.isInteger(data.interval) || !Number.isInteger(data.keep_days)) throw new TypeError("Timelapse intervals must be integers");
  const presets = object(data.presets, "timelapse.presets");
  if (Object.keys(presets).length !== 3) throw new TypeError("Unsupported timelapse preset");
  for (const field of ["ircut", "ir850", "color"] as const) requiredBoolean(presets, field, "timelapse.presets");
  return typed<RaptorTimelapsePolicy>(data);
}

export function decodeRaptorTimelapse(value: unknown): RaptorTimelapseResponse & JsonObject {
  const root = object(value, "timelapse");
  const data = object(root.data, "timelapse.data");
  if (root.ok !== true || data.source !== "raptor" || data.domain !== "timelapse" || data.persistent !== true) throw new TypeError("Invalid Raptor timelapse envelope");
  requiredBoolean(data, "available", "timelapse.data");
  requiredBoolean(data, "matches_saved", "timelapse.data");
  const policy = decodeRaptorTimelapsePolicy(data.timelapse);
  if (data.saved_timelapse !== null) decodeRaptorTimelapsePolicy(data.saved_timelapse);
  if (data.matches_saved && JSON.stringify(data.saved_timelapse) !== JSON.stringify(data.timelapse)) {
    const saved = object(data.saved_timelapse, "timelapse.saved");
    for (const field of ["enabled", "mount", "filepath", "filename", "interval", "keep_days", "preset_enabled"]) if (saved[field] !== policy[field]) throw new TypeError("Timelapse saved state differs");
    const savedPresets = object(saved.presets, "timelapse.saved.presets");
    for (const field of ["ircut", "ir850", "color"] as const) if (savedPresets[field] !== policy.presets[field]) throw new TypeError("Timelapse saved presets differ");
  }
  if (!Array.isArray(data.mounts) || data.mounts.length !== 1 || data.mounts[0] !== policy.mount) throw new TypeError("Invalid timelapse mount");
  const runtime = object(data.runtime, "timelapse.runtime");
  const phases = ["starting", "idle", "disabled", "error", "capturing", "staging", "staging_io", "before_publish", "published", "restoring"];
  if (!phases.includes(String(runtime.phase))) throw new TypeError("Invalid timelapse phase");
  if (!["not_used", "restored", "conflict"].includes(String(runtime.preset_restore))) throw new TypeError("Invalid preset restoration state");
  const errors = ["invalid_saved_config", "config_unavailable", "retention_unavailable", "privacy_or_settings_changed", "preset_unavailable", "preset_apply_failed", "preset_restore_failed", "jpeg_unavailable", "capture_failed", "storage_unavailable", "staging_failed", "publish_failed", "storage_changed", "cleanup_failed", "invalidated_image_removed"];
  if (runtime.last_error !== null && !errors.includes(String(runtime.last_error))) throw new TypeError("Invalid timelapse error");
  numberRange(runtime, "successes", "timelapse.runtime", 0, Number.MAX_SAFE_INTEGER);
  for (const field of ["last_success", "next_due"]) if (runtime[field] !== null) numberRange(runtime, field, "timelapse.runtime", 0, Number.MAX_SAFE_INTEGER);
  requiredBoolean(runtime, "cleanup_blocked", "timelapse.runtime");
  return typed<RaptorTimelapseResponse>(root);
}

export function decodeRecorder(value: unknown): RecorderResponse & JsonObject {
  const root = object(value, "recorder");
  if (root.ok !== true) throw new TypeError("recorder.ok must be true");
  const data = object(root.data, "recorder.data");
  const video = object(data.video, "recorder.data.video");
  for (const field of ["autostart", "cleanup_enabled"] as const) requiredBoolean(video, field, "recorder.data.video");
  for (const field of ["mount", "device_path", "filename"] as const) requiredString(video, field, "recorder.data.video");
  for (const field of ["channel", "duration", "limit", "min_free_mb", "check_interval"] as const) requiredNumber(video, field, "recorder.data.video");
  numberRange(video, "channel", "recorder.data.video", 0, 1);
  numberRange(video, "duration", "recorder.data.video", 1, 86400);
  numberRange(video, "check_interval", "recorder.data.video", 1, 86400);
  numberRange(video, "limit", "recorder.data.video", 1, 100000);
  numberRange(video, "min_free_mb", "recorder.data.video", 1, 1000000);
  if (data.source === "raptor") {
    if (data.persistent !== true || typeof data.timelapse_supported !== "boolean") throw new TypeError("Unsupported Raptor recorder contract");
    if (data.timelapse_supported) decodeRaptorTimelapsePolicy(data.timelapse);
    else if (data.timelapse !== null) throw new TypeError("Unsupported timelapse must be null");
    for (const field of ["matches_saved", "storage_available", "boot_pending", "cleanup_ok"] as const) requiredBoolean(data, field, "recorder.data");
    numberRange(video, "limit", "recorder.data.video", 1, 1000);
    if (video.mount !== "/mnt/mmcblk0p1" || video.device_path !== `raptor/stream${video.channel}` || video.filename !== "%Y-%m-%d/%H-%M-%S") throw new TypeError("Raptor recorder paths must be fixed");
    if (data.storage_available) numberRange(data, "free_mb", "recorder.data", 0, Number.MAX_SAFE_INTEGER);
    else if (data.free_mb !== null) throw new TypeError("Unavailable storage must have unknown free space");
    if (data.runtime !== null) object(data.runtime, "recorder.data.runtime");
    if (data.saved_video !== null) {
      const saved = object(data.saved_video, "recorder.data.saved_video");
      decodeRecorder({ ok: true, data: { ...data, video: saved, saved_video: null, matches_saved: false } });
      if (saved.channel !== video.channel) throw new TypeError("Saved recorder channel differs");
    }
    if (data.matches_saved && (data.saved_video === null || Object.keys(video).some((key) => video[key] !== (data.saved_video as JsonObject)[key]))) throw new TypeError("Recorder saved state differs");
    if (!Array.isArray(data.mounts) || data.mounts.length !== 1 || data.mounts[0] !== video.mount) throw new TypeError("Invalid recorder mount");
    return typed<RecorderResponse>(root);
  }
  const timelapse = object(data.timelapse, "recorder.data.timelapse");
  for (const field of ["enabled", "preset_enabled"] as const) requiredBoolean(timelapse, field, "recorder.data.timelapse");
  for (const field of ["mount", "filepath", "filename"] as const) requiredString(timelapse, field, "recorder.data.timelapse");
  for (const field of ["interval", "keep_days"] as const) requiredNumber(timelapse, field, "recorder.data.timelapse");
  numberRange(timelapse, "interval", "recorder.data.timelapse", 1, 1440);
  numberRange(timelapse, "keep_days", "recorder.data.timelapse", 0, 365);
  const presets = object(timelapse.presets, "recorder.data.timelapse.presets");
  for (const field of ["ircut", "ir850", "color"] as const) requiredBoolean(presets, field, "recorder.data.timelapse.presets");
  if (!Array.isArray(data.mounts)) throw new TypeError("recorder.data.mounts must be an array");
  return typed<RecorderResponse>(root);
}

export function decodeSendConfig(value: unknown): JsonObject {
  const root = object(value, "send destinations");
  const domains = ["email", "ftp", "gphotos", "gotify", "mqtt", "ntfy", "storage", "telegram", "webhook"] as const;
  const booleanFields = new Set(["enabled", "use_ssl", "trust_cert", "tls_skip_verify", "send_photo", "send_video", "silent"]);
  const numberFields = new Set(["port", "priority"]);
  const secretFields = new Set(["password", "token", "client_secret", "refresh_token"]);
  for (const domain of domains) {
    const config = object(root[domain], `send destinations.${domain}`);
    for (const [field, fieldValue] of Object.entries(config)) {
      if (field.endsWith("_set")) {
        if (typeof fieldValue !== "boolean") throw new TypeError(`send destinations.${domain}.${field} must be a boolean`);
      } else if (booleanFields.has(field)) {
        if (typeof fieldValue !== "boolean") throw new TypeError(`send destinations.${domain}.${field} must be a boolean`);
      } else if (numberFields.has(field)) {
        if (typeof fieldValue !== "number" || !Number.isFinite(fieldValue)) throw new TypeError(`send destinations.${domain}.${field} must be a finite number`);
        if (field === "port" && (!Number.isInteger(fieldValue) || fieldValue < 1 || fieldValue > 65_535)) throw new TypeError(`send destinations.${domain}.port is outside 1..65535`);
        if (field === "priority" && (!Number.isInteger(fieldValue) || fieldValue < 0 || fieldValue > 10)) throw new TypeError(`send destinations.${domain}.priority is outside 0..10`);
      } else if (secretFields.has(field)) {
        if (fieldValue !== null && typeof fieldValue !== "string") throw new TypeError(`send destinations.${domain}.${field} must be null or a string`);
      } else if (field === "extras") {
        if (typeof fieldValue !== "string") throw new TypeError(`send destinations.${domain}.extras must be a JSON-encoded string`);
        if (fieldValue.trim() !== "") {
          let parsed: unknown;
          try {
            parsed = JSON.parse(fieldValue);
          } catch {
            throw new TypeError(`send destinations.${domain}.extras must contain valid JSON`);
          }
          object(parsed, `send destinations.${domain}.extras JSON`);
        }
      } else if (typeof fieldValue !== "string" && fieldValue !== null) {
        throw new TypeError(`send destinations.${domain}.${field} has an invalid type`);
      }
    }
  }
  if (root.motion !== undefined) object(root.motion, "send destinations.motion");
  if (root.meta !== undefined) {
    const meta = object(root.meta, "send destinations.meta");
    if (meta.mounts !== undefined && !Array.isArray(meta.mounts)) throw new TypeError("send destinations.meta.mounts must be an array");
  }
  return root as JsonObject;
}

export function decodeSession(value: unknown): SessionState {
  if (!isRecord(value) || typeof value.authenticated !== "boolean") {
    throw new TypeError("session response is invalid");
  }
  if (value.username !== null && typeof value.username !== "string") {
    throw new TypeError("session username is invalid");
  }
  if (typeof value.is_default_password !== "boolean" || typeof value.client_ip !== "string") {
    throw new TypeError("session metadata is invalid");
  }
  if (!isRecord(value.control_api) || value.control_api.name !== "Thingino Control" || value.control_api.version !== 1) {
    throw new TypeError("session Control API metadata is invalid");
  }
  return value as unknown as SessionState;
}

export function decodeHeartbeat(value: unknown): RuntimeHeartbeat {
  if (!isRecord(value)) throw new TypeError("heartbeat response is invalid");
  const numbers = ["time_now", "uptime"] as const;
  const nullableNumbers = ["daynight_brightness", "total_gain"] as const;
  const booleans = [
    "rec_ch0", "rec_ch1", "timelapse_enabled", "motion_enabled", "motion_active", "motion_ingress_ready", "privacy_enabled",
    "mic_enabled", "spk_enabled", "daynight_enabled",
  ] as const;
  const nullableBinary = ["color_mode", "ircut_state", "ir850_state", "ir940_state", "white_state"] as const;
  if (numbers.some((field) => typeof value[field] !== "number" || !Number.isFinite(value[field]))) {
    throw new TypeError("heartbeat numeric state is invalid");
  }
  if (nullableNumbers.some((field) => value[field] !== null && (typeof value[field] !== "number" || !Number.isFinite(value[field])))) {
    throw new TypeError("heartbeat sensor state is invalid");
  }
  if (booleans.some((field) => value[field] !== null && typeof value[field] !== "boolean")) {
    throw new TypeError("heartbeat boolean state is invalid");
  }
  if (nullableBinary.some((field) => value[field] !== null && value[field] !== 0 && value[field] !== 1)) {
    throw new TypeError("heartbeat physical state is invalid");
  }
  if (value.daynight_mode !== "day" && value.daynight_mode !== "night" && value.daynight_mode !== "unknown") {
    throw new TypeError("heartbeat day/night mode is invalid");
  }
  for (const channel of [0, 1] as const) {
    const closed = value[`rec_ch${channel}_file_closed`];
    if (closed !== undefined && typeof closed !== "boolean") throw new TypeError("heartbeat recording closure is invalid");
    const available = value[`rec_ch${channel}_available`];
    const reason = value[`rec_ch${channel}_reason`];
    if (available !== undefined && typeof available !== "boolean") throw new TypeError("heartbeat recording availability is invalid");
    if (reason !== undefined && reason !== null && (typeof reason !== "string" || !["no_sd", "storage_unknown", "no_space", "video_unavailable", "writer_error", "starting", "stopping"].includes(reason))) throw new TypeError("heartbeat recording reason is invalid");
  }
  if (value.controls_supported !== undefined) {
    const supported = object(value.controls_supported, "heartbeat.controls_supported");
    for (const [name, state] of Object.entries(supported)) {
      if (!["daynight", "motion", "privacy"].includes(name) || typeof state !== "boolean") throw new TypeError("heartbeat control capability is invalid");
    }
  }
  return value as unknown as RuntimeHeartbeat;
}

export function decodeMotionRuntime(value: unknown): MotionRuntime | RaptorMotionRuntime {
  const root = object(value, "motion runtime");
  if (root.version !== 1) throw new TypeError("motion runtime.version is unsupported");
  if (root.source === "raptor") {
    for (const field of ["supported", "available", "monitoring", "active", "receiving"] as const) requiredBoolean(root, field, "motion runtime");
    return typed<RaptorMotionRuntime>(root);
  }
  for (const field of ["ingress_ready", "monitoring", "active"] as const) requiredBoolean(root, field, "motion runtime");
  requiredNumber(root, "channel", "motion runtime");
  if (root.channel !== 0 && root.channel !== 1) throw new TypeError("motion runtime.channel is invalid");
  for (const field of ["producer_pid", "producer_sequence", "last_observation_monotonic_ms", "last_transition_unix_ms"] as const) {
    if (root[field] !== null) requiredNumber(root, field, "motion runtime");
  }
  for (const field of ["received", "rejected", "accepted", "duplicate_or_replayed", "producer_restarts", "transitions", "unsupported_destination_events"] as const) requiredNumber(root, field, "motion runtime");
  for (const groupName of ["queue", "sink", "speaker", "clips"] as const) {
    const group = object(root[groupName], `motion runtime.${groupName}`);
    const fields = groupName === "queue" ? ["capacity", "depth", "high_water", "dropped", "coalesced"] : groupName === "sink" ? ["queued", "coalesced", "dropped", "disabled"] : groupName === "clips" ? ["capacity", "pending", "requested", "ready", "dropped", "recovered", "manifest_errors"] : ["queued", "dropped"];
    for (const field of fields) requiredNumber(group, field, `motion runtime.${groupName}`);
  }
  return root as unknown as MotionRuntime;
}

export function decodeApiError(value: unknown): ApiErrorEnvelope | null {
  if (!isRecord(value) || value.status !== "error" || !isRecord(value.error)) return null;
  if (typeof value.error.code !== "string" || typeof value.error.message !== "string") return null;
  return value as unknown as ApiErrorEnvelope;
}

/* Information/Tools decoders deliberately return view models instead of a
 * JsonObject cast. The backend responses below are fixed-shape contracts. */
function readString(value: Record<string, unknown>, field: string, label: string): string {
  requiredString(value, field, label);
  return value[field] as string;
}

function readBoolean(value: Record<string, unknown>, field: string, label: string): boolean {
  requiredBoolean(value, field, label);
  return value[field] as boolean;
}

function readNumber(value: Record<string, unknown>, field: string, label: string): number {
  requiredNumber(value, field, label);
  return value[field] as number;
}

function jsonValue(value: unknown, label: string): import("./contracts").JsonValue {
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map((entry, index) => jsonValue(entry, `${label}[${index}]`));
  if (isRecord(value)) {
    const result: JsonObject = {};
    for (const [key, entry] of Object.entries(value)) result[key] = jsonValue(entry, `${label}.${key}`);
    return result;
  }
  throw new TypeError(`${label} is not JSON data`);
}

function jsonObject(value: unknown, label: string): JsonObject {
  if (!isRecord(value)) throw new TypeError(`${label} must be a JSON object`);
  return jsonValue(value, label) as JsonObject;
}

function decodeUsage(value: unknown, label: string, requireUsed: boolean): RuntimeSystem["memory"] {
  const usage = object(value, label);
  const total = readNumber(usage, "total", label);
  const free = readNumber(usage, "free", label);
  const used = usage.used === undefined && !requireUsed ? total - free : readNumber(usage, "used", label);
  const result: RuntimeSystem["memory"] = { total, free, used };
  for (const field of ["active", "buffers", "cached"] as const) {
    if (usage[field] !== undefined) result[field] = readNumber(usage, field, label);
  }
  return result;
}

function decodeRuntimeInterface(value: unknown, label: string): Partial<NetworkInterfaceConfig> {
  const source = object(value, label);
  const result: Partial<NetworkInterfaceConfig> = {};
  if (source.enabled !== undefined) result.enabled = readBoolean(source, "enabled", label);
  if (source.dhcp !== undefined) result.dhcp = readBoolean(source, "dhcp", label);
  if (source.ipv6 !== undefined) result.ipv6 = readBoolean(source, "ipv6", label);
  if (source.link_up !== undefined) result.link_up = readBoolean(source, "link_up", label);
  for (const field of ["mac", "address", "netmask", "gateway", "broadcast"] as const) {
    if (source[field] !== undefined) result[field] = readString(source, field, label);
  }
  return result;
}

export function decodeRuntimeSystem(value: unknown): RuntimeSystem {
  const envelope = object(value, "runtime system");
  if (envelope.code !== 200 || envelope.result !== "success") throw new TypeError("runtime system envelope is invalid");
  const data = object(envelope.data, "runtime system.data");
  const network = object(data.network, "runtime system.network");
  const interfaces = object(network.interfaces, "runtime system.network.interfaces");
  const decodedInterfaces: Record<string, Partial<NetworkInterfaceConfig>> = {};
  for (const [name, entry] of Object.entries(interfaces)) decodedInterfaces[name] = decodeRuntimeInterface(entry, `runtime system.network.interfaces.${name}`);
  const media = data.media === null ? null : object(data.media, "runtime system.media");
  return {
    memory: decodeUsage(data.memory, "runtime system.memory", false),
    overlay: decodeUsage(data.overlay, "runtime system.overlay", true),
    extras: decodeUsage(data.extras, "runtime system.extras", true),
    network: {
      online: readBoolean(network, "online", "runtime system.network"),
      ip: readString(network, "ip", "runtime system.network"),
      interfaces: decodedInterfaces,
    },
    media: {
      prudynt_running: media === null ? null : readBoolean(media, "prudynt_running", "runtime system.media"),
      media_ready: media === null ? null : readBoolean(media, "media_ready", "runtime system.media"),
      stream0_enabled: media === null ? null : readBoolean(media, "stream0_enabled", "runtime system.media"),
      stream1_enabled: media === null ? null : readBoolean(media, "stream1_enabled", "runtime system.media"),
    },
    timestamp: readNumber(data, "timestamp", "runtime system"),
  };
}

export function decodeOverlayStatus(value: unknown): OverlayStatus {
  const root = object(value, "overlay");
  const usage = object(root.usage, "overlay.usage");
  if (readNumber(usage, "percent", "overlay.usage") < 0 || readNumber(usage, "percent", "overlay.usage") > 100) throw new TypeError("overlay usage percent is out of range");
  if (readString(root, "path", "overlay") !== "/overlay") throw new TypeError("overlay path is invalid");
  return {
    usage: { label: readString(usage, "label", "overlay.usage"), percent: readNumber(usage, "percent", "overlay.usage"), state: readString(usage, "state", "overlay.usage") },
    listing_base64: readString(root, "listing_base64", "overlay"),
    path: "/overlay",
  };
}

export function decodeSdStatus(value: unknown): SdStatus {
  const root = object(value, "SD");
  if (root.ok !== true) throw new TypeError("SD.ok must be true");
  const data = object(root.data, "SD.data");
  const reports = object(data.reports, "SD.data.reports");
  const format = object(data.format, "SD.data.format");
  const formatSupported = readBoolean(format, "supported", "SD.data.format");
  if (!Array.isArray(format.options)) throw new TypeError("SD.data.format.options must be an array");
  const options = format.options.map((entryValue, index) => {
    const entry = object(entryValue, `SD.data.format.options[${index}]`);
    if (entry.id !== "fat32") throw new TypeError("SD format option must be fat32");
    return { id: "fat32" as const, label: readString(entry, "label", "SD format option"), description: readString(entry, "description", "SD format option") };
  });
  if (formatSupported !== (options.length === 1)) throw new TypeError("SD formatting support and options disagree");
  const formatStatus = readString(format, "status", "SD.data.format");
  if (!(formatStatus === "idle" || formatStatus === "queued" || formatStatus === "running" || formatStatus === "succeeded" || formatStatus === "failed")) throw new TypeError("SD format status is invalid");
  const messages = object(data.messages, "SD.data.messages");
  const debug = object(data.debug, "SD.data.debug");
  if (!Array.isArray(data.filesystems)) throw new TypeError("SD.data.filesystems must be an array");
  if (debug.detection !== "sysfs" && debug.detection !== "mount-table" && debug.detection !== "device-node" && debug.detection !== "none") throw new TypeError("SD.data.debug.detection is invalid");
  const hasSdcard = readBoolean(data, "has_sdcard", "SD.data");
  let device: SdStatus["data"]["device"] = null;
  if (data.device !== null) {
    const item = object(data.device, "SD.data.device");
    device = {
      name: readString(item, "name", "SD.data.device"), node: readString(item, "node", "SD.data.device"),
      vendor: readString(item, "vendor", "SD.data.device"), model: readString(item, "model", "SD.data.device"),
      size_bytes: item.size_bytes === null ? null : readNumber(item, "size_bytes", "SD.data.device"),
    };
  }
  if (hasSdcard !== (device !== null)) throw new TypeError("SD card presence and device identity disagree");
  const filesystems = data.filesystems.map((entryValue, index) => {
    const entry = object(entryValue, `SD.data.filesystems[${index}]`);
    return {
      device: readString(entry, "device", `SD.data.filesystems[${index}]`), mountpoint: readString(entry, "mountpoint", `SD.data.filesystems[${index}]`), filesystem: readString(entry, "filesystem", `SD.data.filesystems[${index}]`),
      writable: readBoolean(entry, "writable", `SD.data.filesystems[${index}]`), total_kib: entry.total_kib === null ? null : readNumber(entry, "total_kib", `SD.data.filesystems[${index}]`), used_kib: entry.used_kib === null ? null : readNumber(entry, "used_kib", `SD.data.filesystems[${index}]`), free_kib: entry.free_kib === null ? null : readNumber(entry, "free_kib", `SD.data.filesystems[${index}]`),
    };
  });
  return {
    ok: true,
    data: {
      has_sdcard: hasSdcard,
      device,
      reports: { partitions_b64: readString(reports, "partitions_b64", "SD.data.reports"), mounts_b64: readString(reports, "mounts_b64", "SD.data.reports") },
      format: { supported: formatSupported, options, status: formatStatus, last_output_b64: readString(format, "last_output_b64", "SD.data.format") },
      filesystems,
      messages: { format_warning: readString(messages, "format_warning", "SD.data.messages"), not_present: readString(messages, "not_present", "SD.data.messages") },
      debug: { detection: debug.detection },
    },
  };
}

export function decodeSdFormatAccepted(value: unknown): SdFormatAccepted {
  const root = object(value, "SD format response");
  if (root.status !== "queued" || root.filesystem !== "fat32") throw new TypeError("SD format response is invalid");
  return { status: "queued", filesystem: "fat32" };
}

export function decodeFileList(value: unknown): FileListResponse {
  const root = object(value, "file list");
  if (!Array.isArray(root.breadcrumbs) || !Array.isArray(root.entries)) throw new TypeError("file list arrays are invalid");
  const breadcrumbs = root.breadcrumbs.map((entry, index) => {
    const item = object(entry, `file breadcrumb ${index}`);
    return { label: readString(item, "label", `file breadcrumb ${index}`), path: readString(item, "path", `file breadcrumb ${index}`) };
  });
  const entries = root.entries.map((entry, index) => {
    const item = object(entry, `file entry ${index}`);
    return {
      name: readString(item, "name", `file entry ${index}`), path: readString(item, "path", `file entry ${index}`),
      size: readString(item, "size", `file entry ${index}`), perm: readString(item, "perm", `file entry ${index}`), time: readString(item, "time", `file entry ${index}`),
      is_dir: readBoolean(item, "is_dir", `file entry ${index}`), is_link: readBoolean(item, "is_link", `file entry ${index}`),
      link_target: readString(item, "link_target", `file entry ${index}`), deletable: readBoolean(item, "deletable", `file entry ${index}`),
    };
  });
  if (root.next_cursor !== undefined && root.next_cursor !== null && (typeof root.next_cursor !== "string" || !/^[a-f0-9-]{1,64}$/.test(root.next_cursor))) throw new TypeError("file list.next_cursor is invalid");
  return { ...(root.next_cursor === undefined ? {} : { next_cursor: root.next_cursor as string | null }), ...(root.truncated === undefined ? {} : { truncated: readBoolean(root, "truncated", "file list") }), directory: readString(root, "directory", "file list"), parent: readString(root, "parent", "file list"), breadcrumbs, entries };
}

export function decodeFileText(value: unknown): FileTextResponse {
  const root = object(value, "file text");
  if (root.content_encoding !== "base64") throw new TypeError("file text encoding is invalid");
  return { file: readString(root, "file", "file text"), content: readString(root, "content", "file text"), content_encoding: "base64", size: readNumber(root, "size", "file text"), lines: readNumber(root, "lines", "file text"), writable: readBoolean(root, "writable", "file text") };
}

export function decodeFileTextWrite(value: unknown): FileTextWriteResponse {
  const root = object(value, "file text write");
  if (root.success !== true) throw new TypeError("file text write.success must be true");
  const result: FileTextWriteResponse = { success: true, size: readNumber(root, "size", "file text write"), lines: readNumber(root, "lines", "file text write") };
  if (root.file !== undefined) result.file = readString(root, "file", "file text write");
  return result;
}

export function decodeFileRemove(value: unknown): FileRemoveResponse {
  const root = object(value, "file remove");
  if (root.result !== "ok") throw new TypeError("file remove.result must be ok");
  return { result: "ok" };
}

export function decodeDiagnosticInfo(value: unknown): DiagnosticInfoResponse {
  const root = object(value, "diagnostic info");
  if (!Array.isArray(root.commands)) throw new TypeError("diagnostic info.commands must be an array");
  return {
    commands: root.commands.map((entry, index) => {
      const item = object(entry, `diagnostic command ${index}`);
      return { command: readString(item, "command", `diagnostic command ${index}`), output_base64: readString(item, "output_base64", `diagnostic command ${index}`) };
    }),
    extras_html_base64: root.extras_html_base64 === undefined ? "" : readString(root, "extras_html_base64", "diagnostic info"),
  };
}

export function decodeDiagnosticBundle(value: unknown): DiagnosticBundleResponse {
  const root = object(value, "diagnostic bundle");
  return { output_b64: readString(root, "output_b64", "diagnostic bundle") };
}

export function decodeCrontab(value: unknown): CrontabConfig {
  const root = object(value, "crontab");
  const content = readString(root, "content", "crontab");
  const maxBytes = readNumber(root, "max_bytes", "crontab");
  if (!Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 16 * 1024) throw new TypeError("crontab.max_bytes is invalid");
  if (new TextEncoder().encode(content).length > maxBytes) throw new TypeError("crontab.content exceeds max_bytes");
  return { content, max_bytes: maxBytes };
}

export function decodeMutationSuccess(value: unknown): MutationSuccess {
  const root = object(value, "mutation response");
  if (root.status !== "ok") throw new TypeError("mutation response.status must be ok");
  return { status: "ok" };
}

export function decodeSensorIdentity(value: unknown): SensorIdentity {
  const root = object(value, "sensor identity");
  return { sensor_model: readString(root, "sensor_model", "sensor identity"), soc_model: readString(root, "soc_model", "sensor identity"), soc_family: readString(root, "soc_family", "sensor identity"), file_path: readString(root, "file_path", "sensor identity"), md5: readString(root, "md5", "sensor identity") };
}

export function decodeDaynightSensors(value: unknown): DayNightSensors {
  const root = object(value, "day/night sensors");
  if (root.current !== null && root.current !== undefined && !isRecord(root.current)) throw new TypeError("day/night current sensor state is invalid");
  if (root.night_threshold_pct !== null) readNumber(root, "night_threshold_pct", "day/night sensors");
  if (root.day_threshold_pct !== null) readNumber(root, "day_threshold_pct", "day/night sensors");
  if (root.thresholds !== null && root.thresholds !== undefined && !isRecord(root.thresholds)) throw new TypeError("day/night thresholds are invalid");
  if (root.source !== undefined && root.source !== "raptor") throw new TypeError("day/night sensors source is invalid");
  return {
    ...(root.source === "raptor" ? { source: "raptor" as const } : {}),
    night_threshold_pct: root.night_threshold_pct === null ? null : root.night_threshold_pct as number,
    day_threshold_pct: root.day_threshold_pct === null ? null : root.day_threshold_pct as number,
    thresholds: root.thresholds === null || root.thresholds === undefined ? null : jsonObject(root.thresholds, "day/night sensors.thresholds"),
    current: root.current === null || root.current === undefined ? null : jsonObject(root.current, "day/night sensors.current"),
  };
}

export function decodeDaynightHistory(value: unknown): RuntimeHeartbeat[] {
  if (!Array.isArray(value)) throw new TypeError("day/night history must be an array");
  return value.map((entry, index) => {
    try { return decodeHeartbeat(entry); } catch (error) { throw new TypeError(`day/night history entry ${index} is invalid: ${error instanceof Error ? error.message : "unknown error"}`); }
  });
}

export function decodeHealth(value: unknown): HealthResponse {
  const root = object(value, "health");
  const api = object(root.control_api, "health.control_api");
  const backend = object(root.backend, "health.backend");
  const name = readString(api, "name", "health.control_api");
  const version = readNumber(api, "version", "health.control_api");
  if (name !== "Thingino Control" || version !== 1) throw new TypeError("health Control API metadata is invalid");
  if (root.status !== "ok" && root.status !== "degraded") throw new TypeError("health.status is invalid");
  return {
    control_api: { name: "Thingino Control", version: 1 },
    status: root.status,
    healthy: readBoolean(root, "healthy", "health"),
    backend: { name: readString(backend, "name", "health.backend"), available: readBoolean(backend, "available", "health.backend") },
    checks: jsonObject(root.checks, "health.checks"),
  };
}

export function decodeRaptorEncoding(value: unknown): JsonObject {
  const root = object(value, "stream encoding");
  requiredBoolean(root, "supported", "stream encoding");
  requiredBoolean(root, "available", "stream encoding");
  if (Object.keys(root).length === 2) {
    if (root.supported !== false || root.available !== false) throw new TypeError("Invalid unavailable stream encoding capability");
    return root as JsonObject;
  }
  const allowed = ["supported", "available", "rc_mode", "bitrate", "saved_rc_mode", "saved_bitrate", "matches_saved", "bitrate_min", "bitrate_max", "bitrate_step", "modes"];
  if (Object.keys(root).some((key) => !allowed.includes(key)) || allowed.some((key) => !(key in root))) throw new TypeError("Invalid stream encoding fields");
  if (root.bitrate_min !== 1_000 || root.bitrate_max !== 100_000_000 || root.bitrate_step !== 1_000) throw new TypeError("Invalid stream encoding bitrate limits");
  const expectedModes = ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"];
  if (!Array.isArray(root.modes) || root.modes.length !== expectedModes.length || root.modes.some((mode, index) => mode !== expectedModes[index])) throw new TypeError("Invalid stream encoding modes");
  const validMode = (mode: unknown): mode is string => typeof mode === "string" && expectedModes.includes(mode);
  if (root.available === true && root.supported !== true) throw new TypeError("Available stream encoding must be supported");
  if (root.available) {
    if (!validMode(root.rc_mode)) throw new TypeError("Invalid live stream encoding mode");
    numberRange(root, "bitrate", "stream encoding", 1_000, 100_000_000);
    if (!Number.isInteger(root.bitrate) || (root.bitrate as number) % 1_000 !== 0) throw new TypeError("Invalid live stream encoding bitrate");
  } else if (root.rc_mode !== null || root.bitrate !== null) throw new TypeError("Unavailable stream encoding must use null live values");
  if (root.saved_rc_mode === null || root.saved_bitrate === null) {
    if (root.saved_rc_mode !== null || root.saved_bitrate !== null) throw new TypeError("Saved stream encoding must be complete");
  } else {
    if (!validMode(root.saved_rc_mode)) throw new TypeError("Invalid saved stream encoding mode");
    numberRange(root, "saved_bitrate", "stream encoding", 1_000, 100_000_000);
    if (!Number.isInteger(root.saved_bitrate) || (root.saved_bitrate as number) % 1_000 !== 0) throw new TypeError("Invalid saved stream encoding bitrate");
  }
  requiredBoolean(root, "matches_saved", "stream encoding");
  if (root.matches_saved !== (root.available === true && root.rc_mode === root.saved_rc_mode && root.bitrate === root.saved_bitrate)) throw new TypeError("Stream encoding saved comparison disagrees");
  return root as JsonObject;
}

export function decodeRaptorRcConfig(value: unknown): JsonObject {
  const root = object(value, "stream restart rate control");
  requiredBoolean(root, "supported", "stream restart rate control");
  requiredBoolean(root, "available", "stream restart rate control");
  if (Object.keys(root).length === 2) {
    if (root.supported !== false || root.available !== false) throw new TypeError("Invalid unavailable stream restart rate control");
    return root as JsonObject;
  }
  const fields = ["supported", "available", "active_mode", "active_qp", "saved_available", "saved_mode", "saved_qp", "matches_saved", "pending_restart", "qp_min", "qp_max", "qp_default", "modes"];
  if (Object.keys(root).some(key => !fields.includes(key)) || fields.some(key => !(key in root))) throw new TypeError("Invalid stream restart rate-control fields");
  for (const key of ["saved_available", "matches_saved", "pending_restart"]) requiredBoolean(root, key, "stream restart rate control");
  if (root.qp_min !== 0 || root.qp_max !== 51 || root.qp_default !== 35) throw new TypeError("Invalid FIXQP limits");
  const modes = ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"];
  if (!Array.isArray(root.modes) || root.modes.length !== modes.length || root.modes.some((mode, index) => mode !== modes[index])) throw new TypeError("Invalid stream restart rate-control modes");
  const validMode = (candidate: unknown): candidate is string => typeof candidate === "string" && modes.includes(candidate);
  const validQp = (candidate: unknown) => typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 0 && candidate <= 51;
  if (root.available === true) {
    if (root.supported !== true || !validMode(root.active_mode) || (root.active_mode === "FIXQP" ? !validQp(root.active_qp) : root.active_qp !== null)) throw new TypeError("Invalid active stream restart rate control");
  } else if (root.active_mode !== null || root.active_qp !== null) throw new TypeError("Unavailable stream restart rate control must use null active values");
  if (root.saved_available === true) {
    if (!validMode(root.saved_mode) || (root.saved_qp !== null && !validQp(root.saved_qp)) || (root.saved_mode === "FIXQP" && root.saved_qp === null)) throw new TypeError("Invalid saved stream restart rate control");
  } else if (root.saved_mode !== null || root.saved_qp !== null) throw new TypeError("Unavailable saved stream restart rate control must use null values");
  const matches = root.available === true && root.saved_available === true && root.active_mode === root.saved_mode
    && (root.active_mode !== "FIXQP" || root.active_qp === root.saved_qp);
  const pending = root.available === true && root.saved_available === true && !matches;
  if (root.matches_saved !== matches || root.pending_restart !== pending) throw new TypeError("Stream restart rate-control comparison disagrees");
  return root as JsonObject;
}

export function decodeRaptorGopMode(value: unknown): JsonObject {
  const root = object(value, "stream GOP mode");
  requiredBoolean(root, "supported", "stream GOP mode");
  requiredBoolean(root, "available", "stream GOP mode");
  if (Object.keys(root).length === 2) {
    if (root.supported !== false || root.available !== false) throw new TypeError("Invalid unavailable stream GOP mode");
    return root as JsonObject;
  }
  const fields = ["supported", "available", "active_mode", "saved_available", "saved_mode", "matches_saved", "pending_restart", "modes"];
  if (Object.keys(root).some(key => !fields.includes(key)) || fields.some(key => !(key in root))) throw new TypeError("Invalid stream GOP mode fields");
  for (const key of ["saved_available", "matches_saved", "pending_restart"]) requiredBoolean(root, key, "stream GOP mode");
  const modes = ["DEFAULT", "PYRAMIDAL", "SMARTP"];
  if (!Array.isArray(root.modes) || root.modes.length !== modes.length || root.modes.some((mode, index) => mode !== modes[index])) throw new TypeError("Invalid stream GOP mode options");
  const valid = (candidate: unknown): candidate is string => typeof candidate === "string" && modes.includes(candidate);
  if (root.available === true) {
    if (root.supported !== true || !valid(root.active_mode)) throw new TypeError("Invalid active stream GOP mode");
  } else if (root.active_mode !== null) throw new TypeError("Unavailable stream GOP mode must use null active value");
  if (root.saved_available === true) {
    if (!valid(root.saved_mode)) throw new TypeError("Invalid saved stream GOP mode");
  } else if (root.saved_mode !== null) throw new TypeError("Unavailable saved stream GOP mode must use null value");
  const matches = root.available === true && root.saved_available === true && root.active_mode === root.saved_mode;
  const pending = root.available === true && root.saved_available === true && !matches;
  if (root.matches_saved !== matches || root.pending_restart !== pending) throw new TypeError("Stream GOP mode comparison disagrees");
  return root as JsonObject;
}

export function decodeRaptorCodec(value: unknown): JsonObject {
  const root = object(value, "stream codec");
  requiredBoolean(root, "supported", "stream codec");
  requiredBoolean(root, "available", "stream codec");
  if (Object.keys(root).length === 2) {
    if (root.supported !== false || root.available !== false) throw new TypeError("Invalid unavailable stream codec capability");
    return root as JsonObject;
  }
  const allowed = ["supported", "available", "recovery_required", "codec", "saved_codec", "matches_saved", "codecs"];
  if (Object.keys(root).some((key) => !allowed.includes(key)) || allowed.some((key) => !(key in root))) throw new TypeError("Invalid stream codec fields");
  requiredBoolean(root, "recovery_required", "stream codec");
  requiredBoolean(root, "matches_saved", "stream codec");
  const expected = root.codecs;
  if (!Array.isArray(expected) || expected.length < 1 || expected.length > 2 || expected[0] !== "H264" || (expected.length === 2 && expected[1] !== "H265")) throw new TypeError("Invalid stream codec options");
  if (root.supported !== true) throw new TypeError("Detailed stream codec must be supported");
  if (root.available === true) {
    if (root.recovery_required || !expected.includes(root.codec)) throw new TypeError("Invalid live stream codec");
  } else if (root.codec !== null) throw new TypeError("Unavailable stream codec must use null live value");
  if (root.saved_codec !== null && !expected.includes(root.saved_codec)) throw new TypeError("Invalid saved stream codec");
  if (root.matches_saved !== (root.available === true && root.codec === root.saved_codec)) throw new TypeError("Stream codec saved comparison disagrees");
  return root as JsonObject;
}

export function decodeRaptorProfile(value: unknown): JsonObject {
  const root = object(value, "stream profile");
  requiredBoolean(root, "supported", "stream profile");
  requiredBoolean(root, "available", "stream profile");
  if (Object.keys(root).length === 2) {
    if (root.supported !== false || root.available !== false) throw new TypeError("Invalid unavailable stream profile capability");
    return root as JsonObject;
  }
  const allowed = ["supported", "available", "recovery_required", "profile", "saved_profile", "matches_saved", "profiles"];
  if (Object.keys(root).some((key) => !allowed.includes(key)) || allowed.some((key) => !(key in root))) throw new TypeError("Invalid stream profile fields");
  requiredBoolean(root, "recovery_required", "stream profile");
  requiredBoolean(root, "matches_saved", "stream profile");
  if (!Array.isArray(root.profiles) || root.profiles.length !== 3 || root.profiles.some((value, index) => value !== index)) throw new TypeError("Invalid H.264 profile options");
  const valid = (candidate: unknown) => candidate === 0 || candidate === 1 || candidate === 2;
  if (root.supported !== true) throw new TypeError("Detailed stream profile must be supported");
  if (root.available === true) {
    if (root.recovery_required || !valid(root.profile)) throw new TypeError("Invalid live stream profile");
  } else if (root.profile !== null) throw new TypeError("Unavailable stream profile must use null live value");
  if (root.saved_profile !== null && !valid(root.saved_profile)) throw new TypeError("Invalid saved stream profile");
  if (root.matches_saved !== (root.available === true && root.profile === root.saved_profile)) throw new TypeError("Stream profile saved comparison disagrees");
  return root as JsonObject;
}

export function decodeRaptorStream(value: unknown, id: number): JsonObject {
  const root = object(value, `stream${id}`);
  const allowed = ["source", "persistent", "stream_id", "supported", "available", "gop", "saved_gop", "matches_saved"];
  if (Object.keys(root).some((key) => !allowed.includes(key) && !["fps_control", "encoding_control", "rc_config_control", "gop_mode_control", "codec_control", "profile_control", "geometry_control", "buffer_control", "enable_control", "audio_control"].includes(key)) || allowed.some((key) => !(key in root))) throw new TypeError("Invalid Raptor stream fields");
  if (root.fps_control !== undefined) decodeRaptorFps(root.fps_control, id);
  if (root.encoding_control !== undefined) decodeRaptorEncoding(root.encoding_control);
  if (root.rc_config_control !== undefined) decodeRaptorRcConfig(root.rc_config_control);
  if (root.gop_mode_control !== undefined) decodeRaptorGopMode(root.gop_mode_control);
  if (root.codec_control !== undefined) decodeRaptorCodec(root.codec_control);
  if (root.profile_control !== undefined) decodeRaptorProfile(root.profile_control);
  if (root.geometry_control !== undefined) decodeRaptorGeometry(root.geometry_control);
  if (root.buffer_control !== undefined) decodeRaptorBuffers(root.buffer_control);
  if (root.enable_control !== undefined) decodeRaptorStreamEnable(root.enable_control, id);
  if (root.audio_control !== undefined) decodeRaptorStreamAudio(root.audio_control);
  if (root.source !== "raptor" || root.persistent !== true || root.stream_id !== id) throw new TypeError("Invalid Raptor stream identity");
  for (const name of ["supported", "available", "matches_saved"]) requiredBoolean(root, name, "stream");
  for (const name of ["gop", "saved_gop"]) {
    if (root[name] !== null && (typeof root[name] !== "number" || !Number.isInteger(root[name]) || root[name] < 1 || root[name] > 65535)) throw new TypeError("Invalid Raptor GOP value");
  }
  if (root.available !== (root.gop !== null) || (root.available && !root.supported) ||
    root.matches_saved !== (root.gop !== null && root.gop === root.saved_gop)) throw new TypeError("Inconsistent Raptor GOP observation");
  return typed<JsonObject>(root);
}

export function decodeRaptorStreamAudio(value: unknown): JsonObject {
  const root = object(value, "stream audio");
  if (root.supported === null) {
    if (root.available !== false || root.editable !== false || Object.keys(root).length !== 3) throw new TypeError("Invalid unavailable stream audio status");
    return typed<JsonObject>(root);
  }
  const fields = ["supported", "available", "editable", "apply", "selection_required",
    "legacy_selection_pending", "configured_enabled", "saved_readback_available",
    "saved_available", "saved_enabled", "configured_matches_saved", "active_available",
    "active_enabled", "pending_restart", "rsd_active_enabled", "rmr_required",
    "rmr_active_enabled", "rsd_source_available", "rmr_source_available"];
  if (Object.keys(root).length !== fields.length || fields.some(key => !(key in root)) ||
      root.supported !== true || root.available !== true || root.apply !== "full-camera-restart") {
    throw new TypeError("Invalid stream audio contract");
  }
  for (const key of ["supported", "available", "editable", "selection_required",
    "legacy_selection_pending", "saved_readback_available", "saved_available",
    "active_available", "rsd_active_enabled", "rmr_required", "rsd_source_available"]) {
    requiredBoolean(root, key, "stream audio");
  }
  for (const key of ["configured_enabled", "saved_enabled", "configured_matches_saved",
    "active_enabled", "pending_restart", "rmr_active_enabled", "rmr_source_available"]) {
    if (root[key] !== null && typeof root[key] !== "boolean") throw new TypeError(`Invalid ${key} stream audio state`);
  }
  if ((root.selection_required && root.legacy_selection_pending) ||
      root.saved_available !== (root.saved_enabled !== null) ||
      (!root.saved_readback_available && (root.saved_available || root.editable)) ||
      root.configured_matches_saved !== (root.saved_enabled === null ? null : root.configured_enabled === root.saved_enabled) ||
      root.active_available !== (root.active_enabled !== null) ||
      (root.selection_required && (root.configured_enabled !== null || root.saved_enabled !== null ||
        root.active_enabled !== null || root.pending_restart !== null || !root.editable)) ||
      (root.legacy_selection_pending && (typeof root.configured_enabled !== "boolean" ||
        root.active_enabled !== null || root.pending_restart !== true || !root.editable)) ||
      (!root.selection_required && !root.legacy_selection_pending &&
        root.editable !== (root.saved_available && root.active_available) ||
      !root.selection_required && !root.legacy_selection_pending &&
        root.pending_restart !== (root.saved_enabled === null || root.active_enabled === null ? null : root.active_enabled !== root.saved_enabled)) ||
      (root.rmr_required
        ? typeof root.rmr_source_available !== "boolean" || typeof root.rmr_active_enabled !== "boolean"
        : root.rmr_source_available !== null || root.rmr_active_enabled !== null)) {
    throw new TypeError("Stream audio state disagrees");
  }
  return typed<JsonObject>(root);
}

export function decodeRaptorStreamEnable(value: unknown, id: number): JsonObject {
  const root = object(value, "stream enable");
  for (const key of ["available", "editable"]) requiredBoolean(root, key, "stream enable");
  if (root.supported === null) {
    if (root.available !== false || root.editable !== false || Object.keys(root).length !== 3) throw new TypeError("Invalid unavailable stream enable status");
    return typed<JsonObject>(root);
  }
  requiredBoolean(root, "supported", "stream enable");
  const fields = [
    "supported", "available", "editable", "required", "active_enabled",
    "configured_enabled", "saved_available", "saved_enabled",
    "configured_matches_saved", "pending_restart", "motion_blocks_disable",
    "recorder_blocks_disable", "recorder_state_known",
    "recorder_active_blocks_disable", "apply",
  ];
  if (root.supported !== true || root.available !== true ||
      Object.keys(root).length !== fields.length ||
      fields.some(key => !(key in root)) || root.apply !== "full-camera-restart") {
    throw new TypeError("Invalid stream enable contract");
  }
  for (const key of [
    "required", "active_enabled", "configured_enabled", "saved_available",
    "configured_matches_saved", "motion_blocks_disable",
    "recorder_blocks_disable", "recorder_state_known",
    "recorder_active_blocks_disable",
  ]) requiredBoolean(root, key, "stream enable");
  if ((id === 0 && (root.editable !== false || root.required !== true)) ||
      (id === 1 && (root.editable !== true || root.required !== false))) {
    throw new TypeError("Invalid stream enable ownership");
  }
  if (root.saved_enabled !== null && typeof root.saved_enabled !== "boolean") throw new TypeError("Invalid saved stream enable state");
  if (root.pending_restart !== null && typeof root.pending_restart !== "boolean") throw new TypeError("Invalid stream enable restart state");
  const expectedPending = root.saved_enabled === null
    ? null
    : root.active_enabled !== root.saved_enabled;
  if (root.saved_available !== (root.saved_enabled !== null) ||
      root.configured_matches_saved !== (root.saved_enabled !== null && root.configured_enabled === root.saved_enabled) ||
      root.pending_restart !== expectedPending) throw new TypeError("Stream enable state disagrees");
  if (id === 0 && (root.motion_blocks_disable || root.recorder_blocks_disable ||
      root.recorder_state_known !== true || root.recorder_active_blocks_disable)) {
    throw new TypeError("Main stream dependencies are invalid");
  }
  return typed<JsonObject>(root);
}

export function decodeRaptorBuffers(value: unknown): JsonObject {
  const root = object(value, "stream buffers");
  requiredBoolean(root, "available", "stream buffers");
  requiredBoolean(root, "editable", "stream buffers");
  if (root.supported === false || root.supported === null) {
    if (root.available !== false || root.editable !== false || Object.keys(root).length !== 3) {
      throw new TypeError("Invalid unavailable stream buffer status");
    }
    return typed<JsonObject>(root);
  }
  requiredBoolean(root, "supported", "stream buffers");
  const fields = ["supported", "available", "editable", "active_buffers", "configured_buffers", "saved_available", "saved_buffers", "active_matches_configured", "configured_matches_saved", "profile", "profile_required_buffers", "profile_admitted", "hardware_limit_known"];
  if (fields.some(key => !(key in root))
      || Object.keys(root).length !== fields.length
      || root.editable !== false
      || root.profile !== "dcs6100lhv2-a1-42m-22m-v1"
      || root.profile_required_buffers !== 1
      || root.hardware_limit_known !== false) {
    throw new TypeError("Invalid stream buffer profile");
  }
  for (const key of ["saved_available", "active_matches_configured", "configured_matches_saved", "profile_admitted"]) requiredBoolean(root, key, "stream buffers");
  const count = (key: string): number | null => {
    const candidate = root[key];
    if (candidate === null) return null;
    if (typeof candidate !== "number"
        || !Number.isInteger(candidate)
        || candidate < 1
        || candidate > 2_147_483_647) {
      throw new TypeError("Invalid stream buffer count");
    }
    return candidate;
  };
  const active = count("active_buffers");
  const configured = count("configured_buffers");
  const saved = count("saved_buffers");
  if (root.available !== (active !== null)
      || root.saved_available !== (saved !== null)
      || root.active_matches_configured !== (active !== null && active === configured)
      || root.configured_matches_saved !== (configured !== null && configured === saved)
      || root.profile_admitted !== (active === 1 && configured === 1 && saved === 1)) {
    throw new TypeError("Stream buffer status disagrees");
  }
  return typed<JsonObject>(root);
}

export function decodeRaptorGeometry(value: unknown): JsonObject {
  const root = object(value, "stream geometry");
  for (const key of ["supported", "available"]) requiredBoolean(root, key, "stream geometry");
  if (root.supported === false) {
    if (root.available !== false || Object.keys(root).length !== 2) throw new TypeError("Invalid unsupported stream geometry");
    return typed<JsonObject>(root);
  }
  const fields = ["supported", "available", "saved_available", "profile", "active_width", "active_height", "saved_width", "saved_height", "matches_saved", "pending_restart"];
  if (fields.some(key => !(key in root)) || Object.keys(root).length !== fields.length || root.profile !== "dcs6100lhv2-a1-42m-22m-v1") throw new TypeError("Invalid stream geometry contract");
  for (const key of ["saved_available", "matches_saved", "pending_restart"]) requiredBoolean(root, key, "stream geometry");
  const dimension = (key: string, nullable: boolean) => {
    const candidate = root[key];
    if (nullable && candidate === null) return null;
    const maximum = key.endsWith("height") ? 1080 : 1920;
    if (typeof candidate !== "number" || !Number.isInteger(candidate) || candidate < 32 || candidate > maximum || candidate % 2 !== 0) throw new TypeError("Invalid stream geometry dimension");
    return candidate;
  };
  const activeWidth = dimension("active_width", true), activeHeight = dimension("active_height", true);
  const savedWidth = dimension("saved_width", true), savedHeight = dimension("saved_height", true);
  if ((activeWidth === null) !== (activeHeight === null) || (savedWidth === null) !== (savedHeight === null)) throw new TypeError("Incomplete stream geometry pair");
  if (root.available !== (activeWidth !== null && activeHeight !== null) || root.saved_available !== (savedWidth !== null && savedHeight !== null)) throw new TypeError("Incomplete stream geometry observation");
  const matches = root.available && root.saved_available && activeWidth === savedWidth && activeHeight === savedHeight;
  if (root.matches_saved !== matches || root.pending_restart !== (root.available && root.saved_available && !matches)) throw new TypeError("Stream geometry state disagrees");
  return typed<JsonObject>(root);
}

export function decodeRaptorFps(value: unknown, id: number): JsonObject {
  const root = object(value, "FPS result");
  requiredBoolean(root, "supported", "FPS");
  requiredBoolean(root, "available", "FPS");
  if (root.supported === false) {
    if (root.available !== false || Object.keys(root).length !== 2) throw new TypeError("Invalid unsupported FPS state");
    return typed<JsonObject>(root);
  }
  const allowed = ["supported", "available", "status", "stream_id", "recovery_required", "persistence_pending", "live_applied", "persisted", "fps", "monitoring"];
  if (Object.keys(root).length !== allowed.length || allowed.some((key) => !(key in root)) || root.stream_id !== id || !["ok", "error"].includes(String(root.status))) throw new TypeError("Invalid FPS receipt");
  for (const key of ["recovery_required", "persistence_pending"]) requiredBoolean(root, key, "FPS");
  for (const key of ["live_applied", "persisted"]) if (root[key] !== null && typeof root[key] !== "boolean") throw new TypeError("Invalid FPS confirmation");
  if (root.fps !== null && (typeof root.fps !== "number" || !Number.isInteger(root.fps) || root.fps < 1 || root.fps > 30)) throw new TypeError("Invalid FPS value");
  const monitoring = object(root.monitoring, "FPS monitoring");
  const keys = ["related", "active", "paused", "receiving", "thread_owned"];
  if (Object.keys(monitoring).length !== keys.length) throw new TypeError("Invalid FPS monitoring");
  for (const key of keys) requiredBoolean(monitoring, key, "FPS monitoring");
  const available = !root.recovery_required && root.fps !== null && root.live_applied === true && (root.status === "ok" || root.persistence_pending === true);
  if (root.available !== available || (root.recovery_required && root.fps !== null) || (root.status === "ok" && (root.live_applied !== true || root.recovery_required || root.fps === null))) throw new TypeError("Inconsistent FPS receipt");
  return typed<JsonObject>(root);
}
