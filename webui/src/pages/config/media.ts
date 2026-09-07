import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeAudio, decodeImage, decodeImagingRuntime, decodeStream } from "../../api/decode";
import { type ConfigFormSpec } from "../../app/forms";
import { bool, text, number, select, prudyntLoad } from "./common";

export const audio: ConfigFormSpec = {
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

export const imaging: ConfigFormSpec = {
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
