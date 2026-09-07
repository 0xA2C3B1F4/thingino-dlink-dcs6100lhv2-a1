import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeHomeAssistant, decodeRecorder } from "../../api/decode";
import { button, element, setMessage, statusMessage } from "../../app/dom";
import { type ConfigFormSpec } from "../../app/forms";
import { ControlApi } from "../../api/control";
import { bool, text, number, select, secret, section } from "./common";

export const homeAssistant: ConfigFormSpec = {
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

export function addHomeAssistantRuntime(client: ApiClient, rendered: { node: HTMLElement }): () => void {
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

export const recorder: ConfigFormSpec = {
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

export const timelapse: ConfigFormSpec = {
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
