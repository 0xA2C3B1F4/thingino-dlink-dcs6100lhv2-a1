export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface ApiErrorEnvelope {
  status: "error";
  error: {
    code: string;
    message: string;
  };
}

export interface SessionState {
  authenticated: boolean;
  username: string | null;
  is_default_password: boolean;
  client_ip: string;
  control_api: {
    name: "Thingino Control";
    version: 1;
  };
}

export interface HealthResponse {
  control_api: { name: "Thingino Control"; version: 1 };
  status: "ok" | "degraded";
  healthy: boolean;
  backend: { name: string; available: boolean };
  checks: JsonObject;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  success: true;
  is_default_password: boolean;
}

export interface RuntimeHeartbeat {
  controls_supported?: Partial<Record<"daynight" | "motion" | "privacy", boolean>>;
  time_now: number;
  uptime: number;
  daynight_brightness: number | null;
  total_gain: number | null;
  daynight_mode: "day" | "night" | "unknown";
  rec_ch0_file_closed?: boolean;
  rec_ch1_file_closed?: boolean;
  rec_ch0_available?: boolean;
  rec_ch1_available?: boolean;
  rec_ch0_reason?: "no_sd" | "storage_unknown" | "no_space" | "video_unavailable" | "writer_error" | "starting" | "stopping" | null;
  rec_ch1_reason?: "no_sd" | "storage_unknown" | "no_space" | "video_unavailable" | "writer_error" | "starting" | "stopping" | null;
  rec_ch0: boolean | null;
  rec_ch1: boolean | null;
  timelapse_enabled: boolean | null;
  motion_enabled: boolean | null;
  motion_active: boolean | null;
  motion_ingress_ready: boolean | null;
  privacy_enabled: boolean | null;
  color_mode: 0 | 1 | null;
  mic_enabled: boolean | null;
  spk_enabled: boolean | null;
  daynight_enabled: boolean | null;
  ircut_state: 0 | 1 | null;
  ir850_state: 0 | 1 | null;
  ir940_state: 0 | 1 | null;
  white_state: 0 | 1 | null;
}

export interface RaptorMotionRuntime {
  version: 1;
  source: "raptor";
  available: boolean;
  supported: boolean;
  monitoring: boolean;
  active: boolean;
  receiving: boolean;
}

export interface MotionRuntime {
  version: 1;
  ingress_ready: boolean;
  monitoring: boolean;
  active: boolean;
  channel: 0 | 1;
  producer_pid: number | null;
  producer_sequence: number | null;
  last_observation_monotonic_ms: number | null;
  last_transition_unix_ms: number | null;
  queue: { capacity: number; depth: number; high_water: number; dropped: number; coalesced: number };
  received: number;
  rejected: number;
  accepted: number;
  duplicate_or_replayed: number;
  producer_restarts: number;
  transitions: number;
  sink: { queued: number; coalesced: number; dropped: number; disabled: number };
  speaker: { queued: number; dropped: number };
  clips: { capacity: number; pending: number; requested: number; ready: number; dropped: number; recovered: number; manifest_errors: number };
  unsupported_destination_events: number;
}

export interface StreamConfig {
  enabled: boolean;
  width: number;
  height: number;
  format: "H264" | "H265";
  fps: number;
  gop: number;
  max_gop: number;
  mode: "CBR" | "VBR" | "FIXQP" | "CAPPED_VBR" | "CAPPED_QUALITY";
  bitrate: number;
  profile: number;
  buffers: number;
  rtsp_endpoint: string;
  audio_enabled: boolean;
}

export interface RuntimeMedia {
  rtsp?: {
    username?: string;
    port?: number | string;
  };
  stream0: RuntimeStream;
  stream1: RuntimeStream;
}

export interface RuntimeStreamState {
  available: boolean;
  enabled: boolean;
  snapshot_url: string | null;
}

export type RuntimeStream = Partial<StreamConfig> & RuntimeStreamState;

export interface RuntimeMediaResponse {
  rtsp?: RuntimeMedia["rtsp"];
  streams: {
    ch0: RuntimeStream;
    ch1: RuntimeStream;
  };
}

export interface NetworkInterfaceConfig {
  enabled: boolean;
  mac?: string;
  dhcp: boolean;
  address?: string;
  netmask?: string;
  gateway?: string;
  broadcast?: string;
  ipv6?: boolean;
  link_up?: boolean;
}

