import type { JsonObject } from "../../src/api/contracts";

/**
 * Redacted, device-shaped responses used by host tests.
 *
 * These are deliberately complete enough to catch a form that accidentally
 * serializes only its visible controls.  They are not defaults for a real
 * camera and must never contain credentials or device identifiers.
 */

export const networkFixture = {
  hostname: "dcs6100-a1",
  dns: { primary: "192.0.2.1", secondary: "1.1.1.1" },
  wifi: { ssid: "Quiet Grid Lab", bssid: "02:00:00:00:00:11", password: null, password_set: true },
  wifi_ap: { enabled: false },
  interfaces: {
    eth0: { enabled: false, dhcp: true, ipv6: false, mac: "02:00:00:00:00:02", address: "", netmask: "", gateway: "", broadcast: "", link_up: false },
    wlan0: { enabled: true, dhcp: false, ipv6: false, mac: "02:00:00:00:00:01", address: "192.0.2.54", netmask: "255.255.255.0", gateway: "192.0.2.1", broadcast: "192.0.2.255", link_up: true },
    usb0: { enabled: false, dhcp: true, ipv6: false, mac: "02:00:00:00:00:03", address: "", netmask: "", gateway: "", broadcast: "", link_up: false },
  },
} satisfies JsonObject;

export const timeFixture = {
  timezone: "Europe/Helsinki",
  timezone_options: [
    { name: "Etc/UTC", label: "UTC" },
    { name: "Europe/Helsinki", label: "Europe/Helsinki" },
    { name: "Europe/Berlin", label: "Europe/Berlin" },
    { name: "America/New_York", label: "America/New_York" },
  ],
  current_unix_time: 1787481296,
  dhcp_ignore_timezone: true,
  ntp_server_0: "pool.ntp.org",
  ntp_server_1: "",
  ntp_server_2: "",
  ntp_server_3: "",
} satisfies JsonObject;

export const audioFixture = {
  buffer_cap_frames: 100,
  buffer_warn_frames: 80,
  force_stereo: false,
  mic_agc_compression_gain_db: 9,
  mic_agc_enabled: true,
  mic_agc_target_level_dbfs: 10,
  mic_alc_gain: 4,
  mic_enabled: true,
  mic_format: "AAC",
  mic_gain: 18,
  mic_high_pass_filter: false,
  mic_is_digital: false,
  mic_noise_suppression: 1,
  mic_vol: 70,
  spk_enabled: false,
  spk_gain: 12,
  spk_vol: 55,
  tap_enabled: false,
  tap_path: "/run/prudynt/audio_mic.pcm",
} satisfies JsonObject;

export const imageFixture = {
  brightness: 128,
  contrast: 128,
  sharpness: 128,
  saturation: 128,
  hue: 128,
  backlight_compensation: 0,
  drc_strength: 128,
  highlight_depress: 128,
  defog_strength: 128,
  sinter_strength: 128,
  dpc_strength: 128,
  core_wb_mode: 0,
  wb_bgain: 128,
  wb_rgain: 128,
  ae_compensation: 128,
  hflip: false,
  vflip: false,
} satisfies JsonObject;

export const imagingRuntimeFixture = {
  code: 200,
  result: "success",
  source: "raptor",
  persistent: true,
  message: {
    fields: {
      brightness: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      contrast: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      saturation: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      sharpness: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      backlight: { supported: true, min: 0, max: 10, value: 0, default: 0 },
      wide_dynamic_range: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      tone: { supported: false },
      defog: { supported: true, min: 0, max: 255, value: 128, default: 128 },
      noise_reduction: { supported: true, min: 0, max: 255, value: 128, default: 128 },
    },
  },
} satisfies JsonObject;

export const stream0Fixture = {
  allow_shared: true,
  audio_enabled: false,
  enabled: true,
  width: 1920,
  height: 1080,
  format: "H264",
  fps: 20,
  gop: 40,
  max_gop: 80,
  mode: "CBR",
  bitrate: 2400,
  profile: 2,
  buffers: 4,
  rtsp_endpoint: "ch0",
  rotation: 0,
  qp_init: -1,
  qp_max: -1,
  qp_min: -1,
} satisfies JsonObject;

export const stream1Fixture = {
  allow_shared: true,
  audio_enabled: false,
  enabled: false,
  width: 640,
  height: 360,
  format: "H264",
  fps: 0,
  gop: 30,
  max_gop: 60,
  mode: "CBR",
  bitrate: 640,
  profile: 1,
  buffers: -1,
  rtsp_endpoint: "ch1",
  rotation: 0,
  qp_init: -1,
  qp_max: -1,
  qp_min: -1,
} satisfies JsonObject;

export const webuiFixture = {
  username: "root",
  theme: "auto",
  level: "advanced",
  paranoid: false,
  track_focus: true,
  focus_timeout: 15,
  auth_bypass_ips: "",
} satisfies JsonObject;

export const rsyslogFixture = {
  host: "log.example.invalid",
  port: 514,
  enabled: false,
  file: false,
} satisfies JsonObject;

