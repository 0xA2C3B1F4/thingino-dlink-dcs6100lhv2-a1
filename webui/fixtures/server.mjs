import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";

const host = process.env.WEBUI_HOST || "127.0.0.1";
const port = Number(process.env.WEBUI_PORT || 4173);
const root = new URL("../dist/", import.meta.url).pathname;
const jpeg = Buffer.from(
  "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAEf/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EH//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EH//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EH//2Q==",
  "base64",
);

const timezoneOptions = [
  { name: "Etc/UTC", label: "UTC" },
  { name: "Europe/Helsinki", label: "Europe/Helsinki" },
  { name: "Europe/Berlin", label: "Europe/Berlin" },
  { name: "America/New_York", label: "America/New_York" },
];

const initialHeartbeat = () => ({
  time_now: 1787248800,
  uptime: 186420,
  daynight_brightness: 42.5,
  total_gain: 3.25,
  daynight_mode: "day",
  rec_ch0: false,
  rec_ch1: false,
  timelapse_enabled: true,
  motion_enabled: true,
  motion_active: false,
  motion_ingress_ready: true,
  privacy_enabled: false,
  color_mode: 1,
  mic_enabled: true,
  spk_enabled: false,
  daynight_enabled: true,
  ircut_state: 1,
  ir850_state: 0,
  ir940_state: null,
  white_state: null,
});