export interface NetworkConfig {
  hostname: string;
  dns: { primary: string; secondary: string };
  wifi: { ssid: string; bssid: string; password: string | null; password_set: boolean };
  wifi_ap: { enabled: boolean };
  interfaces: {
    eth0: NetworkInterfaceConfig;
    wlan0: NetworkInterfaceConfig;
    usb0: NetworkInterfaceConfig;
  };
}

export interface WifiNetwork {
  ssid: string;
  bssid?: string;
  signal?: number;
  security?: string;
}

export interface WifiScanResponse {
  networks: WifiNetwork[];
}

export interface NetworkProbeMetadata {
  actions: Array<{ id: "resolve" | "connect"; label: string; description: string }>;
  interfaces: string[];
  defaults: { action: "resolve" | "connect"; interface: string; packet_size: number; count: 1 };
  limits: { packet_size: { min: number; max: number }; count: { min: 1; max: 1 } };
}

export interface NetworkProbeResponse {
  command: string;
  success: boolean;
  output_b64: string;
}

export interface TimeConfig {
  timezone: string;
  timezone_options: Array<string | { name: string; label?: string }>;
  current_unix_time?: number;
  current_unix_ms?: number;
  current_local_time?: string;
  dhcp_ignore_timezone: boolean;
  ntp_server_0: string;
  ntp_server_1: string;
  ntp_server_2: string;
  ntp_server_3: string;
}

export interface TimeUpdate {
  action: "update";
  timezone: string;
  dhcp_ignore_timezone: boolean;
  ntp_server_0: string;
  ntp_server_1: string;
  ntp_server_2: string;
  ntp_server_3: string;
}

export interface AdminConfig {
  name: string;
  email: string;
  telegram: string;
  discord: string;
}

export interface AccessConfig {
  source?: "raptor";
  auth_enabled?: boolean;
  username: string | null;
  password: null;
  password_set: boolean;
  rtsp_port: number | null;
  rtsp_ch0: string | null;
  rtsp_ch1: string | null;
  rtsp_mic: string | null;
  onvif_port: 80 | null;
  onvif_enabled: true | null;
  onvif_ingress: "same-origin" | null;
}

export interface AccessUpdate {
  username?: string;
  password?: string;
  rtsp_port?: number;
  rtsp_ch0?: string;
  rtsp_ch1?: string;
  rtsp_mic?: string;
  onvif_port?: 80;
  onvif_enabled?: true;
}

export interface AccessUpdateResponse {
  status: "ok";
  password_changed: boolean;
}

export interface WebuiConfig {
  username: string;
  auth_bypass_ips?: string;
  theme: "light" | "dark" | "auto";
  paranoid: boolean;
  track_focus: boolean;
  focus_timeout: number;
}

export interface RaptorAudioConfig {
  source: "raptor";
  mic_enabled: boolean | null;
  spk_enabled: boolean | null;
  mic_muted: boolean | null;
  mic_format: string | null;
  mic_sample_rate: number | null;
  input_readback: "owner";
  processing_readback: "owner";
  codecs_built: Record<string, boolean>;
  effects_built: boolean;
  processing_available: boolean;
  mic_vol: number | null;
  mic_gain: number | null;
  mic_alc_gain: number | null;
  mic_noise_suppression: number | null;
  mic_agc_enabled: boolean | null;
  mic_high_pass_filter: boolean | null;
  mic_agc_target_level_dbfs: number | null;
  mic_agc_compression_gain_db: number | null;
  mic_is_digital: false;
  mic_input_basis: "dlink-a1-profile-amic";
  force_stereo: false;
  channel_basis: "rad-fixed-mono";
  buffer_warn_frames: null;
  buffer_cap_frames: null;
  buffer_control: "unsupported-prudynt-queue-policy";
  tap_enabled: null;
  tap_path: null;
  tap_control: "unsupported";
  spk_vol: number | null;
  spk_gain: number | null;
  levels: Record<string, { supported: boolean; available: boolean; value: number | null; min: number; max: number }>;
}

export interface AudioConfig {
  buffer_cap_frames: number;
  buffer_warn_frames: number;
  mic_enabled: boolean;
  mic_format: string;
  mic_vol: number;
  mic_gain: number;
  mic_alc_gain: number;
  mic_noise_suppression: number;
  mic_agc_compression_gain_db: number;
  mic_agc_target_level_dbfs: number;
  mic_agc_enabled: boolean;
  mic_high_pass_filter: boolean;
  mic_is_digital: boolean;
  force_stereo: boolean;
  spk_enabled: boolean;
  spk_vol: number;
  spk_gain: number;
  tap_enabled: boolean;
  tap_path: string;
}

