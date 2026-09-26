import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeAccess, decodeAudio, decodeImagingRuntime, decodeRaptorStream, decodeRaptorFps } from "../../api/decode";
import { type ConfigFormSpec } from "../../app/forms";
import { bool, text, number, select, prudyntLoad } from "./common";
import { validateRaptorStreamPaths } from "./access";

export const audio: ConfigFormSpec = {
  eyebrow: "Settings / audio",
  title: "Audio",
  description: "Microphone processing, speaker level and audio stream state.",
  endpoint: routes.prudynt.domain("audio"),
  saveEndpoint: routes.prudynt.command,
  load: async (client) => {
    const value = decodeAudio(await client.json<unknown>(routes.prudynt.domain("audio")));
    const raptor = value.source === "raptor";
    const ranges: JsonObject = {};
    for (const name of ["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain"]) {
      const gain = name.endsWith("gain");
      const range = raptor ? (value.levels as JsonObject)[name]! : { supported: true, available: true, min: gain ? 0 : -30, max: name === "mic_alc_gain" ? 7 : gain ? 31 : 120 };
      ranges[name] = raptor && name.startsWith("mic_") && value.mic_muted !== false
        ? { ...(range as JsonObject), available: false }
        : range;
    }
    ranges.mic_noise_suppression = { supported: !raptor || value.effects_built === true, available: !raptor || value.processing_available === true, min: 0, max: raptor ? 4 : 3 };
    const codecs = raptor ? Object.entries(value.codecs_built as JsonObject).filter(([, built]) => built === true).map(([name]) => name) : ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"];
    return { audio: value, audio_full_controls: !raptor, audio_input_control: true,
      audio_output_control: !raptor || value.spk_enabled !== null,
      audio_codec_control: !raptor || value.mic_enabled === true,
      audio_processing_control: !raptor || value.processing_available === true,
      audio_codecs: codecs, audio_ranges: ranges };

  },
  save: async (client, value, loaded) => {
    const update = buildAudioUpdate(value, loaded);
    if (!Object.keys(update.audio as JsonObject).length) throw new Error("No editable audio settings are available.");
    await client.postJson<unknown>(routes.prudynt.command, update);
  },
  fields: [
    bool("audio.mic_enabled", "Enable microphone input"),
    select("audio.mic_format", "Microphone codec", ["AAC", "G711A", "G711U", "G726", "OPUS", "PCM"]),
    { ...number("audio.mic_vol", "Microphone volume", -30, 120), description: "Clear microphone mute before changing this level." },
    { ...number("audio.mic_gain", "Microphone gain", 0, 31), description: "Clear microphone mute before changing this level." },
    { ...number("audio.mic_alc_gain", "Microphone ALC gain", 0, 7), description: "Clear microphone mute before changing this level." },
    number("audio.mic_noise_suppression", "Noise suppression", 0, 3),
    bool("audio.mic_agc_enabled", "Enable automatic gain control"),
    number("audio.mic_agc_compression_gain_db", "AGC compression gain dB", 0, 90),
    number("audio.mic_agc_target_level_dbfs", "AGC target level", 0, 31),
    bool("audio.mic_high_pass_filter", "Enable microphone high-pass filter"),
    bool("audio.mic_is_digital", "Use digital microphone input", "Raptor mode: fixed off because the DCS-6100LHV2 A1 profile uses its analog microphone input."),
    bool("audio.force_stereo", "Force stereo output", "Raptor mode: fixed off because RAD publishes one-channel microphone audio."),
    { ...number("audio.buffer_warn_frames", "Buffer warning threshold (frames)", 10, 1_000), description: "Raptor mode: this Prudynt queue policy has no equivalent RAD setting." },
    { ...number("audio.buffer_cap_frames", "Buffer capacity (frames)", 10, 1_000), description: "Raptor mode: fixed native ring capacity replaces this Prudynt setting." },
    bool("audio.tap_enabled", "Write microphone tap for local diagnostics", "Raptor mode: RAD has no microphone tap owner."),
    { ...text("audio.tap_path", "Microphone tap path"), description: "Raptor mode: RAD does not implement microphone taps.", enabledWhen: { path: "audio.tap_enabled", equals: true } },
    bool("audio.spk_enabled", "Enable speaker output"),
    number("audio.spk_vol", "Speaker volume", -30, 120),
    number("audio.spk_gain", "Speaker gain", 0, 31),
  ],
};

const audioLevelNames = ["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain"];
for (const field of audio.fields) {
  const name = field.path.slice("audio.".length);
  if (audioLevelNames.includes(name) || name === "mic_noise_suppression") field.rangeFrom = `audio_ranges.${name}`;
  else if (name === "mic_enabled") field.enabledWhen = { path: "audio_input_control", equals: true };
  else if (name === "mic_format") {
    field.optionsFrom = "audio_codecs";
    field.enabledWhen = { path: "audio_codec_control", equals: true };
  } else if (["mic_agc_enabled", "mic_agc_target_level_dbfs", "mic_agc_compression_gain_db", "mic_high_pass_filter"].includes(name)) field.enabledWhen = { path: "audio_processing_control", equals: true };
  else if (name === "spk_enabled") field.enabledWhen = { path: "audio_output_control", equals: true };
  else if (!field.enabledWhen) field.enabledWhen = { path: "audio_full_controls", equals: true };
}

export function buildAudioUpdate(value: JsonObject, loaded: JsonObject): JsonObject {
  const current = loaded.audio as JsonObject;
  const requested = value.audio as JsonObject;
  if (current.source !== "raptor") return { audio: requested };
  const update: JsonObject = {};
  const levels = current.levels as JsonObject;
  for (const name of audioLevelNames) {
    const metadata = levels[name] as JsonObject | undefined;
    if (requested.mic_enabled === false && name.startsWith("mic_")) continue;
    if (requested.spk_enabled === false && name.startsWith("spk_")) continue;
    if (name.startsWith("mic_") && (current.mic_muted === true || current.mic_muted === null)) continue;
    if (metadata?.supported === true && metadata.available === true && typeof requested[name] === "number") update[name] = requested[name];
  }
  if (typeof requested.mic_enabled === "boolean") update.mic_enabled = requested.mic_enabled;
  if (current.spk_enabled !== null && typeof requested.spk_enabled === "boolean") update.spk_enabled = requested.spk_enabled;
  if (current.mic_enabled === true && requested.mic_enabled !== false) {
    if (typeof requested.mic_format === "string" && (current.codecs_built as JsonObject)?.[requested.mic_format] === true) update.mic_format = requested.mic_format;
    if (current.processing_available === true) {
      for (const name of ["mic_noise_suppression", "mic_agc_enabled", "mic_agc_target_level_dbfs", "mic_agc_compression_gain_db", "mic_high_pass_filter"]) {
        if (requested[name] !== undefined) update[name] = requested[name];
      }
    }
  }
  // Include selected observed levels even when unchanged: a prior live apply
  // may have succeeded while persistence failed, so an explicit retry must save.
  return { audio: update };
}