const state = {
  sessions: new Set(),
  scenario: "normal",
  heartbeat: initialHeartbeat(),
  stats: { mjpeg_started: 0, mjpeg_closed: 0, mjpeg_active: 0, mjpeg_max_active: 0, api_requests: 0 },
  lastMutation: null,
  apiKey: null,
  crontab: "# Fixture schedule\n*/10 * * * * /usr/bin/logger camera\n",
  configs: {
    network: {
      hostname: "dcs6100-a1",
      dns: { primary: "192.0.2.1", secondary: "1.1.1.1" },
      wifi: { ssid: "Quiet Grid Lab", bssid: "", password: null, password_set: true },
      wifi_ap: { enabled: false },
      interfaces: {
        wlan0: { enabled: true, dhcp: true, address: "192.0.2.54", netmask: "255.255.255.0", gateway: "192.0.2.1", broadcast: "192.0.2.255", ipv6: false, mac: "02:00:00:00:00:01", link_up: true },
        eth0: { enabled: false, dhcp: true, address: "", netmask: "", gateway: "", broadcast: "", ipv6: false, mac: "02:00:00:00:00:02", link_up: false },
        usb0: { enabled: false, dhcp: true, address: "", netmask: "", gateway: "", broadcast: "", ipv6: false, mac: "02:00:00:00:00:03", link_up: false },
      },
    },
    time: { timezone: "Europe/Helsinki", timezone_options: timezoneOptions, current_unix_time: Math.floor(Date.now() / 1000), dhcp_ignore_timezone: true, ntp_server_0: "pool.ntp.org", ntp_server_1: "", ntp_server_2: "", ntp_server_3: "" },
    access: { username: "camera", password: null, password_set: true, rtsp_port: 554, rtsp_ch0: "ch0", rtsp_ch1: "ch1", rtsp_mic: "mic", onvif_port: 80, onvif_enabled: true, onvif_ingress: "same-origin" },
    webui: { username: "root", theme: "auto", level: "advanced", paranoid: false, track_focus: true, focus_timeout: 15, auth_bypass_ips: "" },
    admin: { name: "Camera administrator", email: "", telegram: "", discord: "" },
    rsyslog: { host: "", port: 514, enabled: false, file: false },
    daynight: { enabled: true, initial_mode: "", force_mode: "", night_threshold: 28, day_threshold: 42, night_count_threshold: 6, day_count_threshold: 4, sample_interval_ms: 1000, transition_delay_s: 5, loglevel: "INFO", controls: { color: false, ircut: true, ir850: true }, schedule: { enabled: false, start_at: "06:00", stop_at: "22:00" }, sun: { enabled: false, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 0, sunset_offset: 0 } },
    gpio: { profile: "D-Link DCS-6100LHV2 A1", gpio: { ircut: "50 49", ir850: 61 }, led: { startup_indicator: "off" }, available_startup_indicators: "green,red", hardware_io: [
      { gpio: 18, function: "Sensor reset", direction: "output", owner: "sensor driver", read_only: true },
      { gpio: 49, function: "IR-cut coil A", direction: "output", owner: "day/night control", read_only: true },
      { gpio: 50, function: "IR-cut coil B", direction: "output", owner: "day/night control", read_only: true },
      { gpio: 52, function: "Green status LED", direction: "output", owner: "kernel LED class", read_only: true },
      { gpio: 54, function: "Red status LED", direction: "output", owner: "kernel LED class", read_only: true },
      { gpio: 57, function: "Wi-Fi power", direction: "output", owner: "network startup", read_only: true },
      { gpio: 59, function: "SD card detect", direction: "input", owner: "MMC driver", read_only: true },
      { gpio: 60, function: "Reset button", direction: "input", owner: "button service", read_only: true },
      { gpio: 61, function: "850 nm IR LED", direction: "output", owner: "day/night control", read_only: true },
      { gpio: 63, function: "Speaker enable", direction: "output", owner: "audio control", read_only: true },
    ], pwm_pins: "" },
    prudynt: {
      audio: { buffer_cap_frames: 100, buffer_warn_frames: 80, force_stereo: false, mic_agc_compression_gain_db: 9, mic_agc_enabled: true, mic_agc_target_level_dbfs: 10, mic_alc_gain: 4, mic_enabled: true, mic_format: "AAC", mic_gain: 18, mic_high_pass_filter: false, mic_is_digital: false, mic_noise_suppression: 1, mic_vol: 70, spk_enabled: false, spk_gain: 12, spk_vol: 55, tap_enabled: false, tap_path: "/run/prudynt/audio_mic.pcm" },
      image: { brightness: 128, contrast: 128, sharpness: 128, saturation: 128, hue: 128, backlight_compensation: 0, drc_strength: 0, defog_strength: 0, dpc_strength: 64, core_wb_mode: 0, wb_bgain: 128, wb_rgain: 128, ae_compensation: 128, hflip: false, vflip: false },
      stream0: { allow_shared: true, audio_enabled: false, enabled: true, width: 1920, height: 1080, format: "H264", fps: 20, gop: 40, max_gop: 80, mode: "CBR", bitrate: 2400, profile: 2, buffers: 4, rtsp_endpoint: "ch0", rotation: 0, qp_init: -1, qp_max: -1, qp_min: -1 },
      stream1: { allow_shared: true, audio_enabled: false, enabled: false, width: 640, height: 360, format: "H264", fps: 0, gop: 30, max_gop: 60, mode: "CBR", bitrate: 640, profile: 1, buffers: -1, rtsp_endpoint: "ch1", rotation: 0, qp_init: -1, qp_max: -1, qp_min: -1 },
      osd: { burnin: { enabled: true, format: "%F %T", scale: 1, fill_color: "#ffffffff", outline_color: "#000000ff", background_color: "#00000080" }, privacy: { enabled: false }, sei: { enabled: true, entries: { clock: { type: "timestamp", format: "%F %T", position: "-10,-10" }, gain: { type: "gain", format: "%s", position: "10,-10" } } } },
      motion: { enabled: true, sensitivity: 5, playonspeaker: false, cooldown_time: 5, debounce_time: 0, init_time: 5, ivs_polling_timeout: 500, min_time: 1, motor_settle_ms: 0, post_time: 0, frame_width: 640, frame_height: 360, monitor_stream: 1, roi_0_x: 0, roi_0_y: 0, roi_1_x: 640, roi_1_y: 360, roi_count: 12, skip_frame_count: 5, video_length: 10, send2email: false, send2ftp: false, send2gotify: false, send2mqtt: false, send2ntfy: false, send2storage: false, send2telegram: false, send2webhook: false },
      privacy: { enabled: false, stream0_enabled: false, stream1_enabled: false },
    },
    ha: { enabled: false, mqtt: { host: "homeassistant.local", port: 1883, username: "camera", password: null, password_set: true, client_id_prefix: "dcs6100", use_ssl: false, tls_skip_verify: false }, device_name: "Front camera", device_model: "D-Link DCS-6100LHV2 A1", discovery_prefix: "homeassistant", state_interval: 30, discovery_interval: 300, camera_interval: 10, ota_check_interval: 21600, enable_motion: true, enable_motion_guard: true, enable_doorbell: false, enable_live_view: true, enable_daynight: true, enable_privacy: true, enable_snapshot: true, enable_ircut: true, enable_ir850: true, enable_color: true, enable_gain: false, enable_rssi: true, enable_firmware_version: true, enable_firmware_timestamp: true, enable_ota: false, enable_reboot: false, doorbell_supported: false, ota_supported: false },
    send: {
      motion: { send2email: false, send2ftp: false, send2gotify: false, send2mqtt: false, send2ntfy: false, send2storage: false, send2telegram: false, send2webhook: false },
      meta: { mounts: ["/mnt/media"] },
      email: { host: "", port: 587, username: "", password: null, password_set: true, use_ssl: true, trust_cert: false, from_name: "Camera", from_address: "", to_name: "", to_address: "", send_photo: true, send_video: false, subject: "Motion", body: "Motion detected" },
      ftp: { host: "", port: 21, username: "", password: null, password_set: true, path: "", template: "%Y%m%d-%H%M%S", send_photo: true, send_video: false },
      gotify: { enabled: false, url: "", token: null, token_set: true, title: "Motion", message: "Motion detected", extras: "", priority: 5 },
      gphotos: { enabled: false, client_id: "", client_secret: null, client_secret_set: true, refresh_token: null, refresh_token_set: true, album_id: "", description_template: "Motion detected", photo_name_template: "%Y%m%d-%H%M%S.jpg", video_name_template: "%Y%m%d-%H%M%S.mp4", send_photo: true, send_video: false },
      mqtt: { enabled: false, client_id: "dcs6100", host: "", port: 1883, username: "", password: null, password_set: true, use_ssl: false, tls_skip_verify: false, topic: "thingino/motion", message: "motion", send_photo: false, topic_photo: "thingino/motion/photo" },
      ntfy: { enabled: false, host: "ntfy.sh", port: 443, topic: "", use_ssl: true, username: "", password: null, password_set: false, token: null, token_set: true, send_photo: true, send_video: false },
      storage: { enabled: false, mount: "/mnt/media", device_path: "/dev/mmcblk0p1", template: "%Y%m%d-%H%M%S", send_photo: true, send_video: true },
      telegram: { enabled: false, token: null, token_set: true, channel: "", caption: "Motion detected", message: "Motion detected", silent: false, send_photo: true, send_video: false },
      webhook: { enabled: false, url: "", message: "Motion detected", send_photo: false, send_video: false },
    },
  },
  recorder: {
    video: { autostart: false, mount: "/mnt/media", device_path: "/dev/mmcblk0p1", filename: "%Y%m%d-%H%M%S", channel: 0, duration: 300, cleanup_enabled: true, limit: 500, min_free_mb: 256, check_interval: 60 },
    timelapse: { enabled: true, mount: "/mnt/media", filepath: "timelapse", filename: "%Y%m%d-%H%M%S.jpg", interval: 10, keep_days: 14, preset_enabled: false, presets: { ircut: false, ir850: false, ir940: false, white: false, color: true } },
    mounts: [{ path: "/mnt/media", device: "/dev/mmcblk0p1", filesystem: "vfat", writable: true }],
    messages: { recorder: "fixture recorder state" },
    debug: { source: "fixture" },
  },
};

