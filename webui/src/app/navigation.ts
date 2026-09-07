export type PageId =
  | "preview"
  | "status"
  | "usage"
  | "crontab"
  | "onvif-info"
  | "prudynt-info"
  | "thingino-info"
  | "kernel-log"
  | "streamer-log"
  | "system-log"
  | "kernel-modules"
  | "network-sockets"
  | "os-release"
  | "processes"
  | "overlay"
  | "network"
  | "time"
  | "audio"
  | "access"
  | "webui"
  | "admin"
  | "logging"
  | "daynight"
  | "gpio"
  | "imaging"
  | "streams"
  | "osd"
  | "motion-privacy"
  | "sensor"
  | "home-assistant"
  | "recorder"
  | "timelapse"
  | "files"
  | "storage"
  | "network-probe"
  | "reset"
  | "help";

export interface PageDefinition {
  id: PageId;
  label: string;
  group: string;
}

export const pages: PageDefinition[] = [
  { id: "preview", label: "Preview", group: "Preview" },
  { id: "status", label: "System status", group: "Information" },
  { id: "usage", label: "System usage", group: "Information" },
  { id: "crontab", label: "Scheduled tasks", group: "Information" },
  { id: "onvif-info", label: "ONVIF", group: "Information" },
  { id: "prudynt-info", label: "Prudynt", group: "Information" },
  { id: "thingino-info", label: "Thingino", group: "Information" },
  { id: "kernel-log", label: "Kernel log", group: "Information" },
  { id: "streamer-log", label: "Streamer log", group: "Information" },
  { id: "system-log", label: "System log", group: "Information" },
  { id: "processes", label: "Processes", group: "Information" },
  { id: "network-sockets", label: "Network connections", group: "Information" },
  { id: "kernel-modules", label: "Kernel modules", group: "Information" },
  { id: "os-release", label: "OS release", group: "Information" },
  { id: "overlay", label: "Overlay partition", group: "Information" },
  { id: "network", label: "Network", group: "Settings" },
  { id: "time", label: "Time", group: "Settings" },
  { id: "audio", label: "Audio", group: "Settings" },
  { id: "access", label: "RTSP / ONVIF", group: "Settings" },
  { id: "webui", label: "Web interface", group: "Settings" },
  { id: "admin", label: "Admin profile", group: "Settings" },
  { id: "logging", label: "Remote logging", group: "Settings" },
  { id: "daynight", label: "Day / night", group: "Settings" },
  { id: "gpio", label: "GPIO", group: "Settings" },
  { id: "imaging", label: "Image quality", group: "Streamer" },
  { id: "streams", label: "Streams", group: "Streamer" },
  { id: "osd", label: "OSD", group: "Streamer" },
  { id: "motion-privacy", label: "Motion and privacy", group: "Streamer" },
  { id: "sensor", label: "Sensor data", group: "Streamer" },
  { id: "home-assistant", label: "Home Assistant", group: "Services" },
  { id: "recorder", label: "Recorder", group: "Services" },
  { id: "timelapse", label: "Timelapse", group: "Services" },
  { id: "files", label: "Files", group: "Tools" },
  { id: "storage", label: "SD storage", group: "Tools" },
  { id: "network-probe", label: "Network test", group: "Tools" },
  { id: "reset", label: "Restart and reset", group: "Tools" },
  { id: "help", label: "About", group: "Help" },
];

export const primaryGroups = ["Preview", "Information", "Settings", "Streamer", "Services", "Tools", "Help"];

export interface SectionNavigationGroup {
  label: string;
  pages: readonly PageId[];
}

export const sectionNavigation: Partial<Record<string, readonly SectionNavigationGroup[]>> = {
  Information: [
    { label: "Overview", pages: ["status", "usage"] },
    { label: "Configuration", pages: ["crontab", "onvif-info", "prudynt-info", "thingino-info"] },
    { label: "Logs", pages: ["kernel-log", "streamer-log", "system-log"] },
    { label: "Runtime", pages: ["processes", "network-sockets", "kernel-modules", "os-release"] },
    { label: "Storage", pages: ["overlay"] },
  ],
  Settings: [
    { label: "Connectivity", pages: ["network", "access"] },
    { label: "Camera", pages: ["time", "audio", "daynight", "gpio"] },
    { label: "Administration", pages: ["webui", "admin", "logging"] },
  ],
  Streamer: [
    { label: "Video", pages: ["imaging", "streams", "osd"] },
    { label: "Detection", pages: ["motion-privacy", "sensor"] },
  ],
  Services: [
    { label: "Integrations", pages: ["home-assistant"] },
    { label: "Capture", pages: ["recorder", "timelapse"] },
  ],
  Tools: [
    { label: "Storage", pages: ["files", "storage"] },
    { label: "Diagnostics", pages: ["network-probe"] },
    { label: "Maintenance", pages: ["reset"] },
  ],
};

export function pageFromLocation(): PageId {
  const id = location.hash.replace(/^#\/?/, "") as PageId;
  return pages.some((page) => page.id === id) ? id : "preview";
}

export function pageDefinition(id: PageId): PageDefinition {
  return pages.find((page) => page.id === id) ?? pages[0]!;
}
