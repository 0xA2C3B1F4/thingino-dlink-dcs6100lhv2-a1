import type { JsonObject } from "../../api/contracts";
import { ApiClient, ApiRequestError } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeHomeAssistant, decodeRecorder, decodeRaptorTimelapse } from "../../api/decode";
import { button, element, setMessage, statusMessage } from "../../app/dom";
import { renderConfigForm, type ConfigFormSpec } from "../../app/forms";
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
      bool("enabled", "Enable Home Assistant integration", "Save settings to connect automatically to your MQTT broker."),
    ]),
    ...section("Broker", [
      text("mqtt.host", "MQTT host", true), number("mqtt.port", "MQTT port", 1, 65535),
    text("mqtt.username", "MQTT username"), secret("mqtt.password", "MQTT password", "Leave blank to keep the current secret."),
    text("mqtt.client_id_prefix", "MQTT client ID prefix"),
    bool("mqtt.use_ssl", "Use TLS for MQTT"), { ...bool("mqtt.tls_skip_verify", "Skip MQTT TLS certificate verification", "Available only for explicitly trusted private brokers."), enabledWhen: { path: "mqtt.use_ssl", equals: true } },
    ]),
    ...section("Identity and intervals", [
      text("device_name", "Device name"), text("device_model", "Device model"), text("discovery_prefix", "Discovery prefix"),
      { ...number("state_interval", "State interval (seconds)", 5, 604800), description: "Periodic state refresh. Motion notifications and command readback do not wait for this interval." },
      number("discovery_interval", "Discovery interval (seconds)", 60, 604800),
      number("camera_interval", "Camera preview interval (seconds)", 5, 604800),
    ]),
    ...section("D-Link A1 entities", [
    bool("enable_motion", "Publish motion"), bool("enable_live_view", "Publish camera preview", "Publishes fresh JPEG images over MQTT from the selected Raptor stream. Requires an available JPEG source."), bool("enable_daynight", "Publish day / night"),
    bool("enable_privacy", "Publish privacy"), bool("enable_snapshot", "Publish snapshot action"), bool("enable_ircut", "Publish IR filter"), bool("enable_ir850", "Publish 850 nm IR LED"),
    bool("enable_motion_guard", "Publish motion guard"), bool("enable_color", "Publish color mode"),
    bool("enable_gain", "Publish gain"), bool("enable_rssi", "Publish Wi-Fi signal"), bool("enable_reboot", "Publish reboot action"),
    bool("enable_firmware_version", "Publish firmware version"), bool("enable_firmware_timestamp", "Publish firmware timestamp"),
    ]),
    ...section("Unsupported on this build", [
      { ...bool("doorbell_supported", "Doorbell entity is unsupported", "No verified D-Link A1 doorbell input is published."), readOnly: true },
      { ...bool("ota_supported", "OTA entity is unsupported", "Native HTTPS release checks are not implemented; no curl fallback is used."), readOnly: true },
      { ...number("ota_check_interval", "OTA check interval (seconds)", 1, 604800), readOnly: true, description: "Retained legacy value. It has no effect because OTA release checks are not implemented." },
    ]),
  ],
  saveTransform: buildHomeAssistantUpdate,
};