const initialConfigs = structuredClone(state.configs);
const initialRecorder = structuredClone(state.recorder);
const initialCrontab = state.crontab;

const diagnosticQueries = ["crontab", "onvif", "prudynt", "thingino", "logcat", "logread", "dmesg", "lsmod", "netstat", "release", "top", "status", "system"];

function recorderEnvelope() {
  return { ok: true, data: structuredClone(state.recorder) };
}

function json(response, status, value, headers = {}) {
  const body = status === 204 ? "" : JSON.stringify(value);
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  response.end(body);
}

function error(response, status, code, message) {
  json(response, status, { status: "error", error: { code, message } });
}

function requireJson(request, response) {
  if ((request.headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase() === "application/json") return true;
  error(response, 415, "unsupported_media_type", "Content-Type must be application/json");
  return false;
}

function hasRequestBody(request) {
  return Number(request.headers["content-length"] || "0") > 0 || request.headers["transfer-encoding"] !== undefined;
}

async function body(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (!chunks.length) return {};
  const value = Buffer.concat(chunks).toString("utf8");
  try { return JSON.parse(value); } catch { return value; }
}

async function rawBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function cookie(request, name) {
  const match = (request.headers.cookie || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return match?.[1] || null;
}

function merge(target, patch) {
  const next = structuredClone(target);
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === "object" && !Array.isArray(value) && next[key] && typeof next[key] === "object" && !Array.isArray(next[key])) next[key] = merge(next[key], value);
    else next[key] = value;
  }
  return next;
}

function isAuthenticated(request) {
  const token = cookie(request, "thingino_session");
  return token !== null && state.sessions.has(token);
}

function consumeScenario(name) {
  if (state.scenario !== name) return false;
  state.scenario = "normal";
  return true;
}