export interface ImagingConfig {
  brightness: number;
  contrast: number;
  sharpness: number;
  saturation: number;
  hue: number;
  backlight_compensation: number;
  drc_strength: number;
  highlight_depress?: number;
  defog_strength: number;
  sinter_strength?: number;
  dpc_strength: number;
  core_wb_mode: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9;
  wb_bgain: number;
  wb_rgain: number;
  ae_compensation: number;
  hflip: boolean;
  vflip: boolean;
}

export interface ImagingRuntimeField {
  available?: boolean;
  supported: boolean;
  min?: number;
  max?: number;
  value?: number;
  default?: number;
  verification?: "unsupported" | "sdk-readback" | "sdk-setter-config";
  observed_value?: number | null;
  configured_value?: number | null;
  saved_value?: number | null;
  matches_saved?: boolean;
}

export interface ImagingWhiteBalanceRuntime {
  supported: boolean;
  available: boolean;
  verification: "unsupported" | "sdk-readback";
  modes: number[];
  gain_min: number;
  gain_max: number;
  mode: number | null;
  gains_effective: boolean;
  rgain: number | null;
  bgain: number | null;
  configured_mode: number | null;
  configured_rgain: number | null;
  configured_bgain: number | null;
  saved_mode: number | null;
  saved_rgain: number | null;
  saved_bgain: number | null;
  matches_saved: boolean;
}

export interface ImagingRuntimeState {
  source: "raptor";
  persistent: boolean;
  fields: Record<string, ImagingRuntimeField>;
  white_balance?: ImagingWhiteBalanceRuntime;
}

export interface MotionConfig {
  enabled: boolean;
  sensitivity: number;
  playonspeaker?: boolean;
  speaker_repeats?: number;
  cooldown_time?: number;
  debounce_time?: number;
  init_time?: number;
  ivs_polling_timeout?: number;
  min_time?: number;
  motor_settle_ms?: number;
  post_time?: number;
  frame_width?: number;
  frame_height?: number;
  monitor_stream?: 0 | 1 | 2 | 3;
  roi_0_x?: number;
  roi_0_y?: number;
  roi_1_x?: number;
  roi_1_y?: number;
  roi_count?: number;
  skip_frame_count?: number;
  video_length?: number;
  send2email?: boolean;
  send2ftp?: boolean;
  send2gotify?: boolean;
  send2mqtt?: boolean;
  send2ntfy?: boolean;
  send2storage?: boolean;
  send2telegram?: boolean;
  send2webhook?: boolean;
}

export interface PrivacyConfig {
  enabled: boolean;
  stream0_enabled: boolean;
  stream1_enabled: boolean;
}

export interface DayNightConfig {
  enabled: boolean;
  initial_mode: "" | "day" | "night";
  force_mode: "" | "day" | "night";
  night_threshold: number;
  day_threshold: number;
  night_count_threshold: number;
  day_count_threshold: number;
  sample_interval_ms: number;
  transition_delay_s: number;
  loglevel: "FATAL" | "ERROR" | "WARN" | "INFO" | "DEBUG" | "TRACE";
  controls: { color: boolean; ircut: boolean; ir850: boolean };
  schedule: { enabled: boolean; start_at: string; stop_at: string };
  sun: { enabled: boolean; latitude: number; longitude: number; sunrise_offset: number; sunset_offset: number };
}

export interface RecorderConfig {
  video: {
    autostart: boolean;
    mount: string;
    device_path?: string;
    filename: string;
    channel: 0 | 1;
    duration: number;
    cleanup_enabled: boolean;
    limit: number;
    min_free_mb: number;
    check_interval: number;
  };
  timelapse: {
    enabled: boolean;
    mount: string;
    filepath: string;
    filename: string;
    interval: number;
    keep_days: number;
    preset_enabled: boolean;
    presets: {
      ircut: boolean;
      ir850: boolean;
      ir940: boolean;
      white: boolean;
      color: boolean;
    };
  };
}

export type RecorderUpdate =
  | { video: Partial<RecorderConfig["video"]> }
  | { timelapse: Partial<RecorderConfig["timelapse"]> };