export const daynightFixture = {
  enabled: true,
  initial_mode: "",
  force_mode: "",
  night_threshold: 28,
  day_threshold: 42,
  night_count_threshold: 6,
  day_count_threshold: 4,
  sample_interval_ms: 1000,
  transition_delay_s: 5,
  loglevel: "INFO",
  controls: { color: false, ircut: true, ir850: true },
  schedule: { enabled: false, start_at: "06:00", stop_at: "22:00" },
  sun: { enabled: false, latitude: 60.1699, longitude: 24.9384, sunrise_offset: 0, sunset_offset: 0 },
} satisfies JsonObject;

export const gpioFixture = {
  profile: "D-Link DCS-6100LHV2 A1",
  gpio: { ircut: "50 49", ir850: 61 },
  led: { startup_indicator: "off" },
  available_startup_indicators: "green,red",
  hardware_io: [
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
  ],
  pwm_pins: "",
} satisfies JsonObject;

export const homeAssistantFixture = {
  enabled: false,
  mqtt: { host: "homeassistant.local", port: 1883, username: "camera", password: null, password_set: true, client_id_prefix: "dcs6100", use_ssl: false, tls_skip_verify: false },
  device_name: "Front camera",
  device_model: "D-Link DCS-6100LHV2 A1",
  discovery_prefix: "homeassistant",
  state_interval: 30,
  discovery_interval: 300,
  camera_interval: 10,
  ota_check_interval: 21600,
  enable_motion: true,
  enable_motion_guard: true,
  enable_doorbell: false,
  enable_live_view: true,
  enable_daynight: true,
  enable_privacy: true,
  enable_snapshot: true,
  enable_ircut: true,
  enable_ir850: true,
  enable_color: true,
  enable_gain: false,
  enable_rssi: true,
  enable_firmware_version: true,
  enable_firmware_timestamp: true,
  enable_ota: false,
  enable_reboot: false,
  doorbell_supported: false,
  ota_supported: false,
} satisfies JsonObject;

export const homeAssistantRuntimeFixture = {
  enabled: true,
  state: "online",
  connected: true,
  last_connect_unix: 1_787_480_000,
  last_disconnect_unix: null,
  last_error: null,
  reconnect_in_ms: null,
  queue_depth: 0,
  queue_high_water_mark: 3,
  published_messages: 42,
  received_commands: 3,
  rejected_commands: 1,
  dropped_messages: 0,
} satisfies JsonObject;

export const recorderFixture = {
  ok: true,
  data: {
    video: { autostart: false, mount: "/mnt/media", device_path: "/dev/mmcblk0p1", filename: "%Y%m%d-%H%M%S", channel: 0, duration: 300, cleanup_enabled: true, limit: 500, min_free_mb: 256, check_interval: 60 },
    timelapse: { enabled: true, mount: "/mnt/media", filepath: "timelapse", filename: "%Y%m%d-%H%M%S.jpg", interval: 10, keep_days: 14, preset_enabled: false, presets: { ircut: false, ir850: false, color: true } },
    mounts: [{ path: "/mnt/media", device: "/dev/mmcblk0p1", filesystem: "vfat", writable: true }],
    messages: { recorder: "fixture recorder state" },
    debug: { source: "fixture" },
  },
} satisfies JsonObject;

export const osdFixture = {
  burnin: { enabled: true, format: "%F %T", scale: 1, fill_color: "#ffffffff", outline_color: "#000000ff", background_color: "#00000080" },
  sei: { enabled: true, entries: { clock: { type: "timestamp", format: "%F %T", position: "-10,-10" }, gain: { type: "gain", format: "%s", position: "10,-10" } } },
} satisfies JsonObject;

export const motionFixture = {
  enabled: true,
  sensitivity: 5,
  playonspeaker: false,
  cooldown_time: 5,
  debounce_time: 0,
  init_time: 5,
  ivs_polling_timeout: 500,
  min_time: 1,
  motor_settle_ms: 0,
  post_time: 0,
  frame_width: 640,
  frame_height: 360,
  monitor_stream: 1,
  roi_0_x: 0,
  roi_0_y: 0,
  roi_1_x: 640,
  roi_1_y: 360,
  roi_count: 12,
  skip_frame_count: 5,
  video_length: 10,
  send2email: false,
  send2ftp: false,
  send2gotify: false,
  send2mqtt: false,
  send2ntfy: false,
  send2storage: false,
  send2telegram: false,
  send2webhook: false,
} satisfies JsonObject;

export const privacyFixture = {
  enabled: false,
  stream0_enabled: false,
  stream1_enabled: false,
} satisfies JsonObject;

export const sendFixture = {
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
} satisfies JsonObject;

export const domainFixtures = {
  network: networkFixture,
  time: timeFixture,
  audio: audioFixture,
  image: imageFixture,
  stream0: stream0Fixture,
  stream1: stream1Fixture,
  webui: webuiFixture,
  rsyslog: rsyslogFixture,
  daynight: daynightFixture,
  gpio: gpioFixture,
  ha: homeAssistantFixture,
  recorder: recorderFixture,
  osd: osdFixture,
  motion: motionFixture,
  privacy: privacyFixture,
  send: sendFixture,
} satisfies Record<string, JsonObject>;

export type DomainFixtureName = keyof typeof domainFixtures;
