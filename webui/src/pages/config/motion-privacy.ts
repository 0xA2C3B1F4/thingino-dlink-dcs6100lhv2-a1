import type { JsonObject } from "../../api/contracts";
import type { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeHomeAssistant, decodeHomeAssistantRuntime, decodeMotion, decodePrivacy } from "../../api/decode";
import { element } from "../../app/dom";
import { type ConfigFormSpec } from "../../app/forms";
import { bool, text, number, secret, select, section } from "./common";

export const motionPrivacy: ConfigFormSpec = {
  eyebrow: "Streamer / detection",
  title: "Motion and privacy",
  description: "Detection and privacy state for the camera streams. Quick toggles also appear in Preview.",
  endpoint: routes.prudynt.domain("motion"),
  saveEndpoint: routes.prudynt.command,
  refreshStreamerPreview: true,
  load: async (client) => {
    const raw = await client.json<unknown>(routes.prudynt.domain("motion"));
    if (typeof raw === "object" && raw !== null && "source" in raw && raw.source === "raptor") {
      const motion = decodeRaptorMotionConfig(raw);
      const roi = decodeRaptorMotionRoi(motion.roi);
      const single = roi?.single as JsonObject | null;
      for (const key of motionRoiKeys) motion[key] = single?.[key] ?? null;
      motion.frame_width = roi?.frame_width ?? null;
      motion.frame_height = roi?.frame_height ?? null;
      return {
        motion,
        privacy: {},
        ...motionRoiView(roi),
        motion_saved_enabled: motion.saved_enabled,
        motion_matches_saved: motion.matches_saved,
        motion_sensitivity: motion.sensitivity,
        motion_sensitivity_control: motion.sensitivity_available === true,
        motion_saved_sensitivity: motion.saved_sensitivity,
        motion_sensitivity_matches_saved: motion.sensitivity_matches_saved,
        motion_skip_frame_count: motion.skip_frame_count,
        motion_skip_frame_count_control: motion.skip_frame_count_available === true,
        motion_saved_skip_frame_count: motion.saved_skip_frame_count,
        motion_skip_frame_count_matches_saved: motion.skip_frame_count_matches_saved,
        motion_lifecycle_control: motion.lifecycle_available === true,
        motion_debounce_time: motion.debounce_time,
        motion_cooldown_time: motion.cooldown_time,
        motion_init_time: motion.init_time,
        motion_min_time: motion.min_time,
        motion_post_time: motion.post_time,
        motion_lifecycle_matches_saved: motion.lifecycle_matches_saved,
        motion_event_actions_control: motion.event_actions_available === true,
        motion_video_length: motion.video_length,
        motion_saved_video_length: motion.saved_video_length,
        motion_send2storage: motion.send2storage,
        motion_saved_send2storage: motion.saved_send2storage,
        motion_playonspeaker: motion.playonspeaker,
        motion_saved_playonspeaker: motion.saved_playonspeaker,
        motion_speaker_repeats: motion.speaker_repeats,
        motion_saved_speaker_repeats: motion.saved_speaker_repeats,
        motion_speaker_action_control: motion.event_actions_available === true && motion.speaker_action_available === true,
        motion_event_actions_match_saved: motion.event_actions_match_saved,
        motion_event_actions_note: motion.event_actions_available
          ? `Storage owner ${motion.storage_action_available ? "is available" : "is unavailable"}. Speaker action control ${motion.speaker_action_available ? `is reachable; last playback state ${motion.speaker_playback}` : "is unreachable"}. Worker ${motion.event_action_worker_running ? "is running" : "is stopped"}; owned channel ${motion.event_storage_owned_channel ?? "none"}; starts ${motion.event_storage_starts}, stops ${motion.event_storage_stops}, failures ${motion.event_storage_failures}, dropped ${motion.event_action_queue_dropped}; speaker requests ${motion.event_speaker_requests}, failures ${motion.event_speaker_failures}, rate-limited ${motion.event_speaker_rate_limited}.`
          : "Event storage settings need the checked Raptor action owner.",
        motion_monitor_stream: 1,
        motion_frame_width: roi?.frame_width ?? null,
        motion_frame_height: roi?.frame_height ?? null,
        motion_limits_note: "Detection always uses stream 1. The 500 ms owner read deadline is fixed safety policy.",
        motion_timing_note: "Debounce counts positive detector samples. Other lifecycle values are seconds.",
        motion_mqtt_enabled: null,
        motion_mqtt_status: "Checking Home Assistant MQTT status...",
        motion_destinations_note: "FTP uploads one bounded stream 1 JPEG over verified explicit FTPS. Email, Gotify, ntfy, Telegram, and webhook send fixed text only. Each destination has separate settings below.",
        motion_full_controls: false,
        motion_control: motion.supported === true && motion.persistent === true,
      };
    }
    const motion = decodeMotion(raw);
    const privacy = decodePrivacy(await client.json<unknown>(routes.prudynt.domain("privacy")));
    return { motion, privacy, motion_full_controls: true, motion_control: true, motion_roi_control: true };
  },
  save: saveMotionPrivacy,
  successMessage: (loaded) => (loaded.motion as JsonObject).source === "raptor" && (loaded.motion as JsonObject).enabled === false
    ? "Motion is saved as off. Region edits were not saved. Turn motion on to save a region."
    : "Settings saved.",
  fields: [
    ...section("Detection", [
      bool("motion.enabled", "Enable motion detection"),
      { ...bool("motion_saved_enabled", "Saved motion setting"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_matches_saved", "Runtime matches saved setting"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion.sensitivity", "Motion sensitivity", 1, 8), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion_sensitivity", "Motion sensitivity (0–4)", 0, 4), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_sensitivity_control", equals: true } },
      { ...number("motion_saved_sensitivity", "Saved motion sensitivity", 0, 4), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_sensitivity_matches_saved", "Sensitivity matches saved setting"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...select("motion.monitor_stream", "Monitor stream", [0, 1], "number"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.playonspeaker", "Play event sound on speaker"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.frame_width", "Detection frame width", 0, 16_384), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.frame_height", "Detection frame height", 0, 16_384), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.ivs_polling_timeout", "Detector polling timeout (ms)", 100, 10_000), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.motor_settle_ms", "Motor settle time (ms)", 0, 10_000), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion_monitor_stream", "Detection stream", 1, 1), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion_frame_width", "Detection frame width", 0, 16_384), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion_frame_height", "Detection frame height", 0, 16_384), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_limits_note", "Fixed runtime limits"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
    ]),
    ...section("Timing and capture", [
      { ...number("motion.cooldown_time", "Cooldown time", 1, 60), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.debounce_time", "Debounce time", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.init_time", "Initialization time", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.min_time", "Minimum event time", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.post_time", "Post-event time", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion_debounce_time", "Debounce samples", 0, 65_535), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_lifecycle_control", equals: true } },
      { ...number("motion_cooldown_time", "Cooldown time (seconds)", 0, 60), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_lifecycle_control", equals: true } },
      { ...number("motion_init_time", "Initialization time (seconds)", 0, 65_535), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_lifecycle_control", equals: true } },
      { ...number("motion_min_time", "Minimum event time (seconds)", 0, 65_535), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_lifecycle_control", equals: true } },
      { ...number("motion_post_time", "Post-event time (seconds)", 0, 65_535), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_lifecycle_control", equals: true } },
      { ...bool("motion_lifecycle_matches_saved", "Lifecycle timing matches saved settings"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion.skip_frame_count", "Frames skipped between checks", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion.video_length", "Event video length", 0), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion_skip_frame_count", "Frames skipped between checks", 0, 65_535), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_skip_frame_count_control", equals: true } },
      { ...number("motion_saved_skip_frame_count", "Saved skipped-frame count", 0, 65_535), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_skip_frame_count_matches_saved", "Skip count matches saved setting"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_timing_note", "Other timing settings"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion_video_length", "Event video length (seconds)", 1, 86_400), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_event_actions_control", equals: true } },
      { ...number("motion_saved_video_length", "Saved event video length", 1, 86_400), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
    ]),
    ...section("Region of interest", [
      number("motion.roi_0_x", "ROI left", 0, 16_384), number("motion.roi_0_y", "ROI top", 0, 16_384),
      number("motion.roi_1_x", "ROI right", 0, 16_384), number("motion.roi_1_y", "ROI bottom", 0, 16_384),
      { ...number("motion.roi_count", "ROI cell count", 1, 52), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...number("motion_roi_count", "Current ROI count", 1, 51), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_roi_note", "Region editing"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_roi_saved", "Saved detection region"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_roi_matches_saved", "Region matches saved setting"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
    ]),
    ...section("Event destinations", [
      { ...bool("motion_send2storage", "Record event video to storage"), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_event_actions_control", equals: true } },
      { ...bool("motion_saved_send2storage", "Saved storage action"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_playonspeaker", "Play built-in motion alert"), description: "Playback requires enabled 16 kHz speaker output, mute and Privacy off, no active talkback, and the installed alert clip. Reachable control does not mean the speaker is ready.", visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_speaker_action_control", equals: true } },
      { ...number("motion_speaker_repeats", "Motion alert repeats", 1, 3), visibleWhen: { path: "motion_full_controls", equals: false }, enabledWhen: { path: "motion_speaker_action_control", equals: true } },
      { ...bool("motion_saved_playonspeaker", "Saved speaker action"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...number("motion_saved_speaker_repeats", "Saved motion alert repeats", 1, 3), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_event_actions_match_saved", "Event actions match saved settings"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_event_actions_note", "Event action status"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion_mqtt_enabled", "Publish motion through Home Assistant MQTT"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...text("motion_mqtt_status", "Home Assistant MQTT status"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
      { ...bool("motion.send2email", "Route motion to email"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2ftp", "Route motion to FTP"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2gotify", "Route motion to Gotify"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2mqtt", "Route motion to MQTT"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2ntfy", "Route motion to ntfy"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2storage", "Route motion to storage"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2telegram", "Route motion to Telegram"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...bool("motion.send2webhook", "Route motion to webhook"), visibleWhen: { path: "motion_full_controls", equals: true } },
      { ...text("motion_destinations_note", "Raptor event destinations"), readOnly: true, visibleWhen: { path: "motion_full_controls", equals: false } },
    ]),
    ...section("Privacy", [bool("privacy.enabled", "Mask every enabled camera stream")]),
  ],
  saveTransform: buildMotionPrivacyUpdate,
};

interface RaptorMotionMqttStatus {
  enabled: boolean | null;
  status: string;
}

export async function loadRaptorMotionMqttStatus(client: ApiClient): Promise<RaptorMotionMqttStatus> {
  const [configResult, runtimeResult] = await Promise.allSettled([
    client.json<unknown>(routes.config.homeAssistant).then(decodeHomeAssistant),
    client.json<unknown>(routes.runtime.homeAssistant).then(decodeHomeAssistantRuntime),
  ]);
  if (configResult.status === "rejected") {
    return { enabled: null, status: "Home Assistant settings could not be read. Motion MQTT state is unknown." };
  }
  if (!configResult.value.enabled) {
    return { enabled: false, status: "Home Assistant is disabled. No Motion MQTT events are published." };
  }
  if (!configResult.value.enable_motion) {
    return { enabled: false, status: "Home Assistant is enabled, but Motion publishing is disabled." };
  }
  if (runtimeResult.status === "rejected") {
    return { enabled: true, status: "Motion MQTT publishing is enabled. Broker status could not be read." };
  }
  const runtime = runtimeResult.value;
  if (runtime.connected) {
    return { enabled: true, status: "Enabled and connected. Motion publishes retained ON/OFF state through Home Assistant MQTT." };
  }
  const detail = runtime.last_error ? ` Last error: ${runtime.last_error}.` : "";
  return { enabled: true, status: `Motion publishing is enabled, but the MQTT worker is ${runtime.state}.${detail}` };
}

export function addRaptorMotionMqttStatus(client: ApiClient, rendered: { node: HTMLElement }): { refresh: () => void; cleanup: () => void } | undefined {
  const status = rendered.node.querySelector<HTMLInputElement>('[name="motion_mqtt_status"]');
  const enabled = rendered.node.querySelector<HTMLInputElement>('[name="motion_mqtt_enabled"]');
  const row = status?.closest(".field");
  if (!row || row.querySelector('[data-motion-ha-settings="true"]')) return;
  row.append(element("a", {
    className: "button button-compact",
    text: "Open Home Assistant settings",
    attrs: { href: "#/home-assistant", "data-page-link": "home-assistant", "data-motion-ha-settings": "true" },
  }));
  let cancelled = false;
  let generation = 0;
  return {
    refresh: () => {
      const requested = ++generation;
      void loadRaptorMotionMqttStatus(client).then((mqtt) => {
        if (cancelled || requested !== generation || !status || !enabled) return;
        enabled.indeterminate = mqtt.enabled === null;
        enabled.checked = mqtt.enabled === true;
        status.value = mqtt.status;
      });
    },
    cleanup: () => { cancelled = true; generation++; },
  };
}

for (const field of motionPrivacy.fields) {
  if (field.path === "motion.enabled") field.enabledWhen = { path: "motion_control", equals: true };
  else if (["motion.roi_0_x", "motion.roi_0_y", "motion.roi_1_x", "motion.roi_1_y"].includes(field.path)) {
    field.enabledWhen = (read) => read("motion_roi_control") === true && (read("motion_full_controls") === true || read("motion.enabled") === true);
  } else if (!field.readOnly && !field.enabledWhen) field.enabledWhen = { path: "motion_full_controls", equals: true };
}

type RaptorMotionConfig = JsonObject & {
  source: "raptor"; persistent: boolean; supported: boolean; available: boolean;
  enabled: boolean | null; saved_enabled: boolean; matches_saved: boolean;
  sensitivity: number | null; sensitivity_available: boolean; saved_sensitivity: number | null; sensitivity_matches_saved: boolean;
  skip_frame_count: number | null; skip_frame_count_available: boolean; saved_skip_frame_count: number | null; skip_frame_count_matches_saved: boolean;
  lifecycle_available: boolean; lifecycle_matches_saved: boolean;
  debounce_time: number | null; cooldown_time: number | null; init_time: number | null; min_time: number | null; post_time: number | null;
  event_actions_available: boolean; video_length: number | null; send2storage: boolean | null; playonspeaker: boolean | null; speaker_repeats: number | null;
  saved_video_length: number | null; saved_send2storage: boolean | null; saved_playonspeaker: boolean | null; saved_speaker_repeats: number | null; event_actions_match_saved: boolean;
  speaker_action_available: boolean; speaker_playback: string | null;
  storage_action_available: boolean; event_action_worker_running: boolean; event_storage_owned_channel: number | null;
  event_storage_starts: number; event_storage_stops: number; event_storage_failures: number; event_action_queue_dropped: number;
  event_speaker_requests: number; event_speaker_failures: number; event_speaker_rate_limited: number;
};

function decodeRaptorMotionConfig(value: object): RaptorMotionConfig {
  const motion = value as JsonObject;
  for (const name of ["persistent", "supported", "available", "saved_enabled", "matches_saved"]) {
    if (typeof motion[name] !== "boolean") throw new TypeError("Motion configuration is incomplete.");
  }
  if (motion.available ? typeof motion.enabled !== "boolean" : motion.enabled !== null) {
    throw new TypeError("Motion observation is incomplete.");
  }
  if ((motion.available && !motion.supported) || motion.matches_saved !== (motion.available && motion.enabled === motion.saved_enabled)) {
    throw new TypeError("Motion saved-state comparison is inconsistent.");
  }
  const sensitivityAvailable = motion.sensitivity_available === true;
  const bounded = (value: unknown): boolean => typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 4;
  if (motion.sensitivity_available !== undefined && (typeof motion.sensitivity_available !== "boolean" ||
      (sensitivityAvailable ? (!bounded(motion.sensitivity) || motion.enabled !== true || motion.available !== true) : motion.sensitivity !== null) ||
      (motion.saved_sensitivity !== null && !bounded(motion.saved_sensitivity)) ||
      motion.sensitivity_matches_saved !== (sensitivityAvailable && motion.sensitivity === motion.saved_sensitivity))) {
    throw new TypeError("Motion sensitivity observation is incomplete.");
  }
  const skipAvailable = motion.skip_frame_count_available === true;
  const boundedSkip = (value: unknown): boolean => typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 65_535;
  if (motion.skip_frame_count_available !== undefined && (typeof motion.skip_frame_count_available !== "boolean" ||
      (skipAvailable ? (!boundedSkip(motion.skip_frame_count) || motion.enabled !== true || motion.available !== true) : motion.skip_frame_count !== null) ||
      (motion.saved_skip_frame_count !== null && !boundedSkip(motion.saved_skip_frame_count)) ||
      motion.skip_frame_count_matches_saved !== (skipAvailable && motion.skip_frame_count === motion.saved_skip_frame_count))) {
    throw new TypeError("Motion skip-frame observation is incomplete.");
  }
  const lifecycleAvailable = motion.lifecycle_available === true;
  const lifecycleNames = ["debounce_time", "cooldown_time", "init_time", "min_time", "post_time"] as const;
  const lifecycleBounded = (name: typeof lifecycleNames[number], candidate: unknown): boolean =>
    typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 0 && candidate <= (name === "cooldown_time" ? 60 : 65_535);
  const lifecycleKnown = lifecycleNames.every((name) => lifecycleBounded(name, motion[name]));
  const lifecycleEmpty = lifecycleNames.every((name) => motion[name] === null);
  if (motion.lifecycle_available !== undefined && (typeof motion.lifecycle_available !== "boolean" ||
      typeof motion.lifecycle_matches_saved !== "boolean" ||
      (lifecycleAvailable ? !lifecycleKnown : !lifecycleKnown && !lifecycleEmpty) ||
      (!lifecycleKnown && motion.lifecycle_matches_saved === true))) {
    throw new TypeError("Motion lifecycle observation is incomplete.");
  }
  const eventActionsAvailable = motion.event_actions_available === true;
  const boundedLength = (candidate: unknown): boolean => typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 1 && candidate <= 86_400;
  const boundedRepeats = (candidate: unknown): boolean => typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 1 && candidate <= 3;
  const counter = (candidate: unknown): boolean => typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 0 && candidate <= 4_294_967_295;
  const playback = ["idle", "accepted", "running", "completed", "preempted", "cancelled", "error"];
  if (motion.event_actions_available !== undefined && (
      typeof motion.event_actions_available !== "boolean" || typeof motion.event_actions_match_saved !== "boolean" ||
      typeof motion.storage_action_available !== "boolean" || typeof motion.event_action_worker_running !== "boolean" ||
      typeof motion.speaker_action_available !== "boolean" ||
      (motion.speaker_action_available ? typeof motion.speaker_playback !== "string" || !playback.includes(motion.speaker_playback) : motion.speaker_playback !== null) ||
      (eventActionsAvailable ? (!boundedLength(motion.video_length) || typeof motion.send2storage !== "boolean" || typeof motion.playonspeaker !== "boolean" || !boundedRepeats(motion.speaker_repeats)) : motion.video_length !== null || motion.send2storage !== null || motion.playonspeaker !== null || motion.speaker_repeats !== null) ||
      (motion.saved_video_length !== null && !boundedLength(motion.saved_video_length)) ||
      (motion.saved_send2storage !== null && typeof motion.saved_send2storage !== "boolean") ||
      (motion.saved_playonspeaker !== null && typeof motion.saved_playonspeaker !== "boolean") ||
      (motion.saved_speaker_repeats !== null && !boundedRepeats(motion.saved_speaker_repeats)) ||
      motion.event_actions_match_saved !== (eventActionsAvailable && motion.video_length === motion.saved_video_length && motion.send2storage === motion.saved_send2storage && motion.playonspeaker === motion.saved_playonspeaker && motion.speaker_repeats === motion.saved_speaker_repeats) ||
      ![motion.event_storage_starts, motion.event_storage_stops, motion.event_storage_failures, motion.event_action_queue_dropped, motion.event_speaker_requests, motion.event_speaker_failures, motion.event_speaker_rate_limited].every(counter) ||
      (motion.event_storage_owned_channel !== null && motion.event_storage_owned_channel !== 0 && motion.event_storage_owned_channel !== 1))) {
    throw new TypeError("Motion event action observation is incomplete.");
  }
  const roi = decodeRaptorMotionRoi(motion.roi);
  if (roi?.available && motion.available !== true) throw new TypeError("Region owner observation is incomplete.");
  return { sensitivity: sensitivityAvailable ? motion.sensitivity as number : null, sensitivity_available: sensitivityAvailable,
    saved_sensitivity: bounded(motion.saved_sensitivity) ? motion.saved_sensitivity as number : null, sensitivity_matches_saved: motion.sensitivity_matches_saved === true,
    skip_frame_count: skipAvailable ? motion.skip_frame_count as number : null, skip_frame_count_available: skipAvailable,
    saved_skip_frame_count: boundedSkip(motion.saved_skip_frame_count) ? motion.saved_skip_frame_count as number : null, skip_frame_count_matches_saved: motion.skip_frame_count_matches_saved === true,
    lifecycle_available: lifecycleAvailable, lifecycle_matches_saved: motion.lifecycle_matches_saved === true,
    debounce_time: lifecycleKnown ? motion.debounce_time as number : null,
    cooldown_time: lifecycleKnown ? motion.cooldown_time as number : null,
    init_time: lifecycleKnown ? motion.init_time as number : null,
    min_time: lifecycleKnown ? motion.min_time as number : null,
    post_time: lifecycleKnown ? motion.post_time as number : null,
    event_actions_available: eventActionsAvailable,
    video_length: eventActionsAvailable ? motion.video_length as number : null,
    send2storage: eventActionsAvailable ? motion.send2storage as boolean : null,
    playonspeaker: eventActionsAvailable ? motion.playonspeaker as boolean : null,
    speaker_repeats: eventActionsAvailable ? motion.speaker_repeats as number : null,
    saved_video_length: boundedLength(motion.saved_video_length) ? motion.saved_video_length as number : null,
    saved_send2storage: typeof motion.saved_send2storage === "boolean" ? motion.saved_send2storage : null,
    saved_playonspeaker: typeof motion.saved_playonspeaker === "boolean" ? motion.saved_playonspeaker : null,
    saved_speaker_repeats: boundedRepeats(motion.saved_speaker_repeats) ? motion.saved_speaker_repeats as number : null,
    event_actions_match_saved: motion.event_actions_match_saved === true,
    storage_action_available: motion.storage_action_available === true,
    event_action_worker_running: motion.event_action_worker_running === true,
    event_storage_owned_channel: motion.event_storage_owned_channel === 0 || motion.event_storage_owned_channel === 1 ? motion.event_storage_owned_channel : null,
    event_storage_starts: counter(motion.event_storage_starts) ? motion.event_storage_starts as number : 0,
    event_storage_stops: counter(motion.event_storage_stops) ? motion.event_storage_stops as number : 0,
    event_storage_failures: counter(motion.event_storage_failures) ? motion.event_storage_failures as number : 0,
    event_action_queue_dropped: counter(motion.event_action_queue_dropped) ? motion.event_action_queue_dropped as number : 0,
    speaker_action_available: motion.speaker_action_available === true,
    speaker_playback: typeof motion.speaker_playback === "string" ? motion.speaker_playback : null,
    event_speaker_requests: counter(motion.event_speaker_requests) ? motion.event_speaker_requests as number : 0,
    event_speaker_failures: counter(motion.event_speaker_failures) ? motion.event_speaker_failures as number : 0,
    event_speaker_rate_limited: counter(motion.event_speaker_rate_limited) ? motion.event_speaker_rate_limited as number : 0,
    roi,
    source: "raptor", persistent: motion.persistent as boolean, supported: motion.supported as boolean,
    available: motion.available as boolean, enabled: motion.enabled as boolean | null,
    saved_enabled: motion.saved_enabled as boolean, matches_saved: motion.matches_saved as boolean };
}

async function saveMotionPrivacy(client: ApiClient, value: JsonObject, loaded: JsonObject): Promise<void> {
  const response = await client.postJson<unknown>(routes.prudynt.command, value);
  if ((loaded.motion as JsonObject).source === "raptor" &&
      (typeof response !== "object" || response === null || !("persistent" in response) || response.persistent !== true)) {
    throw new TypeError("Motion saving was not confirmed. Reload before retrying.");
  }
}

export function buildMotionPrivacyUpdate(value: JsonObject, loaded?: JsonObject): JsonObject {
  if (loaded && (loaded.motion as JsonObject).source === "raptor") {
    const motion = value.motion as JsonObject;
    if (!loaded.motion_control || typeof motion.enabled !== "boolean") throw new TypeError("Choose an explicit motion setting before saving.");
    const update: JsonObject = { enabled: motion.enabled };
    if (motion.enabled && loaded.motion_sensitivity_control === true) {
      if (typeof value.motion_sensitivity !== "number" || !Number.isInteger(value.motion_sensitivity) || value.motion_sensitivity < 0 || value.motion_sensitivity > 4) {
        throw new TypeError("Choose a motion sensitivity from 0 to 4.");
      }
      update.sensitivity = value.motion_sensitivity;
    }
    if (motion.enabled && loaded.motion_skip_frame_count_control === true) {
      if (typeof value.motion_skip_frame_count !== "number" || !Number.isInteger(value.motion_skip_frame_count) || value.motion_skip_frame_count < 0 || value.motion_skip_frame_count > 65_535) {
        throw new TypeError("Choose a skipped-frame count from 0 to 65535.");
      }
      update.skip_frame_count = value.motion_skip_frame_count;
    }
    if (loaded.motion_lifecycle_control === true) {
      const timing = [
        ["debounce_time", "motion_debounce_time", 65_535],
        ["cooldown_time", "motion_cooldown_time", 60],
        ["init_time", "motion_init_time", 65_535],
        ["min_time", "motion_min_time", 65_535],
        ["post_time", "motion_post_time", 65_535],
      ] as const;
      for (const [target, source, maximum] of timing) {
        const candidate = value[source];
        if (typeof candidate !== "number" || !Number.isInteger(candidate) || candidate < 0 || candidate > maximum) {
          throw new TypeError("Choose complete bounded motion lifecycle settings.");
        }
        update[target] = candidate;
      }
    }
    if (loaded.motion_event_actions_control === true) {
      if (typeof value.motion_video_length !== "number" || !Number.isInteger(value.motion_video_length) || value.motion_video_length < 1 || value.motion_video_length > 86_400 ||
          typeof value.motion_send2storage !== "boolean" || typeof value.motion_playonspeaker !== "boolean" ||
          typeof value.motion_speaker_repeats !== "number" || !Number.isInteger(value.motion_speaker_repeats) || value.motion_speaker_repeats < 1 || value.motion_speaker_repeats > 3) {
        throw new TypeError("Choose complete bounded event actions.");
      }
      update.video_length = value.motion_video_length;
      update.send2storage = value.motion_send2storage;
      update.playonspeaker = value.motion_playonspeaker;
      update.speaker_repeats = value.motion_speaker_repeats;
    }
    if (motion.enabled && loaded.motion_roi_control === true && motionRoiKeys.some((key) => motion[key] !== null && motion[key] !== undefined)) {
      const roi = decodeRaptorMotionRoi((loaded.motion as JsonObject).roi);
      if (!roi?.available) throw new TypeError("Region geometry is unavailable. Reload before saving.");
      validateMotionRegion(motion, roi.frame_width as number, roi.frame_height as number);
      for (const key of motionRoiKeys) update[key] = motion[key]!;
      update.roi_count = 1;
    }
    return { motion: update };
  }
  const privacy = value.privacy;
  if (typeof privacy !== "object" || privacy === null || Array.isArray(privacy) || typeof privacy.enabled !== "boolean") {
    throw new TypeError("Privacy configuration is incomplete.");
  }
  return {
    motion: value.motion ?? {},
    privacy: { enabled: privacy.enabled, stream0_enabled: privacy.enabled, stream1_enabled: privacy.enabled },
  };
}

export const raptorPrivacy: ConfigFormSpec = {
  eyebrow: "Privacy",
  title: "Privacy protection",
  description: "Save protection for video, snapshots and the microphone. Saved protection is applied when the camera services start.",
  endpoint: routes.prudynt.domain("privacy"),
  saveEndpoint: routes.prudynt.command,
  saveLabel: "Save privacy",
  refreshStreamerPreview: true,
  decode: decodeRaptorPrivacy,
  fields: [
    { ...bool("enabled", "Protect video and microphone"), enabledWhen: { path: "supported", equals: true } },
    { ...bool("saved_enabled", "Saved privacy setting"), readOnly: true },
    { ...bool("matches_saved", "Privacy matches saved setting"), readOnly: true },
  ],
  saveTransform: buildRaptorPrivacyUpdate,
  save: async (client, value) => {
    const reply = await client.postJson<unknown>(routes.prudynt.command, value);
    if (typeof reply !== "object" || reply === null ||
        !("persistent" in reply) || reply.persistent !== true || !("applied" in reply) || reply.applied !== true) {
      throw new TypeError("Privacy saving was not confirmed. Reload and explicitly retry protection or off.");
    }
  },
};

export const raptorMotionWebhook: ConfigFormSpec = {
  eyebrow: "Event destination",
  title: "Motion webhook",
  fieldIdPrefix: "motion-webhook",
  description: "Send a small JSON event to one administrator-configured HTTP or verified HTTPS endpoint. Delivery is queued and never includes media or the saved endpoint in status responses.",
  endpoint: routes.config.motionWebhook,
  saveLabel: "Save webhook",
  load: async (client) => {
    const config = decodeMotionWebhookConfig(await client.json<unknown>(routes.config.motionWebhook));
    const runtime = decodeMotionWebhookRuntime(await client.json<unknown>(routes.runtime.motionWebhook));
    return {
      ...config,
      transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; HTTP status ${runtime.last_http_status ?? "none"}; requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.`,
    };
  },
  fields: [
    bool("enabled", "Send Motion events to webhook"),
    secret("url", "Webhook URL", "HTTP or HTTPS. HTTPS always verifies the certificate and hostname. Leave blank to keep the saved URL."),
    { ...bool("url_set", "Webhook URL is saved"), readOnly: true },
    { ...bool("live_url_set", "Saved webhook URL is loaded"), readOnly: true },
    { ...bool("saved_enabled", "Saved webhook setting"), readOnly: true },
    { ...bool("matches_saved", "Webhook runtime matches saved setting"), readOnly: true },
    { ...bool("transport_ready", "Verified HTTP transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true },
  ],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) return "Webhook delivery is unavailable on this camera.";
    if (value.enabled === true && loaded.url_set !== true && (typeof value.url !== "string" || value.url.length === 0)) {
      return "Enter a webhook URL before enabling delivery.";
    }
    return undefined;
  },
  saveTransform: buildMotionWebhookUpdate,
  save: async (client, value) => {
    const reply = await client.postJson<unknown>(routes.config.motionWebhook, value);
    if (typeof reply !== "object" || reply === null || !("status" in reply) || reply.status !== "applied" ||
        !("persistent" in reply) || reply.persistent !== true) {
      throw new TypeError("Webhook persistence and application were not confirmed. Reload saved and live status before retrying.");
    }
  },
};

export const raptorMotionNtfy: ConfigFormSpec = {
  eyebrow: "Event destination",
  title: "Motion ntfy",
  fieldIdPrefix: "motion-ntfy",
  description: "Send a readable text notification to one administrator-configured ntfy topic URL. Delivery has its own bounded queue and never includes media or secrets in status responses.",
  endpoint: routes.config.motionNtfy,
  saveLabel: "Save ntfy",
  load: async (client) => {
    const config = decodeMotionNtfyConfig(await client.json<unknown>(routes.config.motionNtfy));
    const runtime = decodeMotionNtfyRuntime(await client.json<unknown>(routes.runtime.motionNtfy));
    return {
      ...config,
      clear_token: false,
      transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; HTTP status ${runtime.last_http_status ?? "none"}; requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.`,
    };
  },
  fields: [
    bool("enabled", "Send Motion events to ntfy"),
    secret("url", "ntfy topic URL", "Full HTTP or HTTPS topic URL. HTTPS always verifies the certificate and hostname. Leave blank to keep the saved URL."),
    secret("token", "Bearer token", "Optional. Tokens require HTTPS. Leave blank to keep the saved token."),
    bool("clear_token", "Clear saved bearer token"),
    { ...bool("url_set", "ntfy topic URL is saved"), readOnly: true },
    { ...bool("token_set", "ntfy bearer token is saved"), readOnly: true },
    { ...bool("matches_saved", "ntfy runtime matches saved setting"), readOnly: true },
    { ...bool("transport_ready", "Verified HTTP transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true },
  ],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) return "ntfy delivery is unavailable on this camera.";
    if (value.enabled === true && loaded.url_set !== true && (typeof value.url !== "string" || value.url.length === 0)) {
      return "Enter an ntfy topic URL before enabling delivery.";
    }
    if (typeof value.token === "string" && value.token.length > 0 && typeof value.url === "string" && value.url.length > 0 && !value.url.startsWith("https://")) {
      return "Bearer tokens require an HTTPS ntfy topic URL.";
    }
    if (value.clear_token === true && typeof value.token === "string" && value.token.length > 0) {
      return "Clear the token field or turn off the clear-token choice.";
    }
    if (typeof value.url === "string" && value.url.startsWith("http://") && loaded.token_set === true && value.clear_token !== true) {
      return "Clear the saved bearer token before changing the ntfy topic to HTTP.";
    }
    return undefined;
  },
  saveTransform: buildMotionNtfyUpdate,
  save: async (client, value) => {
    const reply = await client.postJson<unknown>(routes.config.motionNtfy, value);
    if (typeof reply !== "object" || reply === null || !("status" in reply) || reply.status !== "applied" ||
        !("persistent" in reply) || reply.persistent !== true) {
      throw new TypeError("ntfy persistence and application were not confirmed. Reload saved and live status before retrying.");
    }
  },
};

export function buildMotionNtfyUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") throw new TypeError("Choose an explicit ntfy setting.");
  const update: JsonObject = { enabled: value.enabled };
  if (typeof value.url === "string" && value.url.length > 0) update.url = value.url;
  if (typeof value.token === "string" && value.token.length > 0) update.token = value.token;
  if (value.clear_token === true) update.clear_token = true;
  return update;
}

export const raptorMotionGotify: ConfigFormSpec = {
  eyebrow: "Event destination",
  title: "Motion Gotify",
  fieldIdPrefix: "motion-gotify",
  description: "Send the fixed text 'Motion detected.' to one Gotify message endpoint. The dedicated bounded worker does not send snapshots or clips.",
  endpoint: routes.config.motionGotify,
  saveLabel: "Save Gotify",
  load: async (client) => {
    const config = decodeMotionGotifyConfig(await client.json<unknown>(routes.config.motionGotify));
    const runtime = decodeMotionDestinationRuntime(await client.json<unknown>(routes.runtime.motionGotify), "Gotify");
    return { ...config, clear_endpoint: false, clear_token: false, transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; HTTP status ${runtime.last_http_status ?? "none"}; requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.` };
  },
  fields: [
    bool("enabled", "Send Motion events to Gotify"),
    secret("endpoint", "Gotify message endpoint", "Full HTTP or HTTPS /message endpoint. Tokens require verified HTTPS. Leave blank to keep the saved endpoint."),
    secret("token", "Gotify application token", "Leave blank to keep the saved token."),
    bool("clear_endpoint", "Clear saved endpoint"), bool("clear_token", "Clear saved token"),
    { ...bool("endpoint_set", "Gotify endpoint is saved"), readOnly: true }, { ...bool("token_set", "Gotify token is saved"), readOnly: true },
    { ...bool("matches_saved", "Gotify runtime matches saved setting"), readOnly: true }, { ...bool("transport_ready", "Verified HTTP transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true },
  ],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) return "Gotify delivery is unavailable on this camera.";
    if (value.enabled === true && loaded.endpoint_set !== true && !(typeof value.endpoint === "string" && value.endpoint.length > 0)) return "Enter a Gotify message endpoint before enabling delivery.";
    if (value.enabled === true && loaded.token_set !== true && !(typeof value.token === "string" && value.token.length > 0)) return "Enter a Gotify application token before enabling delivery.";
    const endpoint = typeof value.endpoint === "string" ? value.endpoint : "";
    if (endpoint.length > 0 && !endpoint.startsWith("https://") && ((typeof value.token === "string" && value.token.length > 0) || (loaded.token_set === true && value.clear_token !== true))) return "Gotify tokens require an HTTPS endpoint.";
    return undefined;
  },
  saveTransform: buildMotionGotifyUpdate,
  save: async (client, value) => confirmApplied(await client.postJson<unknown>(routes.config.motionGotify, value), "Gotify"),
};

export function buildMotionGotifyUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") throw new TypeError("Choose an explicit Gotify setting.");
  const update: JsonObject = { enabled: value.enabled };
  for (const name of ["endpoint", "token"] as const) if (typeof value[name] === "string" && value[name].length > 0) update[name] = value[name];
  for (const name of ["clear_endpoint", "clear_token"] as const) if (value[name] === true) update[name] = true;
  return update;
}

export const raptorMotionEmail: ConfigFormSpec = {
  eyebrow: "Event destination",
  title: "Motion Email",
  fieldIdPrefix: "motion-email",
  description: "Send a fixed 'Motion detected.' text email with the fixed subject 'Motion'. TLS is required. No snapshot, clip, custom subject, or custom body is sent.",
  endpoint: routes.config.motionEmail,
  saveLabel: "Save Email",
  load: async (client) => {
    const config = decodeMotionEmailConfig(await client.json<unknown>(routes.config.motionEmail));
    const runtime = decodeMotionEmailRuntime(await client.json<unknown>(routes.runtime.motionEmail));
    return {
      ...config,
      enabled: config.saved_enabled ?? null,
      clear_password: false,
      transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; SMTP status ${runtime.last_smtp_status ?? "none"}; requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.`,
    };
  },
  fields: [
    bool("enabled", "Send Motion events by email"),
    text("host", "SMTP host"),
    number("port", "SMTP port", 1, 65535),
    select("tls_mode", "TLS mode", ["starttls", "implicit"]),
    text("username", "SMTP username"),
    secret("password", "SMTP password", "Leave blank to keep the saved password."),
    bool("clear_password", "Clear saved SMTP password"),
    text("from_address", "From address"),
    text("to_address", "To address"),
    { ...bool("password_set", "SMTP password is saved"), readOnly: true },
    { ...bool("matches_saved", "Email runtime matches saved setting"), readOnly: true },
    { ...bool("transport_ready", "SMTP and SMTPS transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true },
  ],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) {
      return "SMTP delivery is unavailable on this camera.";
    }
    for (const name of ["host", "from_address", "to_address"] as const) {
      if (value.enabled === true && !(typeof value[name] === "string" && value[name].length > 0)) {
        return `Enter ${name.replaceAll("_", " ")} before enabling email.`;
      }
    }
    const username = typeof value.username === "string" ? value.username : "";
    const hasPassword = (typeof value.password === "string" && value.password.length > 0) || (loaded.password_set === true && value.clear_password !== true);
    if (value.enabled === true && (username.length > 0) !== hasPassword) {
      return "SMTP username and password must either both be set or both be empty.";
    }
    return undefined;
  },
  saveTransform: buildMotionEmailUpdate,
  save: async (client, value) => {
    confirmApplied(await client.postJson<unknown>(routes.config.motionEmail, value), "Email");
  },
};

export function buildMotionEmailUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") {
    throw new TypeError("Choose an explicit Email setting.");
  }
  const update: JsonObject = { enabled: value.enabled };
  for (const name of ["host", "port", "tls_mode", "username", "from_address", "to_address"] as const) {
    if (value[name] !== undefined) {
      update[name] = value[name];
    }
  }
  if (typeof value.password === "string" && value.password.length > 0) {
    update.password = value.password;
  }
  if (value.clear_password === true) {
    update.clear_password = true;
  }
  return update;
}

export const raptorMotionFtp: ConfigFormSpec = {
  eyebrow: "Event destination",
  title: "Motion FTP",
  fieldIdPrefix: "motion-ftp",
  description: "Upload one Motion JPEG from stream 1 over verified explicit FTPS. The worker uses a fixed unique filename, never uploads video, and does not retry. A Privacy or configuration change cancels the transfer on a best-effort basis; bytes already sent cannot be recalled.",
  endpoint: routes.config.motionFtp,
  saveLabel: "Save FTP",
  load: async (client) => {
    const config = decodeMotionFtpConfig(await client.json<unknown>(routes.config.motionFtp));
    const runtime = decodeMotionFtpRuntime(await client.json<unknown>(routes.runtime.motionFtp));
    return {
      ...config,
      enabled: config.saved_enabled ?? null,
      clear_password: false,
      transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; FTP status ${runtime.last_ftp_status ?? "none"}; captures ${runtime.captures}, requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, cancellations ${runtime.cancellations}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.`,
    };
  },
  fields: [
    bool("enabled", "Upload Motion JPEG by FTPS"),
    text("host", "FTP host"),
    number("port", "FTP port", 1, 65535),
    select("tls_mode", "TLS mode", ["explicit"]),
    text("username", "FTP username"),
    secret("password", "FTP password", "Leave blank to keep the saved password."),
    bool("clear_password", "Clear saved FTP password"),
    text("path", "Remote directory"),
    { ...bool("password_set", "FTP password is saved"), readOnly: true },
    { ...bool("matches_saved", "FTP runtime matches saved setting"), readOnly: true },
    { ...bool("transport_ready", "Verified explicit FTPS transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true },
  ],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) {
      return "Verified FTPS delivery is unavailable on this camera.";
    }
    for (const name of ["host", "username"] as const) {
      if (value.enabled === true && !(typeof value[name] === "string" && value[name].length > 0)) {
        return `Enter ${name.replaceAll("_", " ")} before enabling FTP.`;
      }
    }
    const hasPassword = (typeof value.password === "string" && value.password.length > 0)
      || (loaded.password_set === true && value.clear_password !== true);
    if (value.enabled === true && !hasPassword) {
      return "Enter an FTP password before enabling delivery.";
    }
    return undefined;
  },
  saveTransform: buildMotionFtpUpdate,
  save: async (client, value) => {
    confirmApplied(await client.postJson<unknown>(routes.config.motionFtp, value), "FTP");
  },
};

export function buildMotionFtpUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") {
    throw new TypeError("Choose an explicit FTP setting.");
  }
  const update: JsonObject = { enabled: value.enabled };
  for (const name of ["host", "port", "tls_mode", "username", "path"] as const) {
    if (value[name] !== undefined) {
      update[name] = value[name];
    }
  }
  if (typeof value.password === "string" && value.password.length > 0) {
    update.password = value.password;
  }
  if (value.clear_password === true) {
    update.clear_password = true;
  }
  return update;
}

export const raptorMotionTelegram: ConfigFormSpec = {
  eyebrow: "Event destination", title: "Motion Telegram", fieldIdPrefix: "motion-telegram",
  description: "Send the fixed text 'Motion detected.' with Telegram Bot API sendMessage. The target is fixed to api.telegram.org and no media is sent.",
  endpoint: routes.config.motionTelegram, saveLabel: "Save Telegram",
  load: async (client) => {
    const config = decodeMotionTelegramConfig(await client.json<unknown>(routes.config.motionTelegram));
    const runtime = decodeMotionDestinationRuntime(await client.json<unknown>(routes.runtime.motionTelegram), "Telegram");
    return { ...config, clear_bot_token: false, clear_chat_id: false, transport_ready: config.transport_available === true,
      delivery_status: `Worker ${runtime.running ? "is running" : "is stopped"}; last result ${runtime.last_result}; HTTP status ${runtime.last_http_status ?? "none"}; requests ${runtime.requests}, successes ${runtime.successes}, failures ${runtime.failures}, queued ${runtime.queue_depth}, dropped ${runtime.queue_dropped}.` };
  },
  fields: [bool("enabled", "Send Motion events to Telegram"), secret("bot_token", "Telegram bot token", "Leave blank to keep the saved token."),
    secret("chat_id", "Telegram chat destination", "Numeric chat ID or @channel username. Leave blank to keep the saved destination."),
    bool("clear_bot_token", "Clear saved bot token"), bool("clear_chat_id", "Clear saved chat destination"),
    { ...bool("bot_token_set", "Telegram bot token is saved"), readOnly: true }, { ...bool("chat_id_set", "Telegram chat destination is saved"), readOnly: true },
    { ...bool("matches_saved", "Telegram runtime matches saved setting"), readOnly: true }, { ...bool("transport_ready", "Verified HTTPS transport is available"), readOnly: true },
    { ...text("delivery_status", "Delivery status"), readOnly: true }],
  validate: (value, loaded) => {
    if (value.enabled === true && loaded.transport_available !== true) return "Telegram delivery is unavailable on this camera.";
    if (value.enabled === true && loaded.bot_token_set !== true && !(typeof value.bot_token === "string" && value.bot_token.length > 0)) return "Enter a Telegram bot token before enabling delivery.";
    if (value.enabled === true && loaded.chat_id_set !== true && !(typeof value.chat_id === "string" && value.chat_id.length > 0)) return "Enter a Telegram chat destination before enabling delivery.";
    return undefined;
  },
  saveTransform: buildMotionTelegramUpdate,
  save: async (client, value) => confirmApplied(await client.postJson<unknown>(routes.config.motionTelegram, value), "Telegram"),
};

export function buildMotionTelegramUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") throw new TypeError("Choose an explicit Telegram setting.");
  const update: JsonObject = { enabled: value.enabled };
  for (const name of ["bot_token", "chat_id"] as const) if (typeof value[name] === "string" && value[name].length > 0) update[name] = value[name];
  for (const name of ["clear_bot_token", "clear_chat_id"] as const) if (value[name] === true) update[name] = true;
  return update;
}

function decodeMotionGotifyConfig(value: unknown): JsonObject {
  const state = decodeRedactedDestination(value, "Gotify", ["endpoint", "token"]);
  return state;
}

function decodeMotionEmailConfig(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Email configuration is unavailable.");
  }
  const state = value as JsonObject;
  for (const name of ["saved_enabled", "enabled", "password_set", "live_password_set", "matches_saved", "transport_available"]) {
    if (typeof state[name] !== "boolean") {
      throw new TypeError("Email configuration is incomplete.");
    }
  }
  for (const name of ["host", "live_host", "tls_mode", "live_tls_mode", "username", "live_username", "from_address", "live_from_address", "to_address", "live_to_address"]) {
    if (typeof state[name] !== "string") {
      throw new TypeError("Email configuration is incomplete.");
    }
  }
  for (const name of ["port", "live_port"]) {
    if (typeof state[name] !== "number") {
      throw new TypeError("Email configuration is incomplete.");
    }
  }
  const savedTlsMode = String(state.tls_mode);
  const liveTlsMode = String(state.live_tls_mode);
  if (state.password !== null || !["starttls", "implicit"].includes(savedTlsMode) || !["starttls", "implicit"].includes(liveTlsMode)) {
    throw new TypeError("Email configuration is incomplete.");
  }
  return state;
}

function decodeMotionEmailRuntime(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Email runtime is unavailable.");
  }
  const state = value as JsonObject;
  for (const name of ["running", "enabled", "transport_available"]) {
    if (typeof state[name] !== "boolean") {
      throw new TypeError("Email runtime is incomplete.");
    }
  }
  for (const name of ["queue_capacity", "queue_depth", "queue_dropped", "requests", "successes", "failures"]) {
    if (typeof state[name] !== "number") {
      throw new TypeError("Email runtime counters are incomplete.");
    }
  }
  const validResult = ["idle", "success", "smtp_error", "transport_error"].includes(String(state.last_result));
  const validStatus = state.last_smtp_status === null || typeof state.last_smtp_status === "number";
  if (state.queue_capacity !== 2 || !validResult || !validStatus) {
    throw new TypeError("Email delivery status is incomplete.");
  }
  return state;
}

function decodeMotionFtpConfig(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("FTP configuration is unavailable.");
  }
  const state = value as JsonObject;
  for (const name of ["saved_enabled", "enabled", "password_set", "live_password_set", "matches_saved", "transport_available"]) {
    if (typeof state[name] !== "boolean") {
      throw new TypeError("FTP configuration is incomplete.");
    }
  }
  for (const name of ["host", "live_host", "tls_mode", "live_tls_mode", "username", "live_username", "path", "live_path"]) {
    if (typeof state[name] !== "string") {
      throw new TypeError("FTP configuration is incomplete.");
    }
  }
  for (const name of ["port", "live_port"]) {
    if (typeof state[name] !== "number") {
      throw new TypeError("FTP configuration is incomplete.");
    }
  }
  if (state.password !== null || state.tls_mode !== "explicit" || state.live_tls_mode !== "explicit") {
    throw new TypeError("FTP configuration is incomplete.");
  }
  return state;
}

function decodeMotionFtpRuntime(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("FTP runtime is unavailable.");
  }
  const state = value as JsonObject;
  for (const name of ["running", "enabled", "transport_available"]) {
    if (typeof state[name] !== "boolean") {
      throw new TypeError("FTP runtime is incomplete.");
    }
  }
  for (const name of ["queue_capacity", "queue_depth", "queue_dropped", "captures", "requests", "successes", "failures", "cancellations"]) {
    if (typeof state[name] !== "number") {
      throw new TypeError("FTP runtime counters are incomplete.");
    }
  }
  const validResult = ["idle", "success", "capture_error", "ftp_error", "cancelled"].includes(String(state.last_result));
  const validStatus = state.last_ftp_status === null || typeof state.last_ftp_status === "number";
  if (state.queue_capacity !== 2 || !validResult || !validStatus) {
    throw new TypeError("FTP delivery status is incomplete.");
  }
  return state;
}

function decodeMotionTelegramConfig(value: unknown): JsonObject {
  return decodeRedactedDestination(value, "Telegram", ["bot_token", "chat_id"]);
}

function decodeRedactedDestination(value: unknown, label: string, secrets: string[]): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError(`${label} configuration is unavailable.`);
  const state = value as JsonObject;
  for (const name of ["saved_enabled", "enabled", "matches_saved", "transport_available", ...secrets.flatMap((name) => [`${name}_set`, `live_${name}_set`])]) if (typeof state[name] !== "boolean") throw new TypeError(`${label} configuration is incomplete.`);
  for (const name of secrets) if (state[name] !== null) throw new TypeError(`${label} secrets must be redacted.`);
  return state;
}

function decodeMotionDestinationRuntime(value: unknown, label: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError(`${label} runtime is unavailable.`);
  const state = value as JsonObject;
  for (const name of ["running", "enabled", "transport_available"]) if (typeof state[name] !== "boolean") throw new TypeError(`${label} runtime is incomplete.`);
  for (const name of ["queue_capacity", "queue_depth", "queue_dropped", "requests", "successes", "failures"]) if (typeof state[name] !== "number") throw new TypeError(`${label} runtime counters are incomplete.`);
  if (state.queue_capacity !== 2 || !["idle", "success", "http_error", "transport_error"].includes(String(state.last_result)) || (state.last_http_status !== null && typeof state.last_http_status !== "number")) throw new TypeError(`${label} delivery status is incomplete.`);
  return state;
}

function confirmApplied(value: unknown, label: string): void {
  if (typeof value !== "object" || value === null || !("status" in value) || value.status !== "applied" || !("persistent" in value) || value.persistent !== true) throw new TypeError(`${label} persistence and application were not confirmed. Reload saved and live status before retrying.`);
}

function decodeMotionNtfyConfig(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("ntfy configuration is unavailable.");
  const state = value as JsonObject;
  for (const name of ["saved_enabled", "enabled", "url_set", "live_url_set", "token_set", "live_token_set", "matches_saved", "transport_available"]) {
    if (typeof state[name] !== "boolean") throw new TypeError("ntfy configuration is incomplete.");
  }
  if (state.url !== null || state.token !== null) throw new TypeError("ntfy secrets must be redacted.");
  return state;
}

function decodeMotionNtfyRuntime(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("ntfy runtime is unavailable.");
  const state = value as JsonObject;
  for (const name of ["running", "enabled", "transport_available"]) {
    if (typeof state[name] !== "boolean") throw new TypeError("ntfy runtime is incomplete.");
  }
  for (const name of ["queue_capacity", "queue_depth", "queue_dropped", "requests", "successes", "failures"]) {
    if (typeof state[name] !== "number") throw new TypeError("ntfy runtime counters are incomplete.");
  }
  if (state.queue_capacity !== 2 || !["idle", "success", "http_error", "transport_error"].includes(String(state.last_result)) ||
      (state.last_http_status !== null && typeof state.last_http_status !== "number")) {
    throw new TypeError("ntfy delivery status is incomplete.");
  }
  return state;
}

export function buildMotionWebhookUpdate(value: JsonObject): JsonObject {
  if (typeof value.enabled !== "boolean") throw new TypeError("Choose an explicit webhook setting.");
  const update: JsonObject = { enabled: value.enabled };
  if (typeof value.url === "string" && value.url.length > 0) update.url = value.url;
  return update;
}

function decodeMotionWebhookConfig(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Webhook configuration is unavailable.");
  const state = value as JsonObject;
  if (typeof state.saved_enabled !== "boolean" || typeof state.enabled !== "boolean" || state.url !== null ||
      typeof state.url_set !== "boolean" || typeof state.live_url_set !== "boolean" || typeof state.matches_saved !== "boolean" ||
      typeof state.transport_available !== "boolean") {
    throw new TypeError("Webhook configuration is incomplete.");
  }
  return state;
}

function decodeMotionWebhookRuntime(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Webhook runtime is unavailable.");
  const state = value as JsonObject;
  for (const name of ["running", "enabled", "transport_available"]) {
    if (typeof state[name] !== "boolean") throw new TypeError("Webhook runtime is incomplete.");
  }
  for (const name of ["queue_depth", "queue_dropped", "requests", "successes", "failures"]) {
    if (typeof state[name] !== "number") throw new TypeError("Webhook runtime counters are incomplete.");
  }
  if (typeof state.last_result !== "string" || (state.last_http_status !== null && typeof state.last_http_status !== "number")) {
    throw new TypeError("Webhook delivery status is incomplete.");
  }
  return state;
}

export function decodeRaptorPrivacy(value: unknown): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Privacy configuration is unavailable.");
  const state = value as JsonObject;
  if (state.source !== "raptor" || state.persistent !== true || typeof state.supported !== "boolean" ||
      typeof state.available !== "boolean" || typeof state.matches_saved !== "boolean" ||
      (state.available ? typeof state.enabled !== "boolean" : state.enabled !== null) ||
      (state.saved_enabled !== null && typeof state.saved_enabled !== "boolean") ||
      (state.available && !state.supported) ||
      state.matches_saved !== (state.available && state.saved_enabled !== null && state.enabled === state.saved_enabled)) {
    throw new TypeError("Privacy observation is incomplete. Reload before saving.");
  }
  return state;
}

export function buildRaptorPrivacyUpdate(value: JsonObject, loaded: JsonObject): JsonObject {
  if (loaded.supported !== true || loaded.persistent !== true || typeof value.enabled !== "boolean") {
    throw new TypeError("Choose an explicit privacy setting before saving.");
  }
  return { privacy: { enabled: value.enabled } };
}

const motionRoiKeys = ["roi_0_x", "roi_0_y", "roi_1_x", "roi_1_y"] as const;

function validateMotionRegion(region: JsonObject, width: number, height: number): void {
  for (const key of motionRoiKeys) {
    if (typeof region[key] !== "number" || !Number.isInteger(region[key]) || region[key] < 0) {
      throw new TypeError("Enter all four region edges in detection-frame pixels.");
    }
  }
  const [left, top, right, bottom] = motionRoiKeys.map((key) => region[key] as number);
  if (right! - left! < 2 || bottom! - top! < 2 || right! > width || bottom! > height) {
    throw new TypeError(`The region must be at least 2 × 2 pixels and fit inside ${width} × ${height}. Right and bottom edges are excluded.`);
  }
}

export function decodeRaptorMotionRoi(value: unknown): JsonObject | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new TypeError("Region observation is incomplete.");
  const roi = value as JsonObject;
  for (const key of ["supported", "available", "applied", "matches_saved"]) {
    if (typeof roi[key] !== "boolean") throw new TypeError("Region observation is incomplete.");
  }
  const bounded = (value: unknown, min: number, max: number): boolean => typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
  const single = (value: unknown, width: number, height: number): JsonObject => {
    if (typeof value !== "object" || value === null || Array.isArray(value) || Object.keys(value).length !== 4) throw new TypeError("Region coordinates are incomplete.");
    validateMotionRegion(value as JsonObject, width, height);
    return value as JsonObject;
  };
  if ((roi.available && !roi.supported) || (roi.applied && !roi.available)) throw new TypeError("Region capability is inconsistent.");
  if (roi.available) {
    if (!bounded(roi.frame_width, 2, 16384) || !bounded(roi.frame_height, 2, 16384) || !bounded(roi.count, 1, 51)) throw new TypeError("Region frame geometry is incomplete.");
    if (roi.count === 1) single(roi.single, roi.frame_width as number, roi.frame_height as number);
    else if (roi.single !== null) throw new TypeError("Multiple regions cannot be represented as one rectangle.");
  } else if (["frame_width", "frame_height", "count", "single"].some((key) => roi[key] !== null)) {
    throw new TypeError("Unavailable region geometry must stay unknown.");
  }
  if (roi.saved_single !== null) single(roi.saved_single, 16384, 16384);
  const equal = roi.single !== null && roi.saved_single !== null && motionRoiKeys.every((key) => (roi.single as JsonObject)[key] === (roi.saved_single as JsonObject)[key]);
  if (roi.matches_saved !== (roi.available && roi.applied && equal)) throw new TypeError("Region saved-state comparison is inconsistent.");
  return roi;
}

function motionRoiView(roi: JsonObject | null): JsonObject {
  const saved = roi?.saved_single as JsonObject | null;
  return {
    motion_roi_control: roi?.supported === true && roi.available === true,
    motion_roi_count: roi?.count ?? null,
    motion_roi_matches_saved: roi?.matches_saved ?? false,
    motion_roi_saved: saved ? `${saved.roi_0_x}, ${saved.roi_0_y} to ${saved.roi_1_x}, ${saved.roi_1_y} (end edges excluded)` : "Saved single region unavailable",
    motion_roi_note: !roi?.available ? "Region editing is unavailable." : "Select motion on to edit and save a region. Saving off discards region edits. " + (!roi.applied ?
      "Update unconfirmed. Select motion on and save the region again." : roi.count !== 1 ?
      `${roi.count} regions. Enter all four edges to save one rectangle; motion pauses briefly.` :
      "One region. End edges are excluded. Changing the region pauses motion briefly."),
  };
}