export const imaging: ConfigFormSpec = {
  eyebrow: "Streamer / image",
  title: "Image quality",
  description: "Shared sensor and ISP settings applied before both stream encoders. Stream-specific resolution, frame rate and encoding remain on Video streams.",
  endpoint: routes.imaging,
  load: async (client) => {
    const imagingRuntime = decodeImagingRuntime(await client.json<unknown>(routes.imaging));
    const image: JsonObject = {};
    for (const [runtimeName, configName] of Object.entries(imagingFieldMap)) {
      const field = imagingRuntime.fields[runtimeName];
      if (!field) imagingRuntime.fields[runtimeName] = { supported: false };
      else if (field.value !== undefined) image[configName] =
        ["hflip", "vflip"].includes(runtimeName) && typeof field.value === "number"
          ? field.value === 1 : field.value;
    }
    const noiseField = imagingRuntime.fields.noise_reduction;
    let noiseReductionStatus = "Unavailable on this ISP.";
    if (noiseField?.verification === "sdk-setter-config") {
      noiseReductionStatus = noiseField.available === true
        ? "The ISP setter accepted this configured value; this SDK cannot read the live value back."
        : "The configured value has not been acknowledged by the ISP setter in this runtime.";
    } else if (noiseField?.verification === "sdk-readback") {
      noiseReductionStatus = noiseField.available === true
        ? "Live value read back from the ISP."
        : "The ISP live value is temporarily unavailable.";
    }
    const whiteBalance = imagingRuntime.white_balance;
    const antiFlicker = imagingRuntime.fields.anti_flicker;
    let antiFlickerStatus = "Anti-flicker control is unavailable on this ISP.";
    if (antiFlicker?.supported === true) {
      if (antiFlicker.available !== true) antiFlickerStatus = "The anti-flicker SDK readback is temporarily unavailable.";
      else if (typeof antiFlicker.saved_value !== "number") antiFlickerStatus = "The live anti-flicker mode is available, but the saved setting could not be read.";
      else if (antiFlicker.matches_saved !== true) antiFlickerStatus = "The live anti-flicker mode differs from the saved setting.";
      else antiFlickerStatus = "The live anti-flicker mode matches the saved setting.";
    }
    let whiteBalanceStatus = "This runtime does not expose White Balance control.";
    const whiteBalanceConfigured = typeof whiteBalance?.configured_mode === "number" &&
      typeof whiteBalance.configured_rgain === "number" && typeof whiteBalance.configured_bgain === "number";
    const whiteBalanceSaved = typeof whiteBalance?.saved_mode === "number" &&
      typeof whiteBalance.saved_rgain === "number" && typeof whiteBalance.saved_bgain === "number";
    if (whiteBalance?.supported === true) {
      const selectedMode = whiteBalance.available ? whiteBalance.mode : whiteBalance.configured_mode;
      if (typeof selectedMode === "number") image.core_wb_mode = selectedMode;
      if (typeof whiteBalance.configured_rgain === "number") image.wb_rgain = whiteBalance.configured_rgain;
      if (typeof whiteBalance.configured_bgain === "number") image.wb_bgain = whiteBalance.configured_bgain;
      if (!whiteBalanceConfigured) {
        whiteBalanceStatus = "The configured white-balance values are unavailable; White Balance editing is disabled.";
      } else if (!whiteBalance.available) {
        whiteBalanceStatus = "The white-balance SDK readback is temporarily unavailable.";
      } else if (!whiteBalanceSaved) {
        whiteBalanceStatus = "Live White Balance is available, but saved settings could not be read.";
      } else if (!whiteBalance.matches_saved) {
        whiteBalanceStatus = "Live white-balance settings differ from saved settings.";
      } else if (whiteBalance.mode === 1) {
        whiteBalanceStatus = "Manual mode and gains match saved settings.";
      } else {
        whiteBalanceStatus = "Preset mode matches saved settings; gain fields are retained for Manual mode.";
      }
    }
    return {
      image,
      imaging_runtime: imagingRuntime,
      image_persistent: imagingRuntime.persistent === true,
      image_noise_reduction_status: noiseReductionStatus,
      image_antiflicker_status: antiFlickerStatus,
      image_white_balance_control: whiteBalance?.supported === true && whiteBalance.available === true && whiteBalanceConfigured,
      image_white_balance_status: whiteBalanceStatus,
    };
  },
  saveLabel: (loaded) => loaded.image_persistent === false ? "Apply live settings" : "Save settings",
  successMessage: (loaded) => loaded.image_persistent === false ? "Live settings applied. They are not saved across a restart." : "Settings saved.",
  loadTransform: (value) => {
    const view = structuredClone(value);
    const image = view.image as JsonObject;
    const runtime = ((view.imaging_runtime as JsonObject).fields ?? {}) as JsonObject;
    for (const [runtimeName, configName] of Object.entries(imagingFieldMap)) {
      const field = runtime[runtimeName];
      if (typeof field === "object" && field !== null && !Array.isArray(field) && typeof field.value === "number") {
        image[configName] = ["hflip", "vflip"].includes(runtimeName)
          ? field.value === 1 : field.value;
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
    { ...number("image.backlight_compensation", "Backlight compensation"), rangeFrom: "imaging_runtime.fields.backlight", description: "Backlight compensation and highlight tone cannot both be enabled. Set highlight tone to 0 before enabling backlight compensation." },
    { ...number("image.drc_strength", "Dynamic range strength"), rangeFrom: "imaging_runtime.fields.wide_dynamic_range" },
    { ...number("image.highlight_depress", "Highlight tone"), rangeFrom: "imaging_runtime.fields.tone", description: "Set backlight compensation to 0 before enabling highlight tone." },
    { ...number("image.defog_strength", "Defog strength"), rangeFrom: "imaging_runtime.fields.defog" },
    { ...number("image.sinter_strength", "Noise reduction"), rangeFrom: "imaging_runtime.fields.noise_reduction" },
    { ...text("image_noise_reduction_status", "Noise reduction status"), readOnly: true },
    { path: "image.anti_flicker", label: "Anti-flicker", type: "select", valueType: "number",
      options: [{ label: "Off", value: "0" }, { label: "50 Hz", value: "1" }, { label: "60 Hz", value: "2" }] },
    { ...text("image_antiflicker_status", "Anti-flicker status"), readOnly: true },
    number("image.hue", "Hue", 0, 255),
    number("image.dpc_strength", "Defective-pixel correction", 0, 255),
    {
      path: "image.core_wb_mode", label: "White-balance mode", type: "select", valueType: "number",
      options: [
        ["Automatic", 0], ["Manual", 1], ["Daylight", 2], ["Cloudy", 3], ["Incandescent", 4],
        ["Fluorescent", 5], ["Twilight", 6], ["Shade", 7], ["Warm fluorescent", 8], ["Custom", 9],
      ].map(([label, value]) => ({ label: String(label), value: String(value) })),
    }, number("image.wb_bgain", "White-balance blue gain", 0, 1024),
    number("image.wb_rgain", "White-balance red gain", 0, 1024), number("image.ae_compensation", "Exposure compensation", 0, 255),
    { ...text("image_white_balance_status", "White-balance status"), readOnly: true },
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
  hue: "hue",
  dpc_strength: "dpc_strength",
  exposure_compensation: "ae_compensation",
  hflip: "hflip",
  vflip: "vflip",
  anti_flicker: "anti_flicker",
};

const raptorExtraImagingFields = ["hue", "dpc_strength", "exposure_compensation", "hflip", "vflip", "anti_flicker"];

for (const field of imaging.fields) {
  const runtimeName = Object.entries(imagingFieldMap).find(([, config]) => field.path === `image.${config}`)?.[0];
  if (runtimeName && raptorExtraImagingFields.includes(runtimeName)) {
    const runtimePath = `imaging_runtime.fields.${runtimeName}`;
    field.enabledWhen = read => read(`${runtimePath}.supported`) === true && read(`${runtimePath}.available`) === true;
  } else if (!field.rangeFrom) {
    const runtimePath = Object.entries(imagingFieldMap).find(([, config]) => field.path === `image.${config}`)?.[0];
    if (runtimePath) field.enabledWhen = { path: `imaging_runtime.fields.${runtimePath}.available`, equals: true };
  }
}

const whiteBalanceModeField = imaging.fields.find(field => field.path === "image.core_wb_mode")!;
whiteBalanceModeField.enabledWhen = { path: "image_white_balance_control", equals: true };
for (const path of ["image.wb_rgain", "image.wb_bgain"]) {
  const field = imaging.fields.find(candidate => candidate.path === path)!;
  field.enabledWhen = read => read("image_white_balance_control") === true && read("image.core_wb_mode") === 1;
}


export function buildImagingRequests(value: JsonObject, loaded: JsonObject): { live: JsonObject } {
  const image = value.image;
  const metadata = ((loaded.imaging_runtime as JsonObject).fields ?? {}) as JsonObject;
  if (typeof image !== "object" || image === null || Array.isArray(image)) throw new TypeError("Image configuration is incomplete.");
  const live: JsonObject = {};
  for (const [runtimeName, configName] of Object.entries(imagingFieldMap)) {
    const field = metadata[runtimeName];
    const formValue = image[configName];
    const next = ["hflip", "vflip"].includes(runtimeName) && typeof formValue === "boolean"
      ? Number(formValue) : formValue;
    if (typeof field === "object" && field !== null && !Array.isArray(field) && field.supported === true && field.available !== false && typeof next === "number") {
      live[runtimeName] = next;
    }
  }
  if (typeof live.backlight === "number" && live.backlight > 0 && typeof live.tone === "number" && live.tone > 0) {
    throw new TypeError("Backlight compensation and highlight tone cannot both be enabled. Set one to 0.");
  }
  const whiteBalance = (loaded.imaging_runtime as { white_balance?: Record<string, unknown> }).white_balance;
  if (loaded.image_white_balance_control === true && whiteBalance?.supported === true && whiteBalance.available === true) {
    const mode = image.core_wb_mode;
    const rgain = image.wb_rgain;
    const bgain = image.wb_bgain;
    if (typeof mode !== "number" || !Number.isInteger(mode) || mode < 0 || mode > 9 ||
        typeof rgain !== "number" || !Number.isInteger(rgain) || rgain < 0 || rgain > 1024 ||
        typeof bgain !== "number" || !Number.isInteger(bgain) || bgain < 0 || bgain > 1024) {
      throw new TypeError("White-balance settings are incomplete.");
    }
    if (mode !== 1 && (rgain !== whiteBalance.configured_rgain || bgain !== whiteBalance.configured_bgain)) {
      throw new TypeError("White-balance gains can change only while Manual mode is selected.");
    }
    live.white_balance = { mode, rgain, bgain };
  }
  return { live };
}

async function saveImaging(client: ApiClient, value: JsonObject, loaded: JsonObject): Promise<void> {
  const requests = buildImagingRequests(value, loaded);
  if (!Object.keys(requests.live).length) throw new TypeError("No image controls are available. Reload before saving.");
  const response = decodeImagingRuntime(await client.postJson<unknown>(routes.imaging, requests.live));
  if (loaded.image_persistent === true && response.persistent !== true) {
    throw new TypeError("Image settings were applied live, but saving was not confirmed. Reload before retrying.");
  }
  if (Object.hasOwn(requests.live, "white_balance") && response.white_balance?.matches_saved !== true) {
    throw new TypeError("White-balance saving was not confirmed. Reload before retrying.");
  }
}

export const streams: ConfigFormSpec = {
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
    if (typeof stream !== "object" || stream === null || Array.isArray(stream)) return { label: "Unavailable", state: "inactive" };
    const fps = stream.fps_control as JsonObject | undefined;
    return { label: fps?.recovery_required ? "Recovery required" : fps?.persistence_pending ? "Persistence pending" : fps?.available ? "FPS available" : stream.available ? "GOP available" : "Unavailable", state: fps?.available || stream.available === true ? "enabled" : "inactive" };
  },
  cardSummary: (card, value) => {
    const stream = value[card];
    if (typeof stream !== "object" || stream === null || Array.isArray(stream)) return "Unavailable";
    const fps = stream.fps_control as JsonObject | undefined;
    if (fps?.recovery_required) return "FPS recovery requires a full daemon restart.";
    if (fps?.persistence_pending) return `${fps.fps} fps configuration applied. Retry FPS persistence with this value.`;
    const rc = stream.rc_config_control as JsonObject | undefined;
    const gopMode = stream.gop_mode_control as JsonObject | undefined;
    if (rc?.pending_restart === true) return `Saved ${rc.saved_mode}${rc.saved_mode === "FIXQP" ? ` at QP ${rc.saved_qp}` : ""}; active ${rc.active_mode}${rc.active_mode === "FIXQP" ? ` at QP ${rc.active_qp}` : ""}. Restart the full camera stack to apply.`;
    if (gopMode?.pending_restart === true) return `Saved ${gopMode.saved_mode}; active ${gopMode.active_mode}. Restart the full camera stack to apply.`;
    const frameRate = fps?.available ? `${fps.fps} fps configuration · ${fps.persisted === true ? "Saved" : "Saving unconfirmed"}. ` : "FPS unavailable. ";
    return frameRate + (stream.available === true ? `GOP ${stream.gop} · ${stream.matches_saved === true ? "Matches saved value" : "Save to persist this value"}` : "No confirmed encoder GOP readback");
  },
  load: async (client) => {
    const result = await prudyntLoad(client, [0, 1].map((id) => [`stream${id}`, (value: unknown) => decodeRaptorStream(value, id)]));
    result.stream_full_controls = false;
    for (const name of ["stream0", "stream1"]) {
      const stream = result[name] as JsonObject;
      const fps = stream.fps_control as JsonObject | undefined;
      const encoding = stream.encoding_control as JsonObject | undefined;
      const rc = stream.rc_config_control as JsonObject | undefined;
      const gopMode = stream.gop_mode_control as JsonObject | undefined;
      const codec = stream.codec_control as JsonObject | undefined;
      const profile = stream.profile_control as JsonObject | undefined;
      const geometry = stream.geometry_control as JsonObject | undefined;
      const buffers = stream.buffer_control as JsonObject | undefined;
      const enable = stream.enable_control as JsonObject | undefined;
      const audioControl = stream.audio_control as JsonObject | undefined;
      const codecRecovery = codec?.recovery_required === true;
      result[`${name}_gop_control`] = stream.available === true && !fps?.recovery_required && !fps?.persistence_pending && !codecRecovery;
      result[`${name}_gop_mode_control`] = gopMode?.available === true && !codecRecovery;
      result[`${name}_fps_control`] = fps?.available === true && !codecRecovery;
      result[`${name}_encoding_control`] = encoding?.available === true && !codecRecovery;
      result[`${name}_rc_config_control`] = rc?.available === true && !codecRecovery;
      result[`${name}_mode_control`] = (encoding?.available === true || rc?.available === true) && !codecRecovery;
      result[`${name}_codec_control`] = codec?.available === true;
      result[`${name}_profile_control`] = profile?.available === true && !codecRecovery;
      result[`${name}_geometry_control`] = geometry?.available === true && geometry.saved_available === true && !codecRecovery;
      const disableBlocked = enable?.saved_enabled === true && (
        enable.motion_blocks_disable === true ||
        enable.recorder_blocks_disable === true ||
        enable.recorder_state_known !== true ||
        enable.recorder_active_blocks_disable === true
      );
      result[`${name}_enable_control`] = enable?.available === true && enable.saved_available === true && enable.editable === true && !disableBlocked;
      result[`${name}_audio_control`] = audioControl?.available === true && audioControl.editable === true &&
        audioControl.saved_readback_available === true &&
        (audioControl.selection_required === true ||
          audioControl.legacy_selection_pending === true ||
          (audioControl.saved_available === true && audioControl.active_available === true));
      result[`${name}_codecs`] = codec?.codecs ?? [];
      result[`${name}_encoding_modes`] = rc?.modes ?? encoding?.modes ?? [];
      result[`${name}_gop_modes`] = gopMode?.modes ?? [];
      result[`${name}_profiles`] = profile?.profiles ?? [];
      stream.enabled = enable?.configured_enabled ?? enable?.saved_enabled ?? enable?.active_enabled ?? null;
      stream.active_enabled = enable?.active_enabled ?? null;
      stream.enable_pending_restart = enable?.pending_restart ?? null;
      stream.audio_enabled = audioControl?.selection_required === true
        ? null
        : audioControl?.configured_matches_saved === false
          ? audioControl.configured_enabled ?? null
          : audioControl?.saved_enabled ?? audioControl?.configured_enabled ?? null;
      stream.audio_status = audioControl?.supported !== true
        ? "RTSP and recorder audio policy owners are unavailable. Reload before changing this setting."
        : audioControl.selection_required === true
          ? "Legacy RTSP includes audio while managed recordings exclude it. Choose an explicit shared policy; unrelated saves leave this unchanged and a full camera stack restart applies the choice."
        : audioControl.legacy_selection_pending === true
          ? `Saved audio ${audioControl.saved_enabled ? "included" : "excluded"}; legacy RTSP and recorder owners remain split until a full camera stack restart. Retry or change the selection explicitly.`
        : audioControl.saved_available !== true
          ? "Saved audio policy readback is unavailable. Save is disabled until it can be checked."
        : audioControl.active_available !== true
          ? "RTSP and recorder audio owners disagree. Save is disabled until both owners can be read."
          : audioControl.pending_restart === true
            ? `Saved audio ${audioControl.saved_enabled ? "included" : "excluded"}; active RTSP and recorder policy ${audioControl.active_enabled ? "includes" : "excludes"} audio. Restart the full camera stack to apply.`
            : `RTSP and recorder audio policy ${audioControl.active_enabled ? "includes" : "excludes"} audio. Microphone availability, mute and Privacy remain separate.`;
      stream.enable_status = enable?.supported !== true
        ? "Stream enable state could not be read; support is unknown."
          : enable.required === true
            ? "The main stream is required by the current Raptor software pipeline; main-stream disable remains unimplemented."
          : enable.saved_available !== true
            ? "Saved substream enable state could not be read. Reload before changing the substream."
          : enable.saved_enabled === true && enable.recorder_state_known !== true
            ? "Recorder 1 state is unavailable. Reload before changing the substream."
          : enable.saved_enabled === true && enable.recorder_active_blocks_disable === true
            ? "Stop the active or transitioning stream1 recorder before disabling the substream."
          : enable.motion_blocks_disable === true
            ? "Motion uses FrameSource channel 1. Disable Motion before disabling the substream."
            : enable.recorder_blocks_disable === true
              ? "Disable stream1 recorder autostart before disabling the substream."
              : enable.pending_restart === true
                ? `Saved substream ${enable.saved_enabled ? "enabled" : "disabled"}; active substream ${enable.active_enabled ? "enabled" : "disabled"}. Restart the full camera stack to apply.`
                : `Active and saved substream are ${enable.active_enabled ? "enabled" : "disabled"}.`;
      stream.fps = fps?.fps ?? null;
      stream.mode = rc?.saved_available === true ? rc.saved_mode ?? null : encoding?.rc_mode ?? rc?.active_mode ?? null;
      stream.bitrate = encoding?.bitrate ?? null;
      stream.qp_init = rc?.saved_qp ?? rc?.active_qp ?? rc?.qp_default ?? null;
      stream.active_mode = rc?.active_mode ?? null;
      stream.active_qp = rc?.active_qp ?? null;
      stream.rc_config_pending_restart = rc?.pending_restart ?? false;
      stream.rc_config_status = rc?.saved_available !== true
        ? "No explicit saved rate-control mode was read. Selecting FIXQP writes a complete startup mode and QP."
        : rc.available !== true
          ? `Saved ${rc.saved_mode}${rc.saved_mode === "FIXQP" ? ` at QP ${rc.saved_qp}` : ""}; active rate control is unavailable.`
          : rc.pending_restart === true
            ? `Saved ${rc.saved_mode}${rc.saved_mode === "FIXQP" ? ` at QP ${rc.saved_qp}` : ""}; active ${rc.active_mode}${rc.active_mode === "FIXQP" ? ` at QP ${rc.active_qp}` : ""}. Restart the full camera stack to apply.`
            : `Active and saved rate control are ${rc.active_mode}${rc.active_mode === "FIXQP" ? ` at QP ${rc.active_qp}` : ""}.`;
      stream.gop_mode = gopMode?.saved_available === true ? gopMode.saved_mode ?? null : gopMode?.active_mode ?? null;
      stream.active_gop_mode = gopMode?.active_mode ?? null;
      stream.gop_mode_pending_restart = gopMode?.pending_restart ?? false;
      stream.gop_mode_status = gopMode?.saved_available !== true
        ? "Saved GOP mode could not be read. Reload before editing."
        : gopMode.available !== true
          ? `Saved GOP mode is ${gopMode.saved_mode}; active mode is unavailable.`
          : gopMode.pending_restart === true
            ? `Saved ${gopMode.saved_mode}; active ${gopMode.active_mode}. Restart the full camera stack to apply.`
            : `Active and saved GOP mode are ${gopMode.active_mode}.`;
      stream.format = codec?.codec ?? null;
      stream.profile = profile?.profile ?? null;
      stream.width = geometry?.saved_width ?? geometry?.active_width ?? null;
      stream.height = geometry?.saved_height ?? geometry?.active_height ?? null;
      stream.active_width = geometry?.active_width ?? null;
      stream.active_height = geometry?.active_height ?? null;
      stream.geometry_pending_restart = geometry?.pending_restart ?? false;
      stream.geometry_status = geometry?.saved_available !== true
        ? "Saved geometry could not be read. Reload before editing."
        : geometry.available !== true
          ? `Active geometry is unavailable; saved ${geometry.saved_width} × ${geometry.saved_height}. Reload before editing.`
        : geometry.pending_restart === true
          ? `Saved ${geometry.saved_width} × ${geometry.saved_height}; active ${geometry.active_width} × ${geometry.active_height}. Restart the full camera stack to apply.`
          : `Active and saved geometry are ${geometry.active_width} × ${geometry.active_height}.`;
      stream.buffers = buffers?.saved_buffers ?? buffers?.configured_buffers ?? buffers?.active_buffers ?? null;
      if (buffers?.supported === false) {
        stream.buffer_status = "The active native backend does not expose FrameSource buffer readback.";
      } else if (buffers?.supported !== true) {
        stream.buffer_status = "FrameSource buffer readback failed; support is unknown.";
      } else if (buffers.profile_admitted === true) {
        stream.buffer_status = "Active, configured and saved FrameSource buffers are 1. The current 42/22 profile fixes this value; larger counts await measured admission.";
      } else {
        const complete = buffers.active_buffers !== null
          && buffers.configured_buffers !== null && buffers.saved_buffers !== null;
        const state = complete ? "outside the current fixed profile" : "not fully observed";
        stream.buffer_status = `Buffer state is ${state}: active ${buffers.active_buffers ?? "unavailable"}, configured ${buffers.configured_buffers ?? "unavailable"}, saved ${buffers.saved_buffers ?? "unavailable"}.`;
      }
      if (fps?.persistence_pending === true && fps.available === true) {
        result.fps_retry_stream = name;
        result.fps_retry_value = fps.fps!;
      }
    }
    await loadStreamPaths(client, result);
    return result;
  },
  save: async (client, value, loaded) => {
    const body = buildStreamUpdate(value, loaded);
    if (!Object.keys(body).length) throw new Error("No editable stream settings are available.");
    if (loaded.stream_full_controls === false) {
      const invalid = streams.validate?.(value, loaded);
      if (invalid) throw new TypeError(invalid);
      const operations = partitionRaptorStreamUpdate(body);
      if (Object.keys(operations.enable).length) {
        const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.enable);
        if (reply.status !== "accepted" || reply.persistent !== true || typeof reply.pending_restart !== "boolean") throw new TypeError("Substream enable save was not confirmed");
        const stream = loaded.stream1 as JsonObject;
        const enabled = (operations.enable.stream1 as JsonObject).enabled as boolean;
        const control = stream.enable_control as JsonObject;
        stream.enabled = enabled;
        stream.enable_pending_restart = reply.pending_restart;
        stream.enable_status = reply.pending_restart
          ? `Saved substream ${enabled ? "enabled" : "disabled"}; active substream ${control.active_enabled ? "enabled" : "disabled"}. Restart the full camera stack to apply.`
          : `Active and saved substream are ${enabled ? "enabled" : "disabled"}.`;
        control.saved_available = true;
        control.saved_enabled = enabled;
        control.configured_enabled = enabled;
        control.configured_matches_saved = true;
        control.pending_restart = reply.pending_restart;
      }
      if (Object.keys(operations.audio).length) {
        const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.audio);
        if (reply.status !== "accepted" || reply.persistent !== true || typeof reply.pending_restart !== "boolean") throw new TypeError("Stream audio save was not confirmed");
        for (const [name, request] of Object.entries(operations.audio)) {
          const enabled = (request as JsonObject).audio_enabled as boolean;
          const stream = loaded[name] as JsonObject;
          const control = stream.audio_control as JsonObject;
          stream.audio_enabled = enabled;
          control.saved_available = true;
          control.saved_enabled = enabled;
          control.configured_enabled = enabled;
          control.configured_matches_saved = true;
          control.pending_restart = control.active_available !== true || control.active_enabled !== enabled;
        }
      }
      if (typeof loaded.fps_retry_stream === "string") {
        const retryIndex = operations.fps.findIndex(([name]) => name === loaded.fps_retry_stream);
        if (retryIndex > 0) operations.fps.unshift(...operations.fps.splice(retryIndex, 1));
      }
      for (const [name, fields] of operations.fps) {
        let receipt: JsonObject;
        try {
          const response = await client.postJson<JsonObject>(routes.prudynt.command, { [name]: { fps: fields.fps! } });
          receipt = decodeRaptorFps(response.fps_result, Number(name.slice(-1)));
          if (receipt.supported !== true) throw new Error("FPS backend is unavailable. Reload before retrying.");
          if (response.status !== (receipt.status === "ok" ? "accepted" : "error")) throw new TypeError("FPS response status disagrees");
          if (receipt.status === "ok" && (response.persistent !== true || receipt.persisted !== true || receipt.persistence_pending || receipt.fps !== fields.fps)) throw new TypeError("FPS save was not confirmed");
        } catch (error) {
          delete loaded.fps_retry_stream;
          delete loaded.fps_retry_value;
          loaded[`${name}_fps_control`] = false;
          loaded[`${name}_gop_control`] = false;
          (loaded[name] as JsonObject).fps_control = { supported: false, available: false };
          throw new Error(`FPS result is unknown. Your draft is kept. Reload before an explicit retry. ${error instanceof Error ? error.message : ""}`);
        }
        (loaded[name] as JsonObject).fps_control = receipt;
        if (receipt.status !== "ok") {
          if (receipt.persistence_pending === true && receipt.live_applied === true && receipt.fps === fields.fps && !receipt.recovery_required) {
            loaded.fps_retry_stream = name;
            loaded.fps_retry_value = receipt.fps!;
            throw new Error("FPS configuration applied, but saving is unconfirmed. Keep this value and choose Retry FPS persistence and save changes. Motion remains paused if it was paused for this change.");
          }
          loaded[`${name}_gop_control`] = false;
          loaded[`${name}_fps_control`] = false;
          delete loaded.fps_retry_stream;
          delete loaded.fps_retry_value;
          if (receipt.recovery_required) throw new Error("FPS recovery is required. Your draft is kept. A full daemon restart is required before further FPS changes.");
          if (receipt.live_applied === true && receipt.persisted === true) throw new Error("FPS configuration was applied and saved, but motion did not resume. Your draft is kept. Check Motion before another explicit change.");
          throw new Error("FPS was not applied. Your draft is kept. Reload the observed state before an explicit retry.");
        }
        (loaded[name] as JsonObject).fps = receipt.fps!;
        loaded[`${name}_gop_control`] = (loaded[name] as JsonObject).available === true;
        delete loaded.fps_retry_stream;
        delete loaded.fps_retry_value;
      }
      if (Object.keys(operations.codec).length) {
        try {
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.codec);
          if (reply.status !== "accepted" || reply.persistent !== true) throw new TypeError("Codec save was not confirmed");
          for (const [name, update] of Object.entries(operations.codec)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            stream.format = fields.format!;
            stream.codec_control = { ...(stream.codec_control as JsonObject), codec: fields.format!, saved_codec: fields.format!, matches_saved: true };
            if (fields.format === "H265") {
              stream.profile = null;
              loaded[`${name}_profile_control`] = false;
            }
          }
        } catch (error) {
          for (const name of Object.keys(operations.codec)) {
            for (const control of ["codec", "profile", "fps", "encoding", "gop"]) loaded[`${name}_${control}_control`] = false;
          }
          throw new Error(`Stream codec result is unknown. Your draft is kept. Reload before an explicit retry. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.profile).length) {
        try {
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.profile);
          if (reply.status !== "accepted" || reply.persistent !== true) throw new TypeError("Profile save was not confirmed");
          for (const [name, update] of Object.entries(operations.profile)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            stream.profile = fields.profile!;
            stream.profile_control = { ...(stream.profile_control as JsonObject), profile: fields.profile!, saved_profile: fields.profile!, matches_saved: true };
          }
        } catch (error) {
          for (const name of Object.keys(operations.profile)) {
            for (const control of ["codec", "profile", "fps", "encoding", "gop"]) loaded[`${name}_${control}_control`] = false;
          }
          throw new Error(`Stream profile result is unknown. Your draft is kept. Reload before an explicit retry. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.encoding).length) {
        try {
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.encoding);
          if (reply.status !== "accepted" || reply.persistent !== true) throw new TypeError("Encoding save was not confirmed");
          for (const [name, update] of Object.entries(operations.encoding)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            stream.mode = fields.mode!;
            stream.bitrate = fields.bitrate!;
            stream.encoding_control = { ...(stream.encoding_control as JsonObject), rc_mode: fields.mode!, bitrate: fields.bitrate!,
              saved_rc_mode: fields.mode!, saved_bitrate: fields.bitrate!, matches_saved: true };
            const rc = stream.rc_config_control as JsonObject | undefined;
            if (rc?.supported === true) {
              rc.active_mode = fields.mode!;
              rc.active_qp = null;
              rc.saved_available = true;
              rc.saved_mode = fields.mode!;
              rc.matches_saved = true;
              rc.pending_restart = false;
              stream.active_mode = fields.mode!;
              stream.active_qp = null;
              stream.rc_config_pending_restart = false;
              stream.rc_config_status = `Active and saved rate control are ${fields.mode}.`;
            }
          }
        } catch (error) {
          for (const name of Object.keys(operations.encoding)) loaded[`${name}_encoding_control`] = false;
          throw new Error(`Stream encoding result is unknown. Your draft is kept. Reload before an explicit retry. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.rcConfig).length) {
        try {
          const expectedPending = Object.entries(operations.rcConfig).some(([name, update]) => {
            const rc = (loaded[name] as JsonObject).rc_config_control as JsonObject;
            const fields = update as JsonObject;
            return rc.active_mode !== fields.mode || (fields.mode === "FIXQP" && rc.active_qp !== fields.qp_init);
          });
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.rcConfig);
          if (reply.status !== "accepted" || reply.persistent !== true || reply.pending_restart !== expectedPending) throw new TypeError("FIXQP save was not confirmed");
          for (const [name, update] of Object.entries(operations.rcConfig)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            const rc = stream.rc_config_control as JsonObject;
            stream.mode = fields.mode!;
            stream.qp_init = fields.qp_init!;
            rc.saved_available = true;
            rc.saved_mode = fields.mode!;
            rc.saved_qp = fields.qp_init!;
            rc.matches_saved = rc.active_mode === fields.mode && (fields.mode !== "FIXQP" || rc.active_qp === fields.qp_init);
            rc.pending_restart = !rc.matches_saved;
            stream.rc_config_pending_restart = rc.pending_restart;
            stream.rc_config_status = rc.pending_restart
              ? `Saved ${fields.mode}${fields.mode === "FIXQP" ? ` at QP ${fields.qp_init}` : ""}; active ${rc.active_mode}${rc.active_mode === "FIXQP" ? ` at QP ${rc.active_qp}` : ""}. Restart the full camera stack to apply.`
              : `Active and saved rate control are ${fields.mode}${fields.mode === "FIXQP" ? ` at QP ${fields.qp_init}` : ""}.`;
          }
        } catch (error) {
          for (const name of Object.keys(operations.rcConfig)) {
            loaded[`${name}_rc_config_control`] = false;
            loaded[`${name}_mode_control`] = false;
            loaded[`${name}_encoding_control`] = false;
          }
          throw new Error(`FIXQP save is unconfirmed. Your draft is kept. Reload or explicitly retry. A full camera stack restart is required after a confirmed save. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.gopMode).length) {
        try {
          const expectedPending = Object.entries(operations.gopMode).some(([name, update]) =>
            ((loaded[name] as JsonObject).gop_mode_control as JsonObject).active_mode !== (update as JsonObject).gop_mode);
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.gopMode);
          if (reply.status !== "accepted" || reply.persistent !== true || reply.pending_restart !== expectedPending) throw new TypeError("GOP mode save was not confirmed");
          for (const [name, update] of Object.entries(operations.gopMode)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            const control = stream.gop_mode_control as JsonObject;
            stream.gop_mode = fields.gop_mode!;
            control.saved_available = true;
            control.saved_mode = fields.gop_mode!;
            control.matches_saved = control.active_mode === fields.gop_mode;
            control.pending_restart = !control.matches_saved;
            stream.gop_mode_pending_restart = control.pending_restart;
            stream.gop_mode_status = control.pending_restart
              ? `Saved ${fields.gop_mode}; active ${control.active_mode}. Restart the full camera stack to apply.`
              : `Active and saved GOP mode are ${fields.gop_mode}.`;
          }
        } catch (error) {
          for (const name of Object.keys(operations.gopMode)) {
            loaded[`${name}_gop_mode_control`] = false;
            loaded[`${name}_gop_control`] = false;
          }
          throw new Error(`GOP mode save is unconfirmed. Your draft is kept. Reload or explicitly retry. A full camera stack restart is required after a confirmed save. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.gop).length) {
        try {
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.gop);
          if (reply.status !== "accepted" || reply.persistent !== true) throw new TypeError("GOP save was not confirmed");
          for (const [name, update] of Object.entries(operations.gop)) {
            const stream = loaded[name] as JsonObject;
            const fields = update as JsonObject;
            stream.gop = fields.gop!;
            stream.saved_gop = fields.gop!;
            stream.matches_saved = true;
          }
        } catch (error) {
          for (const name of Object.keys(operations.gop)) loaded[`${name}_gop_control`] = false;
          throw new Error(`Stream GOP result is unknown. Your draft is kept. Reload before an explicit retry. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.geometry).length) {
        try {
          const expectedPending = ["stream0", "stream1"].some(name => {
            const stream = loaded[name] as JsonObject;
            const geometry = stream.geometry_control as JsonObject;
            const requested = operations.geometry[name] as JsonObject | undefined;
            return geometry.active_width !== (requested?.width ?? geometry.saved_width)
              || geometry.active_height !== (requested?.height ?? geometry.saved_height);
          });
          const reply = await client.postJson<JsonObject>(routes.prudynt.command, operations.geometry);
          if (reply.status !== "accepted" || reply.persistent !== true || reply.pending_restart !== expectedPending) throw new TypeError("Geometry save was not confirmed");
          for (const name of ["stream0", "stream1"]) {
            const fields = operations.geometry[name] as JsonObject | undefined;
            if (!fields) continue;
            const stream = loaded[name] as JsonObject;
            stream.width = fields.width!;
            stream.height = fields.height!;
            const geometry = stream.geometry_control as JsonObject;
            geometry.saved_width = fields.width!;
            geometry.saved_height = fields.height!;
            geometry.saved_available = true;
            geometry.matches_saved = geometry.active_width === fields.width && geometry.active_height === fields.height;
            geometry.pending_restart = !geometry.matches_saved;
            stream.geometry_pending_restart = geometry.pending_restart;
            stream.geometry_status = geometry.pending_restart
              ? `Saved ${fields.width} × ${fields.height}; active ${geometry.active_width} × ${geometry.active_height}. Restart the full camera stack to apply.`
              : `Active and saved geometry are ${fields.width} × ${fields.height}.`;
          }
        } catch (error) {
          for (const name of Object.keys(operations.geometry)) loaded[`${name}_geometry_control`] = false;
          throw new Error(`Stream geometry save is unconfirmed. Your draft is kept. Reload or explicitly retry. A full camera stack restart is required after a confirmed save. ${error instanceof Error ? error.message : ""}`);
        }
      }
      if (Object.keys(operations.access).length) {
        try {
          const reply = await client.postJson<JsonObject>(routes.config.access, operations.access);
          if (reply.status !== "accepted" || reply.persistent !== true) {
            throw new TypeError("RTSP path save was not confirmed");
          }
          const observed = decodeAccess(await client.json<unknown>(routes.config.access));
          for (const [field, expected] of Object.entries(operations.access)) {
            if (observed.source !== "raptor" || observed.auth_enabled !== true
                || observed[field] !== expected || observed[`saved_${field}`] !== expected) {
              throw new TypeError("RTSP path readback did not match the saved change");
            }
          }
          for (const [field, expected] of Object.entries(operations.access)) {
            const stream = loaded[`stream${field.slice(-1)}`] as JsonObject;
            stream.rtsp_endpoint = expected;
            stream.saved_rtsp_endpoint = expected;
          }
        } catch (error) {
          throw new Error(`RTSP path save is unconfirmed. Your draft is kept. Reload or explicitly retry saving. ${error instanceof Error ? error.message : ""}`);
        }
      }
      return;
    }
    await client.postJson<JsonObject>(routes.prudynt.command, body);
  },
  successMessage: (loaded) => loaded.stream_full_controls === false
    ? ["stream0", "stream1"].some(name => ((loaded[name] as JsonObject)?.geometry_control as JsonObject)?.pending_restart === true || ((loaded[name] as JsonObject)?.rc_config_control as JsonObject)?.pending_restart === true || ((loaded[name] as JsonObject)?.gop_mode_control as JsonObject)?.pending_restart === true || ((loaded[name] as JsonObject)?.enable_control as JsonObject)?.pending_restart === true || ((loaded[name] as JsonObject)?.audio_control as JsonObject)?.pending_restart === true)
      ? "Settings saved with checked readback. Pending stream configuration remains inactive until you restart the full camera stack."
      : "Stream settings applied and saved with checked readback."
    : "Settings saved.",
  saveLabel: (loaded) => loaded.fps_retry_stream ? "Retry FPS persistence and save changes" : "Save settings",
  fields: [
    { ...bool("stream0.enabled", "Enable stream", "An enabled stream requires 1–30 fps and at least one buffer."), card: "stream0", section: "Codec and resolution" },
    { ...text("stream0.enable_status", "Enable status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream0.width", "Width", 1, 16_384), card: "stream0" },
    { ...number("stream0.height", "Height", 1, 16_384), card: "stream0" },
    { ...number("stream0.active_width", "Active width", 1, 16_384), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream0.active_height", "Active height", 1, 16_384), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream0.geometry_status", "Geometry status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...select("stream0.format", "Codec", ["H264", "H265"]), card: "stream0" },
    { ...number("stream0.fps", "Frames per second", 0, 30), description: "Use 1–30 while enabled; 0 is reserved for a disabled stream.", card: "stream0", section: "Frame rate and rate control" },
    { ...select("stream0.mode", "Bitrate mode", ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"]), card: "stream0" },
    { ...number("stream0.bitrate", "Bitrate", 1, 100_000_000), card: "stream0" },
    { ...number("stream0.qp_init", "FIXQP initial QP", 0, 51), card: "stream0", visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream0.rc_config_status", "Rate-control status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream0.gop", "GOP", 1, 65_535), card: "stream0", section: "GOP, profile and buffers" },
    { ...select("stream0.gop_mode", "GOP mode", ["DEFAULT", "PYRAMIDAL", "SMARTP"]), card: "stream0", visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream0.gop_mode_status", "GOP mode status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream0.max_gop", "Maximum GOP", 1, 65_535), card: "stream0" },
    { ...select("stream0.profile", "Profile", [0, 1, 2], "number"), card: "stream0" },
    { ...number("stream0.buffers", "Buffers", -1, 65_535), description: "Use 1–65535 while enabled; -1 is reserved for a disabled stream.", card: "stream0" },
    { ...text("stream0.buffer_status", "Buffer status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream0.rtsp_endpoint", "RTSP path"), card: "stream0", section: "RTSP and audio" },
    { ...text("stream0.rtsp_status", "RTSP path status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...bool("stream0.audio_enabled", "Include audio"), card: "stream0" },
    { ...text("stream0.audio_status", "Audio inclusion status"), card: "stream0", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...bool("stream1.enabled", "Enable stream", "An enabled stream requires 1–30 fps and at least one buffer."), card: "stream1", section: "Codec and resolution" },
    { ...text("stream1.enable_status", "Enable status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream1.width", "Width", 1, 16_384), card: "stream1" },
    { ...number("stream1.height", "Height", 1, 16_384), card: "stream1" },
    { ...number("stream1.active_width", "Active width", 1, 16_384), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream1.active_height", "Active height", 1, 16_384), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream1.geometry_status", "Geometry status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...select("stream1.format", "Codec", ["H264", "H265"]), card: "stream1" },
    { ...number("stream1.fps", "Frames per second", 0, 30), description: "Use 1–30 while enabled; 0 is reserved for a disabled stream.", card: "stream1", section: "Frame rate and rate control" },
    { ...select("stream1.mode", "Bitrate mode", ["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"]), card: "stream1" },
    { ...number("stream1.bitrate", "Bitrate", 1, 100_000_000), card: "stream1" },
    { ...number("stream1.qp_init", "FIXQP initial QP", 0, 51), card: "stream1", visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream1.rc_config_status", "Rate-control status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream1.gop", "GOP", 1, 65_535), card: "stream1", section: "GOP, profile and buffers" },
    { ...select("stream1.gop_mode", "GOP mode", ["DEFAULT", "PYRAMIDAL", "SMARTP"]), card: "stream1", visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream1.gop_mode_status", "GOP mode status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...number("stream1.max_gop", "Maximum GOP", 1, 65_535), card: "stream1" },
    { ...select("stream1.profile", "Profile", [0, 1, 2], "number"), card: "stream1" },
    { ...number("stream1.buffers", "Buffers", -1, 65_535), description: "Use 1–65535 while enabled; -1 is reserved for a disabled stream.", card: "stream1" },
    { ...text("stream1.buffer_status", "Buffer status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...text("stream1.rtsp_endpoint", "RTSP path"), card: "stream1", section: "RTSP and audio" },
    { ...text("stream1.rtsp_status", "RTSP path status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
    { ...bool("stream1.audio_enabled", "Include audio"), card: "stream1" },
    { ...text("stream1.audio_status", "Audio inclusion status"), card: "stream1", readOnly: true, visibleWhen: { path: "stream_full_controls", equals: false } },
  ],
  validate: (value, loaded) => {
    if (loaded.stream_full_controls === false) {
      const body = buildStreamUpdate(value, loaded);
      if (!Object.keys(body).length) return "No editable stream settings are available.";
      if (Object.values(body).some(stream => (stream as JsonObject).rtsp_endpoint !== undefined)) {
        const path = (name: string) =>
          ((body[name] ?? {}) as JsonObject).rtsp_endpoint ?? (loaded[name] as JsonObject)?.rtsp_endpoint;
        const invalid = validateRaptorStreamPaths(path("stream0"), path("stream1"));
        if (invalid) return invalid;
      }
      for (const [name, stream] of Object.entries(body)) {
        const { enabled, gop, gop_mode, fps, mode, bitrate, qp_init, format, profile, width, height } = stream as JsonObject;
        if (enabled !== undefined && (name !== "stream1" || typeof enabled !== "boolean")) return "Only the substream enable state is editable.";
        if ((width === undefined) !== (height === undefined)) return `${name} width and height must be saved together.`;
        if (width !== undefined) {
          const admitted = name === "stream0" ? [[1920, 1080], [1280, 720]] : [[640, 360], [480, 270], [320, 180]];
          if (!admitted.some(pair => pair[0] === width && pair[1] === height)) return `${name} resolution is outside the admitted DCS-6100LHV2 A1 profile.`;
        }
        if (gop !== undefined && (typeof gop !== "number" || !Number.isInteger(gop) || gop < 1 || gop > 65535)) return "GOP must be an integer from 1 to 65535.";
        if (gop_mode !== undefined && !["DEFAULT", "PYRAMIDAL", "SMARTP"].includes(String(gop_mode))) return "GOP mode is unavailable.";
        if (fps !== undefined && (typeof fps !== "number" || !Number.isInteger(fps) || fps < 1 || fps > 30)) return "FPS must be an integer from 1 to 30.";
        if (mode !== undefined && !["CBR", "VBR", "FIXQP", "CAPPED_VBR", "CAPPED_QUALITY"].includes(String(mode))) return "Bitrate mode is unavailable.";
        if (qp_init !== undefined && (typeof qp_init !== "number" || !Number.isInteger(qp_init) || qp_init < 0 || qp_init > 51)) return "FIXQP initial QP must be an integer from 0 to 51.";
        if (bitrate !== undefined && (typeof bitrate !== "number" || !Number.isInteger(bitrate) || bitrate < 1_000 || bitrate > 100_000_000 || bitrate % 1_000 !== 0)) return "Bitrate must be an integer from 1000 to 100000000 in steps of 1000.";
        if (mode === "FIXQP" && qp_init === undefined) return "FIXQP mode and initial QP must be saved together.";
        if (mode !== undefined && mode !== "FIXQP" && bitrate === undefined && qp_init === undefined) return "Bitrate mode must be saved with bitrate or the preserved FIXQP QP.";
        if (mode === undefined && (bitrate !== undefined || qp_init !== undefined)) return "Rate-control mode must be saved with bitrate or FIXQP QP.";
        if (bitrate !== undefined && qp_init !== undefined) return "Bitrate and FIXQP QP cannot be saved together.";
        if (format !== undefined && !["H264", "H265"].includes(String(format))) return "Codec is unavailable.";
        if (profile !== undefined && (!Number.isInteger(profile) || ![0, 1, 2].includes(profile as number))) return "H.264 profile must be Baseline (0), Main (1) or High (2).";
        if (loaded.fps_retry_stream === name && fps !== loaded.fps_retry_value) return "Retry persistence with the same FPS value before changing it.";
      }
      const desired = (name: string, key: "width" | "height") => ((body[name] as JsonObject | undefined)?.[key] ?? (value[name] as JsonObject)?.[key]) as number;
      if (desired("stream1", "width") * 3 > desired("stream0", "width") || desired("stream1", "height") * 3 > desired("stream0", "height")) return "The admitted profile requires the substream to be no more than one third of the main-stream dimensions.";
      return undefined;
    }
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


export function buildStreamUpdate(value: JsonObject, loaded: JsonObject): JsonObject {
  const body: JsonObject = {};
  for (const name of ["stream0", "stream1"]) {
    const stream = value[name] as JsonObject | undefined;
    if (loaded.stream_full_controls !== false) {
      if (stream) body[name] = stream;
    } else {
      const previous = loaded[name] as JsonObject | undefined;
      const update: JsonObject = {};
      const enable = previous?.enable_control as JsonObject | undefined;
      const needsEnableReconciliation = enable?.saved_available === true &&
        enable.configured_matches_saved === false;
      const enableBaseline = needsEnableReconciliation
        ? undefined
        : enable?.saved_available === true
          ? enable.saved_enabled
          : enable?.configured_enabled;
      if (loaded[`${name}_enable_control`] === true && stream?.enabled !== enableBaseline) {
        update.enabled = stream?.enabled ?? null;
      }
      const geometry = previous?.geometry_control as JsonObject | undefined;
      if (loaded[`${name}_geometry_control`] === true && (stream?.width !== geometry?.saved_width || stream?.height !== geometry?.saved_height)) {
        update.width = stream?.width ?? null;
        update.height = stream?.height ?? null;
      }
      if (loaded[`${name}_gop_control`] === true && (stream?.gop !== previous?.gop || previous?.matches_saved !== true)) update.gop = stream?.gop ?? null;
      const gopMode = previous?.gop_mode_control as JsonObject | undefined;
      if (loaded[`${name}_gop_mode_control`] === true &&
          (gopMode?.saved_available !== true || stream?.gop_mode !== gopMode.saved_mode))
        update.gop_mode = stream?.gop_mode ?? null;
      if (loaded[`${name}_fps_control`] === true && (stream?.fps !== previous?.fps || (previous?.fps_control as JsonObject)?.persisted !== true)) update.fps = stream?.fps ?? null;
      const encoding = previous?.encoding_control as JsonObject | undefined;
      const rc = previous?.rc_config_control as JsonObject | undefined;
      const selectedMode = stream?.mode;
      if (loaded[`${name}_rc_config_control`] === true && (selectedMode === "FIXQP" || rc?.active_mode === "FIXQP")) {
        if (selectedMode !== rc?.saved_mode || (selectedMode === "FIXQP" && stream?.qp_init !== rc?.saved_qp)) {
          update.mode = selectedMode ?? null;
          update.qp_init = stream?.qp_init ?? rc?.qp_default ?? null;
        }
      } else if (loaded[`${name}_encoding_control`] === true && (selectedMode !== encoding?.saved_rc_mode || stream?.bitrate !== encoding?.saved_bitrate || encoding?.matches_saved !== true)) {
        update.mode = selectedMode ?? null;
        update.bitrate = stream?.bitrate ?? null;
      }
      const codec = previous?.codec_control as JsonObject | undefined;
      if (loaded[`${name}_codec_control`] === true && (stream?.format !== previous?.format || codec?.matches_saved !== true)) update.format = stream?.format ?? null;
      const profile = previous?.profile_control as JsonObject | undefined;
      if (stream?.format !== "H265" && loaded[`${name}_profile_control`] === true && (stream?.profile !== previous?.profile || profile?.matches_saved !== true)) update.profile = stream?.profile ?? null;
      const audio = previous?.audio_control as JsonObject | undefined;
      if (loaded[`${name}_audio_control`] === true &&
          ((audio?.selection_required === true && typeof stream?.audio_enabled === "boolean") ||
            (audio?.selection_required !== true &&
              (stream?.audio_enabled !== audio?.saved_enabled || audio?.configured_matches_saved !== true)))) {
        update.audio_enabled = stream?.audio_enabled ?? null;
      }
      if (loaded[`${name}_rtsp_control`] === true
          && (stream?.rtsp_endpoint !== previous?.rtsp_endpoint
            || previous?.saved_rtsp_endpoint !== previous?.rtsp_endpoint)) {
        update.rtsp_endpoint = stream?.rtsp_endpoint ?? null;
      }
      if (Object.keys(update).length) body[name] = update;
    }
  }
  return body;
}


function partitionRaptorStreamUpdate(body: JsonObject): { enable: JsonObject; audio: JsonObject; fps: [string, JsonObject][]; codec: JsonObject; profile: JsonObject; encoding: JsonObject; rcConfig: JsonObject; gopMode: JsonObject; gop: JsonObject; geometry: JsonObject; access: JsonObject } {
  const enable: JsonObject = {};
  const audio: JsonObject = {};
  const fps: [string, JsonObject][] = [];
  const codec: JsonObject = {};
  const profile: JsonObject = {};
  const encoding: JsonObject = {};
  const rcConfig: JsonObject = {};
  const gopMode: JsonObject = {};
  const gop: JsonObject = {};
  const geometry: JsonObject = {};
  const access: JsonObject = {};
  for (const [name, update] of Object.entries(body)) {
    const fields = update as JsonObject;
    if (fields.enabled !== undefined) enable[name] = { enabled: fields.enabled };
    if (fields.audio_enabled !== undefined) audio[name] = { audio_enabled: fields.audio_enabled };
    if (fields.fps !== undefined) fps.push([name, { fps: fields.fps }]);
    if (fields.format !== undefined) codec[name] = { format: fields.format };
    if (fields.profile !== undefined) profile[name] = { profile: fields.profile };
    if (fields.qp_init !== undefined) rcConfig[name] = { mode: fields.mode!, qp_init: fields.qp_init };
    else if (fields.mode !== undefined || fields.bitrate !== undefined) encoding[name] = { mode: fields.mode!, bitrate: fields.bitrate! };
    if (fields.gop !== undefined) gop[name] = { gop: fields.gop };
    if (fields.gop_mode !== undefined) gopMode[name] = { gop_mode: fields.gop_mode };
    if (fields.width !== undefined || fields.height !== undefined) geometry[name] = { width: fields.width!, height: fields.height! };
    if (fields.rtsp_endpoint !== undefined) access[`rtsp_ch${name.slice(-1)}`] = fields.rtsp_endpoint;
  }
  return { enable, audio, fps, codec, profile, encoding, rcConfig, gopMode, gop, geometry, access };
}

for (const field of streams.fields) {
  const [name, setting] = field.path.split(".");
  field.enabledWhen = { path: ["gop", "gop_mode", "fps", "profile"].includes(setting!) ? `${name}_${setting}_control` : ["width", "height"].includes(setting!) ? `${name}_geometry_control` : setting === "enabled" ? `${name}_enable_control` : setting === "audio_enabled" ? `${name}_audio_control` : setting === "mode" ? `${name}_mode_control` : setting === "bitrate" ? `${name}_encoding_control` : setting === "qp_init" ? `${name}_rc_config_control` : setting === "format" ? `${name}_codec_control` : "stream_full_controls", equals: true };
  if (setting === "audio_enabled") field.description = "Includes the microphone track in this stream's RTSP sessions and managed recordings after a full camera stack restart. This does not unmute or enable the microphone.";
  if (setting === "mode") field.optionsFrom = `${name}_encoding_modes`;
  if (setting === "format") field.optionsFrom = `${name}_codecs`;
  if (setting === "profile") field.optionsFrom = `${name}_profiles`;
  if (setting === "format") field.description = "Changing codec restarts this stream and briefly interrupts video. H.265 availability confirms encoder support only; WebRTC, browser and client playback may be unavailable.";
  if (setting === "gop") field.description = "Application range 1–65535. Raptor saves only after matching encoder and file readback.";
  if (setting === "gop_mode") {
    field.optionsFrom = `${name}_gop_modes`;
    field.description = "T31 GOP structure. SMARTP is a GOP mode, not SMART rate control. Changes apply after a full camera stack restart; bitstream structure still needs camera verification.";
  }
  if (setting === "bitrate") field.description = "Raptor supports 1000–100000000 bit/s in steps of 1000 with matching live and file readback.";
  if (setting === "qp_init") {
    field.enabledWhen = read => read(`${name}_rc_config_control`) === true && read(`${name}.mode`) === "FIXQP";
    field.description = "FIXQP only, 0–51. This is saved for startup and becomes active only after a full camera stack restart.";
  }
  if (setting === "bitrate") field.enabledWhen = read => read(`${name}_encoding_control`) === true && read(`${name}.mode`) !== "FIXQP" && read(`${name}.active_mode`) !== "FIXQP";
  if (setting === "profile") field.description = "H.264 only: 0 Baseline, 1 Main, 2 High. Changing profile restarts this stream and briefly interrupts video.";
  if (setting === "width" || setting === "height") field.description = name === "stream0"
    ? "Saved startup geometry. Admitted pairs are 1920 × 1080 and 1280 × 720. A full camera stack restart applies the change."
    : "Saved startup geometry. Admitted pairs are 640 × 360, 480 × 270 and 320 × 180. A full camera stack restart applies the change.";
  if (setting === "rtsp_endpoint") {
    field.enabledWhen = read => read("stream_full_controls") === true || read(`${name}_rtsp_control`) === true;
    field.description = "Changing an RTSP path disconnects existing RTSP viewers. Viewer credentials and port stay unchanged; edit those under Media access.";
  }
}

async function loadStreamPaths(client: ApiClient, result: JsonObject): Promise<void> {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), 1500);
  for (const name of ["stream0", "stream1"]) {
    result[`${name}_rtsp_control`] = false;
    Object.assign(result[name] as JsonObject, {
      rtsp_endpoint: null,
      saved_rtsp_endpoint: null,
      rtsp_status: "RTSP path observation is unavailable. Encoder settings remain independent.",
    });
  }
  try {
    const access = decodeAccess(await client.json<unknown>(routes.config.access, {
      signal: controller.signal,
    }));
    if (access.source !== "raptor") throw new TypeError("RTSP and video backends disagree");
    const checked = typeof access.rtsp_paths_match_saved === "boolean";
    for (const id of [0, 1]) {
      const name = `stream${id}`;
      const stream = result[name] as JsonObject;
      stream.rtsp_endpoint = access[`rtsp_ch${id}`]!;
      stream.saved_rtsp_endpoint = access[`saved_rtsp_ch${id}`] ?? null;
      result[`${name}_rtsp_control`] = access.auth_enabled === true && checked;
      stream.rtsp_status = access.auth_enabled !== true
        ? "Set viewer credentials under Media access before changing paths."
        : !checked ? "Saved-path observation is unavailable. Reload after updating the backend."
        : stream.rtsp_endpoint === stream.saved_rtsp_endpoint ? "Matches saved path."
        : "Live path differs from saved or unreadable settings. Save to persist this path.";
    }
  } catch (_) {
    // An unavailable RTSP owner must not disable the independent video controls.
  } finally {
    globalThis.clearTimeout(timer);
  }
}
