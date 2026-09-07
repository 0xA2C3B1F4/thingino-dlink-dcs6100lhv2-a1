export const routes = {
  health: "/api/v1/health",
  auth: {
    login: "/api/v1/auth/login",
    logout: "/api/v1/auth/logout",
    password: "/api/v1/auth/password",
    session: "/api/v1/auth/session",
  },
  actions: {
    control: "/api/v1/actions/control",
    homeAssistant: "/api/v1/actions/ha",
    daynight: "/api/v1/actions/daynight",
    factoryReset: "/api/v1/actions/factory-reset",
    reboot: "/api/v1/actions/reboot",
    reset: "/api/v1/actions/reset",
    restartPrudynt: "/api/v1/actions/prudynt/restart",
    snapshot: (stream: 0 | 1) => `/api/v1/actions/snapshot?stream_id=${stream}`,
    syncTime: "/api/v1/actions/time/sync",
  },
  config: {
    access: "/api/v1/config/access",
    admin: "/api/v1/config/admin",
    crontab: "/api/v1/config/crontab",
    domain: (domain: string) => `/api/v1/config/${encodeURIComponent(domain)}`,
    homeAssistant: "/api/v1/config/ha",
    network: "/api/v1/config/network",
    remoteLogging: "/api/v1/config/rsyslog",
    time: "/api/v1/config/time",
    webui: "/api/v1/config/webui",
  },
  diagnostics: {
    generate: "/api/v1/diagnostics",
    info: (query: string) => `/api/v1/diagnostics/info?${encodeURIComponent(query)}`,
  },
  files: {
    list: (path: string) => `/api/v1/files?cd=${encodeURIComponent(path)}`,
    remove: (path: string) => `/api/v1/files?rm=${encodeURIComponent(path)}`,
    text: (path: string) => `/api/v1/files/text?file=${encodeURIComponent(path)}`,
  },
  imaging: "/api/v1/imaging",
  media: {
    file: (path: string, mode?: "download" | "play") => {
      const query = mode ? `&${mode}=1` : "";
      return `/media/v1/file?path=${encodeURIComponent(path)}${query}`;
    },
    mjpeg: (stream: 0 | 1) => `/media/v1/mjpeg?stream=${stream}`,
    preview: (stream: 0 | 1) => `/media/v1/mjpeg?stream=${stream}`,
    whip: (stream: 0 | 1) => `/api/v1/media/webrtc/whip?stream=${stream}`,
    osdSei: "/media/v1/osd-sei",
  },
  network: {
    probe: "/api/v1/network/probe",
    wifiScan: "/api/v1/network/wifi-scan",
  },
  prudynt: {
    command: "/api/v1/prudynt",
    domain: (domain: string) => `/api/v1/prudynt/${encodeURIComponent(domain)}`,
  },
  recorder: "/api/v1/recorder",
  runtime: {
    daynightHistory: "/api/v1/runtime/daynight/history",
    daynightSensors: "/api/v1/runtime/daynight/sensors",
    heartbeat: "/api/v1/runtime/heartbeat",
    homeAssistant: "/api/v1/runtime/ha",
    media: "/api/v1/runtime/media",
    mediaMetrics: "/api/v1/runtime/media/metrics",
    motion: "/api/v1/runtime/motion",
    sensor: "/api/v1/runtime/sensor",
    system: "/api/v1/runtime/system",
  },
  storage: {
    overlay: "/api/v1/storage/overlay",
    sd: "/api/v1/storage/sd",
  },
  webuiApiKey: "/api/v1/webui/api-key",
} as const;