export interface PrudyntRecorderResponse {
  ok: true;
  data: RecorderConfig & {
    mounts: JsonValue[];
    messages: JsonObject;
    debug: JsonObject;
  };
}

export interface RaptorRecorderResponse {
  ok: true;
  data: {
    source: "raptor";
    persistent: true;
    video: RecorderConfig["video"];
    saved_video: RecorderConfig["video"] | null;
    matches_saved: boolean;
    runtime: JsonObject | null;
    storage_available: boolean;
    free_mb: number | null;
    boot_pending: boolean;
    cleanup_ok: boolean;
    timelapse: RaptorTimelapsePolicy | null;
    timelapse_supported: boolean;
    mounts: string[];
  };
}

export type RaptorTimelapsePolicy = Omit<RecorderConfig["timelapse"], "presets"> & {
  presets: Pick<RecorderConfig["timelapse"]["presets"], "ircut" | "ir850" | "color">;
};
export interface RaptorTimelapseResponse {
  ok: true;
  data: {
    source: "raptor";
    domain: "timelapse";
    persistent: true;
    available: boolean;
    timelapse: RaptorTimelapsePolicy;
    saved_timelapse: RaptorTimelapsePolicy | null;
    matches_saved: boolean;
    mounts: string[];
    runtime: {
      phase: string;
      last_error: string | null;
      preset_restore: "not_used" | "restored" | "conflict";
      successes: number;
      last_success: number | null;
      next_due: number | null;
      cleanup_blocked: boolean;
    };
  };
}

export type RecorderResponse = PrudyntRecorderResponse | RaptorRecorderResponse;

export interface HomeAssistantConfig {
  enabled: boolean;
  mqtt: {
    host: string;
    port: number;
    username: string;
    password: string | null;
    password_set: boolean;
    client_id_prefix: string;
    use_ssl: boolean;
    tls_skip_verify: boolean;
  };
  device_name: string;
  device_model: string;
  discovery_prefix: string;
  state_interval: number;
  discovery_interval: number;
  camera_interval: number;
  ota_check_interval: number;
  enable_motion: boolean;
  enable_doorbell: boolean;
  enable_motion_guard: boolean;
  enable_ircut: boolean;
  enable_daynight: boolean;
  enable_privacy: boolean;
  enable_color: boolean;
  enable_ir850: boolean;
  enable_gain: boolean;
  enable_rssi: boolean;
  enable_snapshot: boolean;
  enable_live_view: boolean;
  enable_reboot: boolean;
  enable_ota: boolean;
  enable_firmware_version: boolean;
  enable_firmware_timestamp: boolean;
  doorbell_supported: false;
  ota_supported: false;
}

export type HomeAssistantAction = "reconnect" | "republish_discovery" | "publish_state";

export interface HomeAssistantRuntime {
  enabled: boolean;
  state: "disabled" | "connecting" | "online" | "backoff" | "error";
  connected: boolean;
  last_connect_unix: number | null;
  last_disconnect_unix: number | null;
  last_error: string | null;
  reconnect_in_ms: number | null;
  queue_depth: number;
  queue_high_water_mark: number;
  published_messages: number;
  received_commands: number;
  rejected_commands: number;
  dropped_messages: number;
}

export interface RemoteLoggingConfig {
  host: string;
  port: number;
  enabled: boolean;
  file: boolean;
}

export interface GpioHardwareIo {
  gpio: 18 | 49 | 50 | 52 | 54 | 57 | 59 | 60 | 61 | 63;
  function: string;
  direction: "input" | "output";
  owner: string;
  read_only: true;
}

export interface GpioConfig {
  profile: "D-Link DCS-6100LHV2 A1";
  gpio: { ircut: "50 49"; ir850: 61 };
  led: { startup_indicator: "off" | "green" | "red" };
  available_startup_indicators: string;
  hardware_io: GpioHardwareIo[];
  pwm_pins: "";
}

export interface OsdEntry {
  type: "text" | "gain" | "hostname" | "ipaddress" | "timestamp" | "uptime";
  format: string;
  position: string;
}

export interface OsdConfig {
  burnin: {
    enabled: boolean;
    format?: string;
    scale: number;
    fill_color: string;
    outline_color: string;
    background_color: string;
  };
  sei: { enabled: boolean; entries: Record<string, OsdEntry> };
}

export interface RaptorOsdMetadataEntry extends OsdEntry {
  name: string;
  available: boolean;
  unavailable_reason?: "gain producer missing";
}

