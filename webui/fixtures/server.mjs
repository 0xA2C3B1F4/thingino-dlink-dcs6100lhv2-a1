import { createServer } from "node:http";
import { readFile, stat, writeFile } from "node:fs/promises";
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
    access: { username: "viewer", password: null, password_set: true, rtsp_port: 554, rtsp_ch0: "ch0", rtsp_ch1: "ch1", rtsp_mic: "mic", onvif_port: 80, onvif_enabled: true, onvif_ingress: "same-origin" },
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
    if (url.pathname === "/__fixture__/native-media" && process.env.RAPTOR_BROWSER_ROOT) {
      const { kind } = await body(request);
      if (!["main-only", "failed-sub", "missing-main-jpeg", "missing-sub-jpeg", "both"].includes(kind)) return error(response, 400, "invalid_request", "unknown native media case");
      await writeFile(join(process.env.RAPTOR_BROWSER_ROOT, "media-case"), kind);
      return json(response, 200, { status: "ok" });
    }
    if (url.pathname === "/__fixture__/reset" && request.method === "POST") {
      state.scenario = "normal";
      state.sessions.clear();
      state.heartbeat = initialHeartbeat();
      state.stats = { mjpeg_started: 0, mjpeg_closed: 0, mjpeg_active: 0, mjpeg_max_active: 0, api_requests: 0 };
      state.lastMutation = null;
      delete state.raptorStreams;
      delete state.motionEmail;
      delete state.motionFtp;
      delete state.motionGotify;
      delete state.motionTelegram;
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

    if (state.scenario === "raptor-native" && process.env.RAPTOR_BROWSER_ROOT && [
      "/api/v1/actions/control", "/api/v1/runtime/heartbeat", "/api/v1/runtime/motion",
      "/api/v1/runtime/media", "/api/v1/prudynt/stream0", "/api/v1/prudynt/stream1", "/api/v1/prudynt/audio", "/api/v1/prudynt", "/api/v1/imaging", "/api/v1/prudynt/motion", "/api/v1/prudynt/privacy", "/api/v1/prudynt/osd", "/api/v1/config/daynight", "/api/v1/config/time",
      "/api/v1/config/motion-webhook", "/api/v1/runtime/motion-webhook",
    ].includes(url.pathname)) {
      const address = (await readFile(join(process.env.RAPTOR_BROWSER_ROOT, "address"), "utf8")).trim();
      if (!/^127\.0\.0\.1:[0-9]+$/.test(address)) throw new Error("invalid host bridge address");
      const reply = await fetch(`http://${address}${url.pathname}`, {
        method: request.method,
        headers: { Authorization: "Bearer host-fixture-token", "Content-Type": "application/json" },
        ...(request.method === "POST" ? { body: await rawBody(request) } : {}),
        signal: AbortSignal.timeout(3000),
      });
      return json(response, reply.status, await reply.json());
    }
    if (state.scenario.startsWith("raptor")) {
      const path = url.pathname;
      state.raptorStreams ??= [
        { format: "H264", profile: 2, gop: 40, gop_mode: "DEFAULT", gop_saved_mode: "DEFAULT", fps: 20, mode: "CBR", bitrate: 2_400_000, rc_saved_mode: "CBR", rc_saved_qp: 35 },
        { format: "H264", profile: 1, gop: 30, gop_mode: "DEFAULT", gop_saved_mode: "DEFAULT", fps: 15, mode: "CBR", bitrate: 640_000, rc_saved_mode: "CBR", rc_saved_qp: 35 },
      ];
      if (path === "/api/v1/config/access") {
        state.raptorAccess ??= { source: "raptor", auth_enabled: true, username: "viewer", password: null, password_set: true, rtsp_port: 8554, rtsp_ch0: "stream0", rtsp_ch1: "stream1", rtsp_mic: "audio", onvif_port: null, onvif_enabled: null, onvif_ingress: null };
        if (request.method === "GET") return json(response, 200, state.raptorAccess);
        const update = await body(request);
        for (const key of ["username", "rtsp_port", "rtsp_ch0", "rtsp_ch1"]) {
          if (update[key] !== undefined) state.raptorAccess[key] = update[key];
        }
        if (state.scenario === "raptor-access-retry" && !state.raptorAccessRetried) {
          state.raptorAccessRetried = true;
          return error(response, 503, "partial_apply", "RTSP access may have changed, but save was incomplete. Reload and explicitly retry saving.");
        }
        return json(response, 200, { status: "accepted", persistent: true });
      }
      if (path === "/api/v1/prudynt/motion" && request.method === "GET") {
        state.raptorMotionSaved ??= false;
        const enabled = state.heartbeat.motion_enabled;
        return json(response, 200, { source: "raptor", persistent: true, supported: true, available: true,
          enabled, saved_enabled: state.raptorMotionSaved, matches_saved: enabled === state.raptorMotionSaved });
      }
      if (path === "/api/v1/config/motion-email") {
        state.motionEmail ??= {
          saved_enabled: false, enabled: false,
          host: "", live_host: "", port: 587, live_port: 587,
          tls_mode: "starttls", live_tls_mode: "starttls",
          username: "", live_username: "",
          password: null, password_set: false, live_password_set: false,
          from_address: "", live_from_address: "",
          to_address: "", live_to_address: "",
          matches_saved: true,
          transport_available: state.scenario !== "raptor-email-unavailable",
        };
        if (state.scenario === "raptor-email-mismatch") {
          Object.assign(state.motionEmail, {
            saved_enabled: true, enabled: false,
            host: "saved.smtp.example.test", live_host: "live.smtp.example.test",
            from_address: "camera@example.test", live_from_address: "camera@example.test",
            to_address: "owner@example.test", live_to_address: "owner@example.test",
            matches_saved: false,
          });
        }
        if (request.method === "GET") return json(response, 200, state.motionEmail);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "host", "port", "tls_mode", "username", "password", "clear_password", "from_address", "to_address"].includes(name)) ||
            (update.host !== undefined && typeof update.host !== "string") ||
            (update.port !== undefined && (!Number.isInteger(update.port) || update.port < 1 || update.port > 65535)) ||
            (update.tls_mode !== undefined && !["starttls", "implicit"].includes(update.tls_mode)) ||
            (update.username !== undefined && typeof update.username !== "string") ||
            (update.password !== undefined && typeof update.password !== "string") ||
            (update.clear_password !== undefined && typeof update.clear_password !== "boolean") ||
            (update.from_address !== undefined && typeof update.from_address !== "string") ||
            (update.to_address !== undefined && typeof update.to_address !== "string")) {
          return error(response, 400, "invalid_request", "invalid Motion Email update");
        }
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        if (state.scenario === "raptor-email-failure") {
          return error(response, 503, "partial_apply", "Email settings were not applied. Other Motion controls remain available.");
        }
        for (const name of ["host", "port", "tls_mode", "username", "from_address", "to_address"]) {
          if (update[name] !== undefined) {
            state.motionEmail[name] = update[name];
            state.motionEmail[`live_${name}`] = update[name];
          }
        }
        if (typeof update.password === "string" && update.password.length > 0) {
          state.motionEmail.password_set = true;
          state.motionEmail.live_password_set = true;
        }
        if (update.clear_password === true) {
          state.motionEmail.password_set = false;
          state.motionEmail.live_password_set = false;
        }
        state.motionEmail.enabled = update.enabled;
        state.motionEmail.saved_enabled = update.enabled;
        state.motionEmail.matches_saved = true;
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-email" && request.method === "GET") {
        return json(response, 200, {
          running: true, enabled: state.motionEmail?.enabled ?? false,
          transport_available: state.scenario !== "raptor-email-unavailable",
          queue_capacity: 2, queue_depth: 0, queue_dropped: 0,
          requests: 0, successes: 0, failures: 0,
          last_result: "idle", last_smtp_status: null,
        });
      }
      if (path === "/api/v1/config/motion-ftp") {
        state.motionFtp ??= {
          saved_enabled: false, enabled: false,
          host: "", live_host: "", port: 21, live_port: 21,
          tls_mode: "explicit", live_tls_mode: "explicit",
          username: "", live_username: "",
          password: null, password_set: false, live_password_set: false,
          path: "", live_path: "",
          matches_saved: true,
          transport_available: state.scenario !== "raptor-ftp-unavailable",
        };
        if (state.scenario === "raptor-ftp-mismatch") {
          Object.assign(state.motionFtp, {
            saved_enabled: true, enabled: false,
            host: "saved.ftp.example.test", live_host: "live.ftp.example.test",
            username: "fixture-camera", live_username: "fixture-camera",
            path: "saved/events", live_path: "live/events",
            matches_saved: false,
          });
        }
        if (request.method === "GET") return json(response, 200, state.motionFtp);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "host", "port", "tls_mode", "username", "password", "clear_password", "path"].includes(name)) ||
            (update.host !== undefined && typeof update.host !== "string") ||
            (update.port !== undefined && (!Number.isInteger(update.port) || update.port < 1 || update.port > 65535)) ||
            (update.tls_mode !== undefined && update.tls_mode !== "explicit") ||
            (update.username !== undefined && typeof update.username !== "string") ||
            (update.password !== undefined && typeof update.password !== "string") ||
            (update.clear_password !== undefined && typeof update.clear_password !== "boolean") ||
            (update.path !== undefined && typeof update.path !== "string")) {
          return error(response, 400, "invalid_request", "invalid Motion FTP update");
        }
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        if (state.scenario === "raptor-ftp-failure") {
          return error(response, 503, "partial_apply", "FTP settings were not applied. Other Motion controls remain available.");
        }
        for (const name of ["host", "port", "tls_mode", "username", "path"]) {
          if (update[name] !== undefined) {
            state.motionFtp[name] = update[name];
            state.motionFtp[`live_${name}`] = update[name];
          }
        }
        if (typeof update.password === "string" && update.password.length > 0) {
          state.motionFtp.password_set = true;
          state.motionFtp.live_password_set = true;
        }
        if (update.clear_password === true) {
          state.motionFtp.password_set = false;
          state.motionFtp.live_password_set = false;
        }
        state.motionFtp.enabled = update.enabled;
        state.motionFtp.saved_enabled = update.enabled;
        state.motionFtp.matches_saved = true;
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-ftp" && request.method === "GET") {
        return json(response, 200, {
          running: true, enabled: state.motionFtp?.enabled ?? false,
          transport_available: state.scenario !== "raptor-ftp-unavailable",
          queue_capacity: 2, queue_depth: 0, queue_dropped: 0,
          captures: 0, requests: 0, successes: 0, failures: 0, cancellations: 0,
          last_result: "idle", last_ftp_status: null,
        });
      }
      if (path === "/api/v1/config/motion-gotify") {
        state.motionGotify ??= { saved_enabled: false, enabled: false, endpoint: null, endpoint_set: false, live_endpoint_set: false, token: null, token_set: false, live_token_set: false, matches_saved: true, transport_available: true };
        if (request.method === "GET") return json(response, 200, state.motionGotify);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "endpoint", "token", "clear_endpoint", "clear_token"].includes(name)) ||
            (update.endpoint !== undefined && (typeof update.endpoint !== "string" || !/^https?:\/\//.test(update.endpoint))) ||
            (update.token !== undefined && typeof update.token !== "string") ||
            (update.clear_endpoint !== undefined && typeof update.clear_endpoint !== "boolean") ||
            (update.clear_token !== undefined && typeof update.clear_token !== "boolean")) {
          return error(response, 400, "invalid_request", "invalid Motion Gotify update");
        }
        if (update.endpoint) state.motionGotify.endpoint_set = state.motionGotify.live_endpoint_set = true;
        if (update.token) state.motionGotify.token_set = state.motionGotify.live_token_set = true;
        if (update.clear_endpoint) state.motionGotify.endpoint_set = state.motionGotify.live_endpoint_set = false;
        if (update.clear_token) state.motionGotify.token_set = state.motionGotify.live_token_set = false;
        state.motionGotify.enabled = update.enabled;
        state.motionGotify.saved_enabled = update.enabled;
        state.motionGotify.matches_saved = true;
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-gotify" && request.method === "GET") {
        return json(response, 200, { running: true, enabled: state.motionGotify?.enabled ?? false, transport_available: true,
          queue_capacity: 2, queue_depth: 0, queue_dropped: 0, requests: 0, successes: 0, failures: 0,
          last_result: "idle", last_http_status: null });
      }
      if (path === "/api/v1/config/motion-telegram") {
        state.motionTelegram ??= { saved_enabled: false, enabled: false, bot_token: null, bot_token_set: false, live_bot_token_set: false, chat_id: null, chat_id_set: false, live_chat_id_set: false, matches_saved: true, transport_available: true };
        if (request.method === "GET") return json(response, 200, state.motionTelegram);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "bot_token", "chat_id", "clear_bot_token", "clear_chat_id"].includes(name)) ||
            (update.bot_token !== undefined && typeof update.bot_token !== "string") ||
            (update.chat_id !== undefined && typeof update.chat_id !== "string") ||
            (update.clear_bot_token !== undefined && typeof update.clear_bot_token !== "boolean") ||
            (update.clear_chat_id !== undefined && typeof update.clear_chat_id !== "boolean")) {
          return error(response, 400, "invalid_request", "invalid Motion Telegram update");
        }
        if (update.bot_token) state.motionTelegram.bot_token_set = state.motionTelegram.live_bot_token_set = true;
        if (update.chat_id) state.motionTelegram.chat_id_set = state.motionTelegram.live_chat_id_set = true;
        if (update.clear_bot_token) state.motionTelegram.bot_token_set = state.motionTelegram.live_bot_token_set = false;
        if (update.clear_chat_id) state.motionTelegram.chat_id_set = state.motionTelegram.live_chat_id_set = false;
        state.motionTelegram.enabled = update.enabled;
        state.motionTelegram.saved_enabled = update.enabled;
        state.motionTelegram.matches_saved = true;
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-telegram" && request.method === "GET") {
        return json(response, 200, { running: true, enabled: state.motionTelegram?.enabled ?? false, transport_available: true,
          queue_capacity: 2, queue_depth: 0, queue_dropped: 0, requests: 0, successes: 0, failures: 0,
          last_result: "idle", last_http_status: null });
      }
      if (path === "/api/v1/config/motion-ntfy") {
        state.motionNtfy ??= { saved_enabled: false, enabled: false, url: null, url_set: false, live_url_set: false, token: null, token_set: false, live_token_set: false, matches_saved: true, transport_available: true };
        if (request.method === "GET") return json(response, 200, state.motionNtfy);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "url", "token", "clear_token"].includes(name)) ||
            (update.url !== undefined && (typeof update.url !== "string" || !/^https?:\/\//.test(update.url))) ||
            (update.token !== undefined && typeof update.token !== "string") ||
            (update.clear_token !== undefined && typeof update.clear_token !== "boolean")) {
          return error(response, 400, "invalid_request", "invalid Motion ntfy update");
        }
        if (update.url) state.motionNtfy.url_set = state.motionNtfy.live_url_set = true;
        if (update.token) state.motionNtfy.token_set = state.motionNtfy.live_token_set = true;
        if (update.clear_token) state.motionNtfy.token_set = state.motionNtfy.live_token_set = false;
        state.motionNtfy.enabled = update.enabled;
        state.motionNtfy.saved_enabled = update.enabled;
        state.motionNtfy.matches_saved = true;
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-ntfy" && request.method === "GET") {
        return json(response, 200, { running: true, enabled: state.motionNtfy?.enabled ?? false, transport_available: true,
          queue_capacity: 2, queue_depth: 0, queue_dropped: 0, requests: 0, successes: 0, failures: 0,
          last_result: "idle", last_http_status: null });
      }
      if (path === "/api/v1/config/motion-webhook") {
        state.motionWebhook ??= { saved_enabled: false, enabled: false, url: null, url_set: false, live_url_set: false, matches_saved: true, transport_available: true };
        if (request.method === "GET") return json(response, 200, state.motionWebhook);
        const update = await body(request);
        if (typeof update.enabled !== "boolean" || Object.keys(update).some((name) => !["enabled", "url"].includes(name)) ||
            (update.url !== undefined && (typeof update.url !== "string" || !/^https?:\/\//.test(update.url)))) {
          return error(response, 400, "invalid_request", "invalid Motion webhook update");
        }
        if (update.url) state.motionWebhook.url_set = state.motionWebhook.live_url_set = true;
        state.motionWebhook.enabled = update.enabled;
        state.motionWebhook.saved_enabled = update.enabled;
        state.motionWebhook.matches_saved = true;
        state.lastMutation = { method: request.method, path, body: structuredClone(update) };
        return json(response, 200, { status: "applied", persistent: true });
      }
      if (path === "/api/v1/runtime/motion-webhook" && request.method === "GET") {
        return json(response, 200, { running: true, enabled: state.motionWebhook?.enabled ?? false, transport_available: true,
          queue_depth: 0, queue_dropped: 0, requests: 0, successes: 0, failures: 0,
          last_result: "idle", last_http_status: null, last_event_sequence: null });
      }
      const raptorStreamMatch = path.match(/^\/api\/v1\/prudynt\/stream([01])$/);
      if (raptorStreamMatch && request.method === "GET") {
        const id = Number(raptorStreamMatch[1]);
        const stream = state.raptorStreams[id];
        const profile = stream.format === "H264"
          ? { supported: true, available: true, recovery_required: false, profile: stream.profile, saved_profile: stream.profile, matches_saved: true, profiles: [0, 1, 2] }
          : { supported: false, available: false };
        return json(response, 200, { source: "raptor", persistent: true, stream_id: id, supported: true, available: true,
          gop: stream.gop, saved_gop: stream.gop, matches_saved: true,
          gop_mode_control: { supported: true, available: true, active_mode: stream.gop_mode,
            saved_available: true, saved_mode: stream.gop_saved_mode, matches_saved: stream.gop_mode === stream.gop_saved_mode,
            pending_restart: stream.gop_mode !== stream.gop_saved_mode, modes: ["DEFAULT", "PYRAMIDAL", "SMARTP"] },
          fps_control: { supported: true, available: true, status: "ok", stream_id: id, recovery_required: false, persistence_pending: false,
            live_applied: true, persisted: true, fps: stream.fps, monitoring: { related: true, active: true, paused: false, receiving: true, thread_owned: true } },
          encoding_control: { supported: true, available: true, rc_mode: stream.mode, bitrate: stream.bitrate, saved_rc_mode: stream.mode,
            saved_bitrate: stream.bitrate, matches_saved: true, bitrate_min: 1_000, bitrate_max: 100_000_000, bitrate_step: 1_000,
            modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY"] },
          rc_config_control: { supported: true, available: true, active_mode: stream.mode,
            active_qp: stream.mode === "FIXQP" ? stream.rc_active_qp : null, saved_available: true,
            saved_mode: stream.rc_saved_mode, saved_qp: stream.rc_saved_qp,
            matches_saved: stream.mode === stream.rc_saved_mode && (stream.mode !== "FIXQP" || stream.rc_active_qp === stream.rc_saved_qp),
            pending_restart: stream.mode !== stream.rc_saved_mode || (stream.mode === "FIXQP" && stream.rc_active_qp !== stream.rc_saved_qp),
            qp_min: 0, qp_max: 51, qp_default: 35, modes: ["CBR", "VBR", "CAPPED_VBR", "CAPPED_QUALITY", "FIXQP"] },
          codec_control: { supported: true, available: true, recovery_required: false, codec: stream.format, saved_codec: stream.format,
            matches_saved: true, codecs: ["H264", "H265"] }, profile_control: profile });
      }
      if (path === "/api/v1/prudynt/audio" && request.method === "GET") {
        const audio = state.configs.prudynt.audio;
        const effects = state.scenario === "raptor-audio-effects";
        const processing = effects && audio.mic_enabled;
        const names = ["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain"];
        const levels = Object.fromEntries(names.map((name) => {
          const gain = name.endsWith("gain");
          const available = state.scenario !== "raptor-audio-unavailable" && (name.startsWith("mic") ? audio.mic_enabled : audio.spk_enabled);
          return [name, { supported: true, available, value: available ? audio[name] : null, min: gain ? 0 : -30, max: name === "mic_alc_gain" ? 7 : gain ? 31 : 120 }];
        }));
        return json(response, 200, { source: "raptor", mic_enabled: state.scenario === "raptor-audio-unavailable" ? null : audio.mic_enabled, spk_enabled: state.scenario === "raptor-audio-unavailable" ? null : audio.spk_enabled,
          mic_muted: false, mic_format: state.scenario !== "raptor-audio-unavailable" && audio.mic_enabled ? (["PCM", "G711A", "G711U"].includes(audio.mic_format) ? audio.mic_format : "PCM") : null,
          mic_sample_rate: state.scenario !== "raptor-audio-unavailable" && audio.mic_enabled ? 8000 : null, input_readback: "owner", processing_readback: "owner",
          codecs_built: { PCM: true, G711A: true, G711U: true, AAC: false, OPUS: false }, effects_built: effects, processing_available: processing,
          mic_noise_suppression: processing ? audio.mic_noise_suppression : null,
          mic_agc_enabled: processing ? audio.mic_agc_enabled : null, mic_high_pass_filter: processing ? audio.mic_high_pass_filter : null,
          mic_agc_target_level_dbfs: processing ? audio.mic_agc_target_level_dbfs : null,
          mic_agc_compression_gain_db: processing ? audio.mic_agc_compression_gain_db : null,
          mic_is_digital: false, mic_input_basis: "dlink-a1-profile-amic", force_stereo: false, channel_basis: "rad-fixed-mono",
          buffer_warn_frames: null, buffer_cap_frames: null, buffer_control: "unsupported-prudynt-queue-policy",
          tap_enabled: null, tap_path: null, tap_control: "unsupported",
          levels, ...Object.fromEntries(names.map((name) => [name, levels[name].value])) });
      }
      if (path === "/api/v1/prudynt" && request.method === "POST") {
        const update = await body(request);
        if (update.motion) {
          if (Object.keys(update).length !== 1 || Object.keys(update.motion).length !== 1 || typeof update.motion.enabled !== "boolean") return error(response, 400, "invalid_request", "invalid motion update");
          state.heartbeat.motion_enabled = update.motion.enabled;
          state.raptorMotionSaved = update.motion.enabled;
          return json(response, 200, { status: "accepted", persistent: true });
        }
        const streamNames = Object.keys(update).filter((name) => /^stream[01]$/.test(name));
        if (streamNames.length > 0 && streamNames.length === Object.keys(update).length) {
          for (const name of streamNames) {
            const id = Number(name.slice(-1));
            const fields = update[name];
            const allowed = ["fps", "format", "profile", "mode", "bitrate", "qp_init", "gop", "gop_mode"];
            if (!fields || typeof fields !== "object" || Array.isArray(fields) || Object.keys(fields).some((field) => !allowed.includes(field))) return error(response, 400, "invalid_request", "invalid stream update");
            if (fields.qp_init !== undefined) {
              state.raptorStreams[id].rc_saved_mode = fields.mode;
              state.raptorStreams[id].rc_saved_qp = fields.qp_init;
            } else {
              const liveFields = { ...fields };
              delete liveFields.gop_mode;
              Object.assign(state.raptorStreams[id], liveFields);
              if (fields.mode !== undefined) state.raptorStreams[id].rc_saved_mode = fields.mode;
            }
            if (fields.gop_mode !== undefined) {
              state.raptorStreams[id].gop_mode = state.raptorStreams[id].gop_mode ?? "DEFAULT";
              state.raptorStreams[id].gop_saved_mode = fields.gop_mode;
            }
            if (fields.format === "H265") state.raptorStreams[id].profile = null;
            if (fields.format === "H264" && state.raptorStreams[id].profile === null) state.raptorStreams[id].profile = 1;
          }
          state.lastMutation = { method: request.method, path, body: structuredClone(update) };
          if (streamNames.length === 1 && update[streamNames[0]].fps !== undefined) {
            const name = streamNames[0];
            const id = Number(name.slice(-1));
            return json(response, 200, { status: "accepted", persistent: true,
              fps_result: { supported: true, available: true, status: "ok", stream_id: id, recovery_required: false, persistence_pending: false,
                live_applied: true, persisted: true, fps: state.raptorStreams[id].fps,
                monitoring: { related: true, active: true, paused: false, receiving: true, thread_owned: true } } });
          }
          const pendingRestart = streamNames.some(name =>
            (update[name].qp_init !== undefined &&
             (state.raptorStreams[Number(name.slice(-1))].mode !== update[name].mode ||
              (update[name].mode === "FIXQP" && state.raptorStreams[Number(name.slice(-1))].rc_active_qp !== update[name].qp_init))) ||
            (update[name].gop_mode !== undefined && state.raptorStreams[Number(name.slice(-1))].gop_mode !== update[name].gop_mode));
          return json(response, 200, { status: "accepted", persistent: true,
            ...(streamNames.some(name => update[name].qp_init !== undefined || update[name].gop_mode !== undefined) ? { pending_restart: pendingRestart } : {}) });
        }
        if (!update.audio || Object.keys(update.audio).some((name) => !["mic_vol", "mic_gain", "mic_alc_gain", "spk_vol", "spk_gain", "mic_enabled", "spk_enabled", "mic_format", ...(state.scenario === "raptor-audio-effects" ? ["mic_noise_suppression", "mic_agc_enabled", "mic_high_pass_filter", "mic_agc_target_level_dbfs", "mic_agc_compression_gain_db"] : [])].includes(name))) return error(response, 503, "service_unavailable", "Audio setting not mapped");
        Object.assign(state.configs.prudynt.audio, update.audio);
        return json(response, 200, { status: "accepted", persistent: true });
      }
      const unavailable = path.startsWith("/api/v1/prudynt") || [
        "/api/v1/config/daynight", "/api/v1/config/ha",
        "/api/v1/runtime/ha", "/api/v1/actions/ha", "/api/v1/recorder",
        "/api/v1/runtime/daynight/history", "/api/v1/runtime/daynight/sensors",
        "/api/v1/runtime/media/metrics", "/api/v1/actions/reset", "/api/v1/actions/factory-reset",
        "/api/v1/services/send/config",
      ].includes(path);
      if (unavailable) return error(response, 503, "service_unavailable", "This media configuration operation is not implemented by the Raptor adapter");
      if (path === "/api/v1/runtime/heartbeat") return json(response, 200, {
        ...state.heartbeat, rec_ch0: null, rec_ch1: null, timelapse_enabled: null,
        mic_enabled: null, spk_enabled: null, motion_ingress_ready: null,
        motion_enabled: state.scenario === "raptor-failure" ? null : state.heartbeat.motion_enabled,
        privacy_enabled: state.scenario === "raptor-failure" ? null : state.heartbeat.privacy_enabled,
        ircut_state: null, ir850_state: null, ir940_state: null, white_state: null,
        controls_supported: { daynight: true, motion: state.scenario !== "raptor-missing-motion", privacy: true },
      });
      if (path === "/api/v1/runtime/media") return json(response, 200, {
        streams: Object.fromEntries([0, 1].map((id) => [`ch${id}`, {
          available: true, enabled: true, snapshot_url: `/api/v1/actions/snapshot?stream_id=${id}`,
          width: id === 0 ? 1920 : 640, height: id === 0 ? 1080 : 360,
          fps: 15, format: "H264", rtsp_endpoint: `stream${id}`,
        }])),
        ...(state.scenario === "raptor-failure" ? {} : { rtsp: { port: 8554 } }),
      });
      if (path === "/api/v1/runtime/motion") return json(response, 200, {
        version: 1, source: "raptor", supported: state.scenario !== "raptor-missing-motion",
        available: true, monitoring: state.heartbeat.motion_enabled,
        active: state.heartbeat.motion_active, receiving: state.heartbeat.motion_enabled,
      });
      if (path === "/api/v1/imaging") {
        const names = ["brightness", "contrast", "saturation", "sharpness"];
        if (request.method === "POST") {
          const update = await body(request);
          if (Object.entries(update).some(([name, value]) => !names.includes(name) || !Number.isInteger(value) || value < 0 || value > 255)) return error(response, 400, "invalid_request", "invalid imaging field");
          Object.assign(state.configs.prudynt.image, update);
        }
        return json(response, 200, { code: 200, result: "success", source: "raptor", persistent: true, message: { fields: Object.fromEntries(names.map((name) => [name, { supported: true, available: true, min: 0, max: 255, value: state.configs.prudynt.image[name] }])) } });
      }
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
      ch0: { ...state.configs.prudynt.stream0, available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=0" },
      ch1: { ...state.configs.prudynt.stream1, available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=1" },
    } });
    if (url.pathname === "/api/v1/runtime/system") return json(response, 200, { code: 200, result: "success", data: {
      network: { online: true, ip: "192.0.2.54", interfaces: state.configs.network.interfaces },
      memory: { total: 65536, free: 24576, active: 24576, buffers: 4096, cached: 12288, used: 40960 },
      overlay: { total: 8192, free: 3584, used: 4608 }, extras: { total: 31166976, free: 24576000, used: 6590976 },
      media: state.scenario.startsWith("raptor") ? null : { prudynt_running: true, media_ready: true, stream0_enabled: true, stream1_enabled: true }, timestamp: 1787248800,
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
      if (command.cmd === "color") state.heartbeat.color_mode = Number(command.val) ? 0 : 1;
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
    if (url.pathname === "/api/v1/health") return json(response, 200, { status: "ok", healthy: true, control_api: { name: "Thingino Control", version: 1 }, backend: { name: state.scenario.startsWith("raptor") ? "raptor" : "Prudynt", available: true }, checks: { media: "ready" } });
    if (url.pathname.startsWith("/api/v1/") || url.pathname.startsWith("/media/v1/")) return error(response, 404, "not_found", "fixture route not found");
    return serveStatic(response, url.pathname);
  } catch (failure) {
    return error(response, 400, "invalid_request", failure instanceof Error ? failure.message : "invalid fixture request");
  }
});

server.listen(port, host, () => console.log(`fixture WebUI listening on http://${host}:${port}`));