export function buildHomeAssistantUpdate(value: JsonObject, loaded?: JsonObject): JsonObject {
  const update = structuredClone(value);
  const mqtt = update.mqtt;
  if (typeof mqtt !== "object" || mqtt === null || Array.isArray(mqtt)) throw new TypeError("Home Assistant MQTT configuration is incomplete.");
  delete mqtt.password_set;
  delete update.doorbell_supported;
  delete update.ota_supported;
  delete update.ota_check_interval;
  // GET exposes effective limits for legacy values. Do not overwrite those
  // stored values just because an unrelated field was edited in the form.
  if (loaded) {
    for (const key of ["state_interval", "discovery_interval", "camera_interval"]) {
      if (update[key] === loaded[key]) delete update[key];
    }
  }
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
    element("h2", { text: "Home Assistant connection" }),
    element("p", { text: "Current connection status. Enabling the integration and saving settings starts the connection automatically." }),
    message,
    summary,
    actions,
  );
  rendered.node.append(card);
  let cancelled = false;
  let timer: number | undefined;
  let runtimeLoad: Promise<void> | undefined;
  let confirmed: { enabled: boolean; connected: boolean } | undefined;
  let busy = false;
  let generation = 0;
  const availability = element("p", { attrs: { role: "status" } });
  actions.before(availability);

  function updateActions(): void {
    reconnect.disabled = busy || !confirmed?.enabled;
    discovery.disabled = state.disabled = busy || !confirmed?.enabled || !confirmed.connected;
    availability.textContent = !confirmed ? "Checking Home Assistant status…"
      : !confirmed.enabled ? "Home Assistant integration is disabled. Enable it and save settings to connect."
      : !confirmed.connected ? "Not connected to the MQTT broker. Reconnect is available; publishing requires a connection."
      : "";
  }
  updateActions();

  const cell = (label: string, value: string, detail: string): HTMLElement => {
    const node = element("div", { className: "status-cell" });
    node.append(element("span", { text: label }), element("strong", { text: value }), element("small", { text: detail }));
    return node;
  };
  const timestamp = (value: number | null): string => value === null ? "Never" : new Date(value * 1000).toLocaleString();

  async function readRuntime(): Promise<void> {
    const requestedGeneration = generation;
    try {
      const runtime = await api.homeAssistantRuntime();
      if (cancelled || requestedGeneration !== generation) return;
      confirmed = runtime;
      updateActions();
      summary.replaceChildren(
        cell("Connection", !runtime.enabled ? "Disabled" : runtime.connected ? "Online" : runtime.state, runtime.last_error ?? "No reported error"),
        cell("Last connected", timestamp(runtime.last_connect_unix), runtime.reconnect_in_ms === null ? "No reconnect scheduled" : `Reconnect in ${runtime.reconnect_in_ms} ms`),
        cell("Queue", `${runtime.queue_depth} / 64`, `High-water ${runtime.queue_high_water_mark}`),
        cell("Messages", `${runtime.published_messages} published`, `${runtime.received_commands} commands · ${runtime.rejected_commands} rejected · ${runtime.dropped_messages} dropped`),
      );
      setMessage(message);
    } catch (error) {
      if (!cancelled && requestedGeneration === generation) {
        confirmed = undefined;
        updateActions();
        availability.textContent = "Connection status is unavailable. Actions are disabled until status can be checked.";
        setMessage(message, error instanceof Error ? error.message : "Unable to read Home Assistant runtime.", "error");
      }
    }
  }

  function load(): Promise<void> {
    if (runtimeLoad) return runtimeLoad;
    if (timer !== undefined) window.clearTimeout(timer);
    runtimeLoad = readRuntime().finally(() => {
      runtimeLoad = undefined;
      if (!cancelled) timer = window.setTimeout(() => void load(), 5_000);
    });
    return runtimeLoad;
  }

  const run = async (action: "reconnect" | "republish_discovery" | "publish_state", label: string): Promise<void> => {
    if (busy || !confirmed?.enabled || (action !== "reconnect" && !confirmed.connected)) return;
    busy = true;
    updateActions();
    setMessage(message, `${label} queued…`);
    try {
      await api.homeAssistantAction(action);
      if (!cancelled) {
        setMessage(message, `${label} accepted by the HA worker.`, "success");
        // Finish an older poll before reading the post-action state.
        await runtimeLoad;
        if (!cancelled) await load();
      }
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : `Unable to queue ${label.toLowerCase()}.`, "error");
    } finally {
      busy = false;
      if (!cancelled) updateActions();
    }
  };
  const saved = (event: Event): void => {
    if ((event as CustomEvent<{ endpoint: string }>).detail?.endpoint !== routes.config.homeAssistant) return;
    generation += 1;
    confirmed = undefined;
    updateActions();
    void (async () => {
      await runtimeLoad;
      if (!cancelled) await load();
    })();
  };
  window.addEventListener("thingino:config-saved", saved);
  reconnect.addEventListener("click", () => void run("reconnect", "Reconnect"));
  discovery.addEventListener("click", () => void run("republish_discovery", "Discovery publication"));
  state.addEventListener("click", () => void run("publish_state", "State publication"));
  void load();
  return () => {
    cancelled = true;
    window.removeEventListener("thingino:config-saved", saved);
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

export function renderRecorderPage(client: ApiClient): { node: HTMLElement; cleanup: () => void } {
  const node = element("div");
  const message = statusMessage();
  const selector = element("select", { attrs: { "aria-label": "Recorder stream" } }) as HTMLSelectElement;
  for (const [value, label] of [["0", "Main stream"], ["1", "Sub stream"]] as const) selector.append(element("option", { text: label, attrs: { value } }));
  const holder = element("div");
  const loadingTitle = element("h1", { text: "Recorder" });
  node.append(loadingTitle, message, holder);
  let form: ReturnType<typeof renderConfigForm> | undefined;
  let disposed = false;
  let dirty = false;
  let selected: 0 | 1 = 0;
  function show(channel: 0 | 1): void {
    form?.cleanup();
    const endpoint = routes.recorderStream(channel);
    form = renderConfigForm(client, {
      ...recorder, endpoint,
      description: "Settings apply only to the selected stream. Autostart takes effect at boot. Cleanup removes only this stream's marked closed clips; unmarked files remain. Both streams share SD free space.",
      fields: recorder.fields.map((field) => {
        if (["video.mount", "video.device_path", "video.filename", "video.channel"].includes(field.path)) return { ...field, type: "text", readOnly: true };
        if (field.path === "video.limit") return { ...field, max: 1000, label: "Maximum closed clips" };
        return field;
      }),
      load: async (http) => {
        const result = decodeRecorder(await http.json<unknown>(endpoint));
        if (!("source" in result.data) || result.data.source !== "raptor" || result.data.video.channel !== channel) throw new TypeError("Recorder stream response differs from selection");
        return result;
      },
      onLoaded: (value) => {
        dirty = false;
        setMessage(message, `${value.matches_saved ? "Saved settings verified." : "Saved settings differ or could not be read."} ${value.storage_available ? `${value.free_mb} MiB free on shared SD.` : "SD storage unavailable."} ${value.cleanup_ok ? "" : "Cleanup could not complete."}`);
      },
    });
    form.node.addEventListener("input", () => { dirty = true; });
    form.node.addEventListener("change", () => { dirty = true; });
    loadingTitle.remove();
    holder.replaceChildren(form.node);
  }
  selector.addEventListener("change", () => {
    if (dirty) {
      selector.value = String(selected);
      setMessage(message, "Save or reload this stream before switching. Your edits are still in the form.", "error");
      return;
    }
    selected = selector.value === "1" ? 1 : 0;
    show(selected);
  });
  void client.json<unknown>(routes.recorder).then((raw) => {
    if (disposed) return;
    const response = decodeRecorder(raw);
    if ("source" in response.data && response.data.source === "raptor") {
      node.insertBefore(selector, holder);
      show(0);
    } else {
      form = renderConfigForm(client, recorder);
      loadingTitle.remove();
      holder.replaceChildren(form.node);
    }
  }).catch((error: unknown) => {
    if (!disposed) setMessage(message, error instanceof Error ? error.message : "Unable to load recorder settings.", "error");
  });
  return { node, cleanup: () => { disposed = true; form?.cleanup(); } };
}

export const timelapse: ConfigFormSpec = {
  eyebrow: "Services / timelapse",
  title: "Timelapse",
  description: "Periodic snapshots stored with the camera recordings.",
  endpoint: routes.recorder,
  decode: (value) => {
    const response = decodeRecorder(value);
    if ("source" in response.data && response.data.source === "raptor") throw new TypeError("Timelapse is not supported by this Raptor build.");
    return response;
  },
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


export async function loadTimelapse(client: ApiClient): Promise<JsonObject> {
  try {
    const raw = await client.json<unknown>(routes.recorderTimelapse);
    const envelope = raw as { data?: { domain?: unknown } } | null;
    if (envelope?.data?.domain !== undefined) return decodeRaptorTimelapse(raw);
    const legacy = decodeRecorder(raw);
    if ("source" in legacy.data) {
      throw new TypeError("Timelapse is not supported by this Raptor build.");
    }
    return legacy;
  } catch (error) {
    if (!(error instanceof ApiRequestError) || ![400, 404].includes(error.status)) throw error;
    const result = decodeRecorder(await client.json<unknown>(routes.recorder));
    if ("source" in result.data) {
      throw new TypeError("Timelapse is not supported by this Raptor build.");
    }
    return result;
  }
}

const timelapseErrors: Record<string, string> = {
  privacy_or_settings_changed: "Capture cancelled by Privacy or a settings change.",
  preset_unavailable: "Preset is unavailable. Use manual day/night mode and check the camera controls.",
  preset_apply_failed: "Preset could not be applied.",
  preset_restore_failed: "Preset restoration could not be confirmed. Check the camera controls.",
  cleanup_failed: "Could not remove this attempt's file. New captures are paused.",
  jpeg_unavailable: "Main-stream JPEG is unavailable.",
  capture_failed: "JPEG capture failed.",
  storage_unavailable: "SD storage is unavailable.",
  storage_changed: "SD storage changed during capture.",
  staging_failed: "Image staging failed.",
  publish_failed: "Image publication failed.",
  invalid_saved_config: "Saved settings are invalid.",
  config_unavailable: "Saved settings are unavailable.",
  retention_unavailable: "Old-image cleanup could not complete.",
  invalidated_image_removed: "The cancelled image has been removed.",
};

export function renderTimelapsePage(client: ApiClient): { node: HTMLElement; cleanup: () => void } {
  const node = element("div");
  const title = element("h1", { text: "Timelapse" });
  const message = statusMessage();
  const holder = element("div");
  node.append(title, message, holder);
  let disposed = false;
  let timer: number | undefined;
  let form: ReturnType<typeof renderConfigForm> | undefined;

  function showRuntime(value: JsonObject): void {
    if (disposed) return;
    const runtime = value.runtime as JsonObject;
    const last = typeof runtime.last_success === "number"
      ? new Date(runtime.last_success * 1000).toLocaleString()
      : "Never";
    let detail = runtime.last_error === null ? "" : timelapseErrors[String(runtime.last_error)] ?? "Timelapse needs attention.";
    if (runtime.cleanup_blocked) {
      detail = timelapseErrors.cleanup_failed ?? "Image cleanup needs attention."
    } else if (runtime.preset_restore === "conflict") {
      detail = "Preset restoration is unconfirmed. Captures are paused. Check the camera controls, then save settings to resume.";
    }
    const failed = runtime.last_error !== null || runtime.cleanup_blocked || runtime.preset_restore === "conflict";
    const saved = value.matches_saved ? "Saved settings verified." : "Saved settings are unconfirmed.";
    setMessage(message, `${saved} Completed images: ${runtime.successes}. Last success: ${last}. ${detail}`, failed ? "error" : "success");
  }

  async function pollRuntime(): Promise<void> {
    try {
      const response = decodeRaptorTimelapse(await client.json<unknown>(routes.recorderTimelapse));
      showRuntime(response.data);
    } catch (error) {
      if (!disposed) {
        setMessage(message, error instanceof Error ? error.message : "Unable to read Timelapse runtime.", "error");
      }
    } finally {
      if (!disposed) timer = window.setTimeout(() => void pollRuntime(), 2_000);
    }
  }

  async function initialize(): Promise<void> {
    try {
      const raw = await loadTimelapse(client);
      if (disposed) return;
      const raptor = unwrapRecorder(raw).source === "raptor";
      const endpoint = raptor ? routes.recorderTimelapse : routes.recorder;
      form = renderConfigForm(client, {
        ...timelapse,
        description: raptor
          ? "Captures main-stream JPEG images. Presets require manual day/night mode; visual settling is not verified. Concurrent settings or Privacy changes cancel capture or restoration. Keep days = 0 preserves all completed images."
          : timelapse.description,
        endpoint,
        load: loadTimelapse,
        save: async (api, value) => {
          const receipt = await api.postJson<JsonObject>(endpoint, value);
          if (raptor && (receipt.status !== "accepted" || receipt.persistent !== true)) {
            throw new TypeError("Timelapse save/readback was not confirmed. Reload before retrying.");
          }
        },
        fields: timelapse.fields.map((field) => {
          const fixedPath = ["timelapse.mount", "timelapse.filepath", "timelapse.filename"].includes(field.path);
          return raptor && fixedPath ? { ...field, type: "text", readOnly: true } : field;
        }),
        validate: (_value, loaded) => unwrapRecorder(loaded).available === false
          ? "Timelapse worker is not ready. Reload before saving."
          : undefined,
        onLoaded: (value) => { if (raptor) showRuntime(value); },
      });
      title.remove();
      holder.replaceChildren(form.node);
      // This poll updates only the status node. The form owns its loaded policy and draft.
      if (raptor) timer = window.setTimeout(() => void pollRuntime(), 2_000);
    } catch (error) {
      if (!disposed) {
        setMessage(message, error instanceof Error ? error.message : "Unable to load timelapse settings.", "error");
      }
    }
  }

  void initialize();
  return {
    node,
    cleanup: () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      form?.cleanup();
    },
  };
}