export interface RaptorOsdMetadataConfig {
  source: "raptor";
  persistent: true;
  supported: true;
  confirmed: boolean;
  saved: {
    available: boolean;
    id: string;
    enabled: boolean | null;
    entries: RaptorOsdMetadataEntry[];
  };
  published: {
    id: string;
    generation: number;
    status: number;
    fresh: boolean;
    matches_saved: boolean;
  };
}

export interface SensorIdentity {
  sensor_model: string;
  soc_model: string;
  soc_family: string;
  file_path: string;
  md5: string;
}

export interface DayNightSensors {
  source?: "raptor";
  night_threshold_pct: number | null;
  day_threshold_pct: number | null;
  thresholds?: JsonObject | null;
  current: JsonObject | null;
}

export interface UsageSection {
  total: number;
  free: number;
  used: number;
  active?: number;
  buffers?: number;
  cached?: number;
}

export interface RuntimeSystem {
  network: { online: boolean; ip: string; interfaces: Record<string, Partial<NetworkInterfaceConfig>> };
  memory: UsageSection;
  overlay: UsageSection;
  extras: UsageSection;
  media: { prudynt_running: boolean | null; media_ready: boolean | null; stream0_enabled: boolean | null; stream1_enabled: boolean | null };
  timestamp: number;
}

export interface RuntimeSystemEnvelope {
  code: 200;
  result: "success";
  data: RuntimeSystem;
}

export interface OverlayStatus {
  usage: { label: string; percent: number; state: string };
  listing_base64: string;
  path: "/overlay";
}

export interface SdStatus {
  ok: true;
  data: {
    has_sdcard: boolean;
    device: null | { name: string; node: string; vendor: string; model: string; size_bytes: number | null };
    reports: { partitions_b64: string; mounts_b64: string };
    format: {
      supported: boolean;
      options: Array<{ id: "fat32"; label: string; description: string }>;
      status: "idle" | "queued" | "running" | "succeeded" | "failed";
      last_output_b64: string;
    };
    filesystems: Array<{ device: string; mountpoint: string; filesystem: string; writable: boolean; total_kib: number | null; used_kib: number | null; free_kib: number | null }>;
    messages: { format_warning: string; not_present: string };
    debug: { detection: "sysfs" | "mount-table" | "device-node" | "none" };
  };
}

export interface SdFormatAccepted {
  status: "queued";
  filesystem: "fat32";
}

export interface FileBreadcrumb {
  label: string;
  path: string;
}

export interface FileEntry {
  name: string;
  path: string;
  size: string;
  perm: string;
  time: string;
  is_dir: boolean;
  is_link: boolean;
  link_target: string;
  deletable: boolean;
}

export interface FileListResponse {
  next_cursor?: string | null;
  truncated?: boolean;
  directory: string;
  parent: string;
  breadcrumbs: FileBreadcrumb[];
  entries: FileEntry[];
}

export interface FileTextResponse {
  file: string;
  content: string;
  content_encoding: "base64";
  size: number;
  lines: number;
  writable: boolean;
}

export interface FileTextWriteResponse {
  success: true;
  file?: string;
  size: number;
  lines: number;
}

export interface FileRemoveResponse {
  result: "ok";
}

export interface DiagnosticResponse {
  output_b64?: string;
  commands?: Array<{ command: string; output_base64: string }>;
  extras_html_base64?: string;
}

export interface DiagnosticCommand {
  command: string;
  output_base64: string;
}

export interface DiagnosticInfoResponse {
  commands: DiagnosticCommand[];
  extras_html_base64: string;
}

export interface DiagnosticBundleResponse {
  output_b64: string;
}

export interface CrontabConfig {
  content: string;
  max_bytes: number;
}

export interface MutationSuccess {
  status: "ok";
}

export interface DaynightSensorState {
  [key: string]: JsonValue;
}

export interface DaynightSensorsResponse {
  source?: "raptor";
  night_threshold_pct: number | null;
  day_threshold_pct: number | null;
  thresholds?: DaynightSensorState | null;
  current: DaynightSensorState | null;
}

export type LiveControlCommand =
  | { kind: "motion" | "privacy" | "microphone" | "speaker"; enabled: boolean }
  | { kind: "color" | "ircut" | "ir850"; enabled: boolean }
  | { kind: "recording"; enabled: boolean; stream: 0 | 1 }
  | { kind: "daynight"; mode: "auto" | "day" | "night" };