function imagingResponse() {
  const image = state.configs.prudynt.image;
  const field = (name, min, max, fallback = 0) => ({ supported: true, min, max, value: image[name] ?? fallback, default: fallback });
  return { code: 200, result: "success", message: { fields: {
    brightness: field("brightness", 0, 255, 128),
    contrast: field("contrast", 0, 255, 128),
    saturation: field("saturation", 0, 255, 128),
    sharpness: field("sharpness", 0, 255, 128),
    backlight: { supported: true, min: 0, max: 10, value: image.backlight_compensation ?? 0, default: 0 },
    wide_dynamic_range: { supported: true, min: 0, max: 255, value: image.drc_strength ?? 0, default: 0 },
    tone: { supported: false },
    defog: { supported: true, min: 0, max: 255, value: image.defog_strength ?? 0, default: 0 },
    noise_reduction: { supported: true, min: 0, max: 255, value: image.sinter_strength ?? 0, default: 0 },
  } } };
}

async function serveStatic(response, pathname) {
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const safe = normalize(requested).replace(/^(\.\.[/\\])+/, "");
  const path = join(root, safe);
  try {
    const info = await stat(path);
    if (!info.isFile()) throw new Error("not a file");
    const type = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json" }[extname(path)] || "application/octet-stream";
    response.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" });
    response.end(await readFile(path));
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain" });
    response.end("Not found");
  }
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${request.headers.host || `${host}:${port}`}`);
  try {
    if (url.pathname === "/__fixture__/scenario" && request.method === "POST") {
      const value = await body(request);
      state.scenario = typeof value.kind === "string" ? value.kind : "normal";
      return json(response, 200, { scenario: state.scenario });
    }
    if (url.pathname === "/__fixture__/stats") return json(response, 200, { ...state.stats, last_mutation: state.lastMutation });
    if (url.pathname === "/__fixture__/reset" && request.method === "POST") {
      state.scenario = "normal";
      state.sessions.clear();
      state.heartbeat = initialHeartbeat();
      state.stats = { mjpeg_started: 0, mjpeg_closed: 0, mjpeg_active: 0, mjpeg_max_active: 0, api_requests: 0 };
      state.lastMutation = null;
      state.configs = structuredClone(initialConfigs);
      state.recorder = structuredClone(initialRecorder);
      state.crontab = initialCrontab;
      return json(response, 200, { reset: true });
    }

    if (url.pathname === "/api/v1/auth/session") {
      const authenticated = isAuthenticated(request);
      return json(response, 200, { authenticated, username: authenticated ? "root" : null, is_default_password: authenticated && state.scenario === "default-password", client_ip: request.socket.remoteAddress || "127.0.0.1", control_api: { name: "Thingino Control", version: 1 } });
    }
    if (url.pathname === "/api/v1/auth/login" && request.method === "POST") {
      if (!requireJson(request, response)) return;
      const value = await body(request);
      if (value.username !== "root" || value.password !== "thingino") return error(response, 401, "invalid_credentials", "invalid username or password");
      const token = `fixture-${Date.now()}-${Math.random()}`;
      state.sessions.add(token);
      return json(response, 200, { success: true, is_default_password: state.scenario === "default-password" }, { "Set-Cookie": `thingino_session=${token}; Path=/; Max-Age=86400; HttpOnly; SameSite=Strict` });
    }
    if (url.pathname === "/api/v1/auth/logout" && request.method === "POST") {
      const token = cookie(request, "thingino_session");
      if (token) state.sessions.delete(token);
      return json(response, 204, null, { "Set-Cookie": "thingino_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" });
    }

    if (url.pathname.startsWith("/api/v1/") || url.pathname.startsWith("/media/v1/")) {
      state.stats.api_requests += 1;
      if (!isAuthenticated(request) || consumeScenario("unauthorized_once")) return error(response, 401, "unauthorized", "authentication required");
      if (consumeScenario("slow_once")) await new Promise((resolve) => setTimeout(resolve, 11_000));
      if (consumeScenario("disconnect_once")) return request.socket.destroy();
      if (consumeScenario("save_error_once") && request.method !== "GET") return error(response, 400, "invalid_request", "fixture rejected the submitted configuration");
    }

    if (url.pathname === "/api/v1/auth/password" && request.method === "POST") {
      if (!requireJson(request, response)) return;
      const value = await body(request);
      if (!value.password) return error(response, 400, "invalid_request", "password is required");
      return json(response, 200, { status: "ok" });
    }

    if (url.pathname === "/api/v1/runtime/heartbeat") {
      if (consumeScenario("heartbeat_unavailable_once")) return error(response, 503, "service_unavailable", "backend is unavailable");
      return json(response, 200, state.heartbeat);
    }
    if (url.pathname === "/api/v1/runtime/motion") return json(response, 200, {
      version: 1, ingress_ready: true, monitoring: state.heartbeat.motion_enabled,
      active: state.heartbeat.motion_active, channel: 0, producer_pid: 42,
      producer_sequence: 7, last_observation_monotonic_ms: 1234,
      last_transition_unix_ms: null,
      queue: { capacity: 32, depth: 0, high_water: 2, dropped: 0, coalesced: 0 },
      received: 7, rejected: 0, accepted: 7, duplicate_or_replayed: 0,
      producer_restarts: 0, transitions: 0,
      sink: { queued: 0, coalesced: 0, dropped: 0, disabled: 1 },
      speaker: { queued: 0, dropped: 0 },
      clips: { capacity: 4, pending: 0, requested: 0, ready: 0, dropped: 0, recovered: 0, manifest_errors: 0 },
      unsupported_destination_events: 0,
    });
    if (url.pathname === "/api/v1/runtime/ha") return json(response, 200, {
      enabled: state.configs.ha.enabled,
      state: state.configs.ha.enabled ? "online" : "disabled",
      connected: state.configs.ha.enabled,
      last_connect_unix: state.configs.ha.enabled ? 1_787_480_000 : null,
      last_disconnect_unix: null,
      last_error: null,
      reconnect_in_ms: null,
      queue_depth: 0,
      queue_high_water_mark: 3,
      published_messages: 42,
      received_commands: 3,
      rejected_commands: 1,
      dropped_messages: 0,
    });
    if (url.pathname === "/api/v1/runtime/media") return json(response, 200, { streams: {
      ch0: { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=0" },
      ch1: { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=1" },
    } });
    if (url.pathname === "/api/v1/runtime/system") return json(response, 200, { code: 200, result: "success", data: {
      network: { online: true, ip: "192.0.2.54", interfaces: state.configs.network.interfaces },
      memory: { total: 65536, free: 24576, active: 24576, buffers: 4096, cached: 12288, used: 40960 },
      overlay: { total: 8192, free: 3584, used: 4608 }, extras: { total: 31166976, free: 24576000, used: 6590976 },
      media: { prudynt_running: true, media_ready: true, stream0_enabled: true, stream1_enabled: true }, timestamp: 1787248800,
    } });
    if (url.pathname === "/api/v1/runtime/sensor" || url.pathname === "/api/v1/sensor/iq") {
      if (request.method === "GET" && !hasRequestBody(request)) return json(response, 200, { sensor_model: "os02g10", soc_model: "t31n", soc_family: "t", file_path: "/usr/share/sensor/os02g10-t31n.bin", md5: "not computed in request path" });
      return error(response, 400, "invalid_request", "sensor metadata is read-only");
    }
    if (url.pathname === "/api/v1/runtime/daynight/sensors") return json(response, 200, { night_threshold_pct: 28, day_threshold_pct: 42, current: { brightness: 42.5, total_gain: 3.25 } });
    if (url.pathname === "/api/v1/runtime/daynight/history") return json(response, 200, [{ ...state.heartbeat, time_now: state.heartbeat.time_now - 60 }]);

    if (url.pathname === "/api/v1/config/access") {
      if (request.method === "GET") return json(response, 200, state.configs.access);
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      const changed = typeof patch.password === "string" && patch.password.length > 0;
      for (const key of ["username", "rtsp_port", "rtsp_ch0", "rtsp_ch1", "rtsp_mic"]) {
        if (patch[key] !== undefined) state.configs.access[key] = patch[key];
      }
      if (changed) state.configs.access.password_set = true;
      return json(response, 200, { status: "ok", password_changed: changed });
    }
    if (url.pathname === "/api/v1/config/crontab") {
      if (request.method === "GET" && !hasRequestBody(request)) return json(response, 200, { content: state.crontab, max_bytes: 16384 });
      if (request.method !== "POST" || !hasRequestBody(request)) return error(response, 400, "invalid_request", "crontab requires an empty GET or a non-empty POST");
      if (!requireJson(request, response)) return;
      const value = await body(request);
      if (typeof value.content !== "string" || Object.keys(value).some((key) => key !== "content") || Buffer.byteLength(value.content) > 16384) return error(response, 400, "invalid_request", "invalid crontab content");
      state.crontab = value.content;
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(value) };
      return json(response, 200, { status: "ok" });
    }
    if (url.pathname === "/api/v1/config/network") {
      if (request.method === "GET") return json(response, 200, state.configs.network);
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      const suppliedPassword = typeof patch.wifi?.password === "string" && patch.wifi.password.length > 0;
      if (patch.wifi) delete patch.wifi.password;
      state.configs.network = merge(state.configs.network, patch);
      state.configs.network.wifi.password = null;
      if (suppliedPassword) state.configs.network.wifi.password_set = true;
      return json(response, 200, { status: "ok" });
    }
    const configMatch = url.pathname.match(/^\/api\/v1\/config\/(time|webui|admin|rsyslog|daynight|gpio|ha)$/);
    if (configMatch) {
      const key = configMatch[1];
      if (request.method === "GET") {
        if (key === "time") return json(response, 200, { ...state.configs.time, current_unix_time: Math.floor(Date.now() / 1000) });
        return json(response, 200, state.configs[key]);
      }
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      if (key === "gpio") {
        if (Object.keys(patch).length !== 1 || !["off", "green", "red"].includes(patch.startup_indicator)) return error(response, 400, "invalid_request", "invalid D-Link A1 GPIO update");
        state.configs.gpio.led.startup_indicator = patch.startup_indicator;
        return json(response, 200, { status: "ok" });
      }
      if (key === "ha" && patch.mqtt) {
        const suppliedPassword = typeof patch.mqtt.password === "string" && patch.mqtt.password.length > 0;
        delete patch.mqtt.password;
        if (suppliedPassword) state.configs.ha.mqtt.password_set = true;
      }
      if (key === "time") {
        if (patch.action !== "update" || typeof patch.timezone !== "string" || !timezoneOptions.some((option) => option.name === patch.timezone)) return error(response, 400, "invalid_request", "invalid timezone update");
        delete patch.action;
        state.configs.time = merge(state.configs.time, patch);
        state.configs.time.current_unix_time = Math.floor(Date.now() / 1000);
        return json(response, 200, { status: "ok" });
      }
      state.configs[key] = merge(state.configs[key], patch);
      if (key === "ha") state.configs.ha.mqtt.password = null;
      return json(response, 200, { status: "ok" });
    }
    if (url.pathname === "/api/v1/services/send/config") {
      if (request.method === "GET") return json(response, 200, state.configs.send);
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      for (const [domain, config] of Object.entries(patch)) {
        if (!config || typeof config !== "object" || Array.isArray(config)) continue;
        for (const key of ["password", "token", "client_secret", "refresh_token"]) {
          if (typeof config[key] === "string" && config[key].length > 0) {
            delete config[key];
            if (state.configs.send[domain]) state.configs.send[domain][`${key}_set`] = true;
          }
        }
      }
      state.configs.send = merge(state.configs.send, patch);
      for (const config of Object.values(state.configs.send)) {
        if (!config || typeof config !== "object" || Array.isArray(config)) continue;
        for (const key of ["password", "token", "client_secret", "refresh_token"]) if (`${key}_set` in config) config[key] = null;
      }
      return json(response, 200, state.configs.send);
    }
    if (url.pathname === "/api/v1/webui/api-key") {
      if (request.method === "GET") return json(response, 200, state.apiKey ? { exists: true, api_key: state.apiKey } : { exists: false });
      if (request.method === "POST") { state.apiKey = `fixture-key-${Date.now()}`; return json(response, 200, { api_key: state.apiKey, generated: true }); }
      if (request.method === "DELETE") { state.apiKey = null; return json(response, 200, { deleted: true }); }
    }
    const prudyntMatch = url.pathname.match(/^\/api\/v1\/prudynt\/(audio|image|stream0|stream1|osd|motion|privacy)$/);
    if (prudyntMatch && request.method === "GET") return json(response, 200, state.configs.prudynt[prudyntMatch[1]]);
    if (url.pathname === "/api/v1/prudynt" && request.method === "POST") {
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      for (const [domain, value] of Object.entries(patch)) state.configs.prudynt[domain] = merge(state.configs.prudynt[domain] || {}, value);
      return json(response, 200, { status: "ok" });
    }
    if (url.pathname === "/api/v1/recorder") {
      if (request.method === "GET") return json(response, 200, recorderEnvelope());
      const patch = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(patch) };
      state.recorder = merge(state.recorder, patch);
      return json(response, 200, recorderEnvelope());
    }
    if (url.pathname === "/api/v1/actions/control" && request.method === "POST") {
      const command = await body(request);
      if (command.motion) state.heartbeat.motion_enabled = Boolean(command.motion.enabled);
      if (command.privacy) state.heartbeat.privacy_enabled = Boolean(command.privacy.enabled);
      if (command.audio?.mic_enabled !== undefined) state.heartbeat.mic_enabled = Boolean(command.audio.mic_enabled);
      if (command.audio?.spk_enabled !== undefined) state.heartbeat.spk_enabled = Boolean(command.audio.spk_enabled);
      if (command.mp4?.start) state.heartbeat[`rec_ch${command.mp4.start.channel}`] = true;
      if (command.mp4?.stop) state.heartbeat[`rec_ch${command.mp4.stop.channel}`] = false;
      if (command.cmd === "color") state.heartbeat.color_mode = Number(command.val) ? 1 : 0;
      if (command.cmd === "ircut") state.heartbeat.ircut_state = Number(command.val) ? 1 : 0;
      if (command.cmd === "ir850") state.heartbeat.ir850_state = Number(command.val) ? 1 : 0;
      return json(response, 200, { status: "ok" });
    }
    if (url.pathname === "/api/v1/actions/daynight" && request.method === "POST") {
      const command = await body(request);
      state.heartbeat.daynight_enabled = command.mode === "auto";
      state.heartbeat.daynight_mode = command.mode === "auto" ? "unknown" : command.mode;
      state.configs.daynight.enabled = command.mode === "auto";
      state.configs.daynight.force_mode = command.mode === "auto" ? "" : command.mode;
      return json(response, 200, { status: "accepted", mode: command.mode });
    }
    if (url.pathname === "/api/v1/actions/ha" && request.method === "POST") {
      const command = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(command) };
      return json(response, 200, { status: "accepted", action: command.action });
    }
    if (url.pathname === "/api/v1/actions/snapshot") {
      response.writeHead(200, { "Content-Type": "image/jpeg", "Content-Disposition": `attachment; filename="snapshot-ch${url.searchParams.get("stream_id") || "0"}.jpg"` });
      return response.end(jpeg);
    }
    if (url.pathname === "/media/v1/mjpeg") {
      if (consumeScenario("stream_error_once")) return error(response, 503, "service_unavailable", "fixture stream is unavailable");
      state.stats.mjpeg_started += 1;
      state.stats.mjpeg_active += 1;
      state.stats.mjpeg_max_active = Math.max(state.stats.mjpeg_max_active, state.stats.mjpeg_active);
      response.writeHead(200, { "Content-Type": "multipart/x-mixed-replace; boundary=frame", "Cache-Control": "no-store" });
      const writeFrame = () => {
        if (!response.destroyed) response.write(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`), response.write(jpeg), response.write("\r\n");
      };
      writeFrame();
      const interval = setInterval(writeFrame, 400);
      request.on("close", () => { clearInterval(interval); state.stats.mjpeg_closed += 1; state.stats.mjpeg_active = Math.max(0, state.stats.mjpeg_active - 1); });
      return;
    }
    if (url.pathname === "/media/v1/file") {
      response.writeHead(200, { "Content-Type": "text/plain", "Content-Disposition": "attachment" });
      return response.end("fixture media file\n");
    }
    if (url.pathname === "/api/v1/storage/overlay") return json(response, 200, { usage: { label: "56%", percent: 56, state: "primary" }, listing_base64: Buffer.from("/overlay/etc\n/overlay/var\n").toString("base64"), path: "/overlay" });
    if (url.pathname === "/api/v1/storage/sd") return json(response, 200, { ok: true, data: { has_sdcard: true, device: { name: "mmcblk0", node: "/dev/mmcblk0", vendor: "", model: "", size_bytes: 31914983424 }, reports: { partitions_b64: "", mounts_b64: Buffer.from("/dev/mmcblk0p1 /mnt/mmcblk0p1 vfat rw\n").toString("base64") }, format: { supported: true, options: [{ id: "fat32", label: "FAT32", description: "Best compatibility for camera recordings." }], status: "idle", last_output_b64: "" }, filesystems: [{ device: "/dev/mmcblk0p1", mountpoint: "/mnt/mmcblk0p1", filesystem: "vfat", writable: true, total_kib: 31166976, used_kib: 1048576, free_kib: 30118400 }], messages: { format_warning: "Formatting permanently erases the selected partition.", not_present: "Insert or reseat the SD card to manage it here." }, debug: { detection: "mount-table" } } });
    if (url.pathname === "/api/v1/files" && request.method === "POST" && url.searchParams.has("rm")) {
      state.lastMutation = { method: request.method, path: `${url.pathname}${url.search}`, body: await rawBody(request) };
      return json(response, 200, { result: "ok" });
    }
    if (url.pathname === "/api/v1/files" && request.method === "GET") {
      const directory = url.searchParams.get("cd") || "/";
      const rootDirectory = directory === "/";
      return json(response, 200, { directory, parent: rootDirectory ? "/" : "/", breadcrumbs: [{ label: "Home", path: "/" }], entries: rootDirectory
        ? [{ name: "media", path: "/mnt/media", size: "-", perm: "0755", time: "1787248800", is_dir: true, is_link: false, link_target: "", deletable: false }]
        : [{ name: "recordings", path: "/mnt/media/recordings", size: "-", perm: "0755", time: "1787248800", is_dir: true, is_link: false, link_target: "", deletable: false }, { name: "camera-notes.txt", path: "/mnt/media/camera-notes.txt", size: "128", perm: "0644", time: "1787248800", is_dir: false, is_link: false, link_target: "", deletable: true }] });
    }
    if (url.pathname === "/api/v1/files/text") {
      if (request.method === "GET") return json(response, 200, { file: url.searchParams.get("file"), content: Buffer.from("Fixture camera notes").toString("base64"), content_encoding: "base64", size: 20, lines: 0, writable: true });
      const content = await rawBody(request);
      state.lastMutation = { method: request.method, path: `${url.pathname}${url.search}`, body: content };
      return json(response, 200, { success: true, file: url.searchParams.get("file"), size: Buffer.byteLength(content), lines: content ? content.split("\n").length : 0 });
    }
    if (url.pathname === "/api/v1/diagnostics/info") {
      const query = url.searchParams.keys().next().value || "status";
      if (!diagnosticQueries.includes(query)) return error(response, 400, "invalid_request", "diagnostic query is not allowlisted");
      return json(response, 200, { commands: [{ command: query, output_base64: Buffer.from(`fixture ${query}: all systems nominal.\n`).toString("base64") }], extras_html_base64: "" });
    }
    if (url.pathname === "/api/v1/diagnostics" && request.method === "POST") {
      const value = await body(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: structuredClone(value) };
      return json(response, 200, { output_b64: Buffer.from("Fixture diagnostic bundle prepared.\n").toString("base64") });
    }
    if (url.pathname === "/api/v1/network/probe" && request.method === "GET") return json(response, 200, { actions: [{ id: "resolve", label: "DNS resolve", description: "Resolve a host with the system resolver." }, { id: "connect", label: "TCP connect", description: "Resolve a host and measure a bounded TCP connection." }], interfaces: ["wlan0"], defaults: { action: "resolve", interface: "auto", packet_size: 56, count: 1 }, limits: { packet_size: { min: 1, max: 65507 }, count: { min: 1, max: 1 } } });
    if (url.pathname === "/api/v1/network/probe" && request.method === "POST") {
      const value = await rawBody(request);
      state.lastMutation = { method: request.method, path: url.pathname, body: value };
      const params = new URLSearchParams(value);
      const command = `${params.get("action") || "resolve"} ${params.get("target") || "camera.local"}`;
      return json(response, 200, { command, success: true, output_b64: Buffer.from(`${command}\nAddress: 192.0.2.54\n`).toString("base64") });
    }
    if (url.pathname === "/api/v1/network/wifi-scan") return json(response, 200, { networks: [{ ssid: "Quiet Grid Lab", bssid: "02:00:00:00:00:11", frequency: 2412, signal: -47, security: "WPA2" }] });
    if (url.pathname === "/api/v1/imaging" && request.method === "GET") return json(response, 200, imagingResponse());
    if (url.pathname === "/api/v1/imaging" && request.method === "POST") {
      state.lastMutation = { method: request.method, path: url.pathname, body: await body(request) };
      return json(response, 200, imagingResponse());
    }
    if (url.pathname === "/api/v1/actions/time/sync") {
      if (request.method === "POST" && !hasRequestBody(request)) return json(response, 200, { status: "ok", message: "Time synchronized" });
      return error(response, 400, "invalid_request", "time sync requires an empty POST");
    }
    if (url.pathname === "/api/v1/actions/prudynt/restart") return json(response, 200, { status: "ok" });
    if (url.pathname === "/api/v1/actions/reboot") {
      if (request.method === "POST" && !hasRequestBody(request)) return json(response, 200, { status: "accepted", message: "Reboot scheduled" });
      return error(response, 400, "invalid_request", "reboot requires an empty POST");
    }
    if (url.pathname === "/api/v1/actions/factory-reset") { const value = await body(request); return json(response, 200, { status: "accepted", action: value.action, reboot: true }); }
    if (url.pathname === "/api/v1/health") return json(response, 200, { status: "ok", healthy: true, control_api: { name: "Thingino Control", version: 1 }, backend: { name: "Prudynt", available: true }, checks: { media: "ready" } });
    if (url.pathname.startsWith("/api/v1/") || url.pathname.startsWith("/media/v1/")) return error(response, 404, "not_found", "fixture route not found");
    return serveStatic(response, url.pathname);
  } catch (failure) {
    return error(response, 400, "invalid_request", failure instanceof Error ? failure.message : "invalid fixture request");
  }
});

server.listen(port, host, () => console.log(`fixture WebUI listening on http://${host}:${port}`));
