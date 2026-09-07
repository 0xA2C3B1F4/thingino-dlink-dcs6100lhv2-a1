import type { JsonObject } from "../../api/contracts";
import { routes } from "../../api/routes";
import { decodeMotion, decodePrivacy } from "../../api/decode";
import { type ConfigFormSpec } from "../../app/forms";
import { bool, text, number, select, section, prudyntLoad } from "./common";

export const motionPrivacy: ConfigFormSpec = {
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
