import type {
  CrontabConfig,
  DayNightSensors,
  DiagnosticInfoResponse,
  FileListResponse,
  HealthResponse,
  JsonObject,
  OverlayStatus,
  RuntimeHeartbeat,
  RuntimeSystem,
  SdStatus,
  SensorIdentity,
  UsageSection,
} from "../api/contracts";
import { ApiClient } from "../api/client";
import {
  decodeDaynightHistory,
  decodeDaynightSensors,
  decodeCrontab,
  decodeDiagnosticInfo,
  decodeFileList,
  decodeFileRemove,
  decodeFileText,
  decodeFileTextWrite,
  decodeHealth,
  decodeHeartbeat,
  decodeNetworkProbeMetadata,
  decodeNetworkProbeResponse,
  decodeMutationSuccess,
  decodeOverlayStatus,
  decodeRuntimeSystem,
  decodeSdFormatAccepted,
  decodeSdStatus,
  decodeSensorIdentity,
} from "../api/decode";
import { routes } from "../api/routes";
import { button, element, formatKiB, setMessage, statusMessage } from "../app/dom";
import type { PageId } from "../app/navigation";

type RenderedPage = { node: HTMLElement; cleanup: () => void };

const TEXT_EXTENSIONS = new Set(["txt", "log", "conf", "cfg", "ini", "json", "yaml", "yml", "xml", "md", "html", "css", "js", "sh"]);
const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "gif", "webp"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "mkv", "avi", "mov", "ts", "h264", "h265", "hevc"]);

function heading(eyebrow: string, title: string, description: string): HTMLElement {
  const header = element("header", { className: "page-heading" });
  header.append(element("span", { className: "eyebrow", text: eyebrow }), element("h1", { text: title }), element("p", { text: description }));
  return header;
}

function decodeBase64(value: string): string {
  try {
    const bytes = Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch { return "The camera returned invalid base64 output."; }
}

function decoded<T>(client: ApiClient, path: string, decoder: (value: unknown) => T): Promise<T> {
  return client.json<unknown>(path).then(decoder);
}

export function formatTimestamp(value: string | number): string {
  const seconds = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "Not reported";
  const date = new Date(seconds * 1000);
  const pad = (part: number): string => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "Not reported";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.floor(seconds % 60);
  return days > 0 ? `${days}d ${hours}h ${minutes}m` : `${hours}h ${minutes}m ${rest}s`;
}

function formatFileSize(value: string): string {
  if (value === "-") return "—";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return value;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function addKeyValue(parent: HTMLElement, label: string, value: string): void {
  const row = element("div", { className: "tools-key-value" });
  row.append(element("span", { text: label }), element("strong", { text: value }));
  parent.append(row);
}

function usageCard(label: string, usage: UsageSection): HTMLElement {
  const card = element("section", { className: "card tools-panel" });
  card.append(element("h2", { text: label }));
  addKeyValue(card, "Total", formatKiB(usage.total)); addKeyValue(card, "Used", formatKiB(usage.used)); addKeyValue(card, "Free", formatKiB(usage.free));
  if (usage.active !== undefined) addKeyValue(card, "Active", formatKiB(usage.active));
  if (usage.buffers !== undefined) addKeyValue(card, "Buffers", formatKiB(usage.buffers));
  if (usage.cached !== undefined) addKeyValue(card, "Cached", formatKiB(usage.cached));
  const meter = element("progress", { className: "tools-meter", attrs: { max: "100", "aria-label": `${label} used` } });
  meter.value = usage.total > 0 ? Math.max(0, Math.min(100, usage.used * 100 / usage.total)) : 0;
  card.append(meter);
  return card;
}

function renderInterfaceTable(system: RuntimeSystem): HTMLElement {
  const card = element("section", { className: "card tools-panel tools-wide" });
  card.append(element("h2", { text: "Network interfaces" }), element("p", { className: "tools-muted", text: "Runtime state reported by Control; configuration is managed on the Network page." }));
  const table = element("table", { className: "tools-table" });
  const head = element("tr");
  for (const title of ["Interface", "Link", "Address", "Netmask", "Gateway", "Broadcast", "MAC", "Mode"]) head.append(element("th", { text: title, attrs: { scope: "col" } }));
  const thead = element("thead"); thead.append(head); table.append(thead);
  const body = element("tbody");
  for (const [name, value] of Object.entries(system.network.interfaces).filter(([name]) => name === "wlan0")) {
    const row = element("tr");
    const link = value.link_up === undefined ? "—" : value.link_up ? "Up" : "Down";
    const mode = value.dhcp === undefined ? "—" : value.dhcp ? "DHCP" : "Static";
    for (const text of [name, link, value.address ?? "—", value.netmask ?? "—", value.gateway ?? "—", value.broadcast ?? "—", value.mac ?? "—", mode]) row.append(element("td", { text }));
    body.append(row);
  }
  table.append(body); card.append(table); return card;
}

function renderStatus(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const reload = button("Refresh", "button secondary");
  const summary = element("div", { className: "metric-grid" }); const detail = element("div", { className: "tools-detail-grid" });
  section.append(heading("Information", "System status", "Runtime, memory, storage, network and stream state reported by Thingino Control."), message, reload, summary, detail);
  let cancelled = false;
  async function load(): Promise<void> {
    setMessage(message, "Loading system status…");
    try {
      const [system, heartbeat] = await Promise.all([decoded(client, routes.runtime.system, decodeRuntimeSystem), decoded(client, routes.runtime.heartbeat, decodeHeartbeat)]);
      if (cancelled) return; summary.replaceChildren();
      const values: Array<[string, string]> = [
        ["Timestamp", formatTimestamp(system.timestamp)], ["Uptime", formatDuration(heartbeat.uptime)], ["Media", system.media.media_ready ? "Ready" : "Starting"], ["Prudynt", system.media.prudynt_running ? "Running" : "Stopped"], ["Network", system.network.online ? system.network.ip || "Connected" : "Disconnected"], ["Stream 0", system.media.stream0_enabled ? "Enabled" : "Disabled"], ["Stream 1", system.media.stream1_enabled ? "Enabled" : "Disabled"], ["Day / night", heartbeat.daynight_mode], ["Motion", heartbeat.motion_active ? "Active" : heartbeat.motion_enabled ? "Monitoring" : "Disabled"], ["Privacy", heartbeat.privacy_enabled ? "Enabled" : "Disabled"], ["Recorder", heartbeat.rec_ch0 || heartbeat.rec_ch1 ? `ch${heartbeat.rec_ch0 ? "0" : "1"}` : "Idle"], ["Timelapse", heartbeat.timelapse_enabled ? "Enabled" : "Disabled"],
      ];
      for (const [label, value] of values) { const card = element("div", { className: "card metric-card" }); card.append(element("span", { text: label }), element("strong", { text: value })); summary.append(card); }
      detail.replaceChildren(usageCard("Memory", system.memory), usageCard("Overlay", system.overlay), usageCard("Extra storage", system.extras));
      const runtime = element("section", { className: "card tools-panel" }); runtime.append(element("h2", { text: "Runtime state" }));
      addKeyValue(runtime, "Brightness", heartbeat.daynight_brightness === null ? "Not reported" : String(heartbeat.daynight_brightness)); addKeyValue(runtime, "Total gain", heartbeat.total_gain === null ? "Not reported" : String(heartbeat.total_gain)); addKeyValue(runtime, "Color mode", heartbeat.color_mode === null ? "Not reported" : heartbeat.color_mode === 0 ? "Color" : "Monochrome"); addKeyValue(runtime, "IR-cut", heartbeat.ircut_state === null ? "Not reported" : heartbeat.ircut_state ? "On" : "Off"); addKeyValue(runtime, "IR850", heartbeat.ir850_state === null ? "Not reported" : heartbeat.ir850_state ? "On" : "Off");
      detail.append(runtime, renderInterfaceTable(system)); setMessage(message);
    } catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load system status.", "error"); }
  }
  reload.addEventListener("click", () => void load()); void load(); return { node: section, cleanup: () => { cancelled = true; } };
}

function renderUsage(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" });
  const message = statusMessage();
  const refresh = button("Refresh", "button secondary");
  const detail = element("div", { className: "tools-detail-grid" });
  section.append(heading("Information", "System usage", "Memory, overlay and extra-storage usage reported by Thingino Control."), message, refresh, detail);
  let cancelled = false;
  async function load(): Promise<void> {
    setMessage(message, "Loading system usage…");
    try {
      const system = await decoded(client, routes.runtime.system, decodeRuntimeSystem);
      if (cancelled) return;
      detail.replaceChildren(usageCard("Memory", system.memory), usageCard("Overlay", system.overlay), usageCard("Extra storage", system.extras));
      setMessage(message);
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load system usage.", "error");
    }
  }
  refresh.addEventListener("click", () => void load());
  void load();
  return { node: section, cleanup: () => { cancelled = true; } };
}

function renderOverlay(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const refresh = button("Refresh", "button secondary");
  const card = element("section", { className: "card tools-panel" }); const usage = element("div", { className: "tools-usage" }); const listing = element("pre", { className: "output", text: "Loading…" }); const updated = element("small", { className: "tools-muted" });
  card.append(element("h2", { text: "Overlay usage" }), usage, updated, element("h3", { text: "Directory listing" }), listing); section.append(heading("Information", "Overlay partition", "Read-only usage and the bounded directory listing reported by Control."), message, refresh, card);
  let cancelled = false;
  async function load(): Promise<void> {
    setMessage(message, "Loading overlay status…");
    try { const value: OverlayStatus = await decoded(client, routes.storage.overlay, decodeOverlayStatus); if (cancelled) return; usage.replaceChildren(); const meter = element("progress", { className: "tools-meter", attrs: { max: "100", "aria-label": "Overlay used" } }); meter.value = value.usage.percent; usage.append(element("strong", { className: "tools-usage-label", text: value.usage.label }), meter, element("span", { text: `${value.usage.percent}% used · ${value.usage.state}` })); listing.textContent = decodeBase64(value.listing_base64) || "No files reported."; updated.textContent = `Updated ${new Date().toLocaleTimeString()} · ${value.path}`; setMessage(message); }
    catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load overlay status.", "error"); }
  }
  refresh.addEventListener("click", () => void load()); void load(); return { node: section, cleanup: () => { cancelled = true; } };
}

function downloadText(filename: string, text: string, type = "text/plain"): void {
  const url = URL.createObjectURL(new Blob([text], { type })); const link = element("a", { attrs: { href: url, download: filename } }); document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  const input = element("textarea", { className: "clipboard-buffer", attrs: { readonly: "" } }); input.value = text; document.body.append(input);
  try { input.select(); if (!document.execCommand("copy")) throw new Error("Clipboard is unavailable."); } finally { input.remove(); }
}

function diagnosticText(value: DiagnosticInfoResponse): string { return value.commands.map((entry) => `# ${entry.command}\n${decodeBase64(entry.output_base64)}`).join("\n\n"); }

const INFORMATION_SOURCES: Partial<Record<PageId, { query: string; title: string; description: string }>> = {
  "onvif-info": { query: "onvif", title: "ONVIF", description: "Secret-redacted /etc/onvif.json snapshot." },
  "prudynt-info": { query: "prudynt", title: "Prudynt", description: "Secret-redacted /etc/prudynt.json snapshot." },
  "thingino-info": { query: "thingino", title: "Thingino", description: "Secret-redacted /etc/thingino.json snapshot." },
  "kernel-log": { query: "dmesg", title: "Kernel log", description: "Bounded, non-destructive kernel ring snapshot read directly by Control." },
  "streamer-log": { query: "logcat", title: "Streamer log", description: "Streamer log source exposed directly by Control when supported." },
  "system-log": { query: "logread", title: "System log", description: "Bounded BusyBox system-log ring snapshot read directly by Control." },
  "kernel-modules": { query: "lsmod", title: "Kernel modules", description: "Loaded kernel modules read from /proc/modules." },
  "network-sockets": { query: "netstat", title: "Network connections", description: "Human-readable TCP, UDP and UNIX socket state formatted natively by Control." },
  "os-release": { query: "release", title: "OS release", description: "Operating-system release metadata." },
  processes: { query: "top", title: "Processes", description: "Bounded process, memory and thread snapshot read directly from /proc." },
};

function renderInformationSource(client: ApiClient, id: PageId): RenderedPage | null {
  const source = INFORMATION_SOURCES[id];
  if (!source) return null;
  const definition = source;
  const section = element("section", { className: "page" });
  const message = statusMessage();
  const refresh = button("Refresh", "button secondary");
  const copy = button("Copy", "button quiet");
  const download = button("Download", "button quiet");
  const actions = element("div", { className: "tools-toolbar" }); actions.append(refresh, copy, download);
  const output = element("pre", { className: "output", text: "Loading…" });
  const card = element("section", { className: "card tools-panel" }); card.append(actions, output);
  section.append(heading("Information", definition.title, definition.description), message, card);
  let cancelled = false;
  let lastOutput = "";
  async function load(): Promise<void> {
    setMessage(message, `Loading ${definition.title.toLowerCase()}…`);
    try {
      const value = await decoded(client, routes.diagnostics.info(definition.query), decodeDiagnosticInfo);
      if (cancelled) return;
      lastOutput = diagnosticText(value);
      output.textContent = lastOutput || "No output returned.";
      copy.disabled = !lastOutput; download.disabled = !lastOutput;
      setMessage(message);
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : `Unable to load ${definition.title.toLowerCase()}.`, "error");
    }
  }
  refresh.addEventListener("click", () => void load());
  copy.addEventListener("click", async () => { try { await copyText(lastOutput); setMessage(message, "Output copied.", "success"); } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to copy output.", "error"); } });
  download.addEventListener("click", () => downloadText(`thingino-${definition.query}-${new Date().toISOString().replace(/[:.]/g, "-")}.log`, lastOutput));
  copy.disabled = true; download.disabled = true; void load();
  return { node: section, cleanup: () => { cancelled = true; } };
}

function renderCrontab(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" });
  const message = statusMessage();
  const editor = element("textarea", { className: "input code-editor", attrs: { "aria-label": "Root crontab", spellcheck: "false" } });
  const save = button("Save scheduled tasks", "button primary");
  const reload = button("Reload", "button secondary");
  const backup = button("Download backup", "button quiet");
  const actions = element("div", { className: "form-actions" }); actions.append(save, reload, backup);
  const card = element("section", { className: "card tools-panel file-editor" });
  card.append(
    element("p", { text: "Edit the root crontab directly. Empty lines, comments, environment assignments, five-field schedules and supported @ schedules are accepted." }),
    element("p", { className: "tools-warning", text: "Scheduled commands run as root. Review every line before saving." }),
    editor,
    actions,
  );
  section.append(heading("Information", "Scheduled tasks", "The camera cron schedule, edited through the bounded Control API without a request-time shell or helper."), message, card);
  let cancelled = false;
  let loaded = false;
  let original = "";
  let maxBytes = 0;
  const beforeRouteChange = (event: Event): void => { if (editor.value !== original && !window.confirm("Discard unsaved scheduled-task changes?")) event.preventDefault(); };
  window.addEventListener("thingino:before-route-change", beforeRouteChange);
  function refreshButtons(): void {
    const bytes = new TextEncoder().encode(editor.value).length;
    save.disabled = !loaded || editor.value === original || bytes > maxBytes;
    editor.setCustomValidity(bytes > maxBytes ? `Schedule exceeds the ${maxBytes}-byte limit.` : "");
  }
  async function load(): Promise<void> {
    loaded = false; refreshButtons(); setMessage(message, "Loading scheduled tasks…");
    try {
      const value: CrontabConfig = await decoded(client, routes.config.crontab, decodeCrontab);
      if (cancelled) return;
      original = value.content; maxBytes = value.max_bytes; editor.value = value.content; loaded = true; refreshButtons(); setMessage(message);
    } catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load scheduled tasks.", "error"); }
  }
  async function saveSchedule(): Promise<void> {
    if (!loaded || save.disabled || !editor.reportValidity()) return;
    setMessage(message, "Saving scheduled tasks…");
    try {
      decodeMutationSuccess(await client.postJson<unknown>(routes.config.crontab, { content: editor.value }));
      original = editor.value; refreshButtons(); setMessage(message, "Scheduled tasks saved.", "success");
    } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to save scheduled tasks.", "error"); }
  }
  editor.addEventListener("input", refreshButtons);
  editor.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void saveSchedule(); } });
  save.addEventListener("click", () => void saveSchedule());
  reload.addEventListener("click", () => { if (editor.value === original || window.confirm("Discard unsaved scheduled-task changes?")) void load(); });
  backup.addEventListener("click", () => downloadText(`root-crontab-${new Date().toISOString().replace(/[:.]/g, "-")}.txt`, original));
  void load();
  return { node: section, cleanup: () => { cancelled = true; window.removeEventListener("thingino:before-route-change", beforeRouteChange); } };
}

function isTextFile(path: string): boolean { const extension = path.split(".").pop()?.toLowerCase(); return extension !== undefined && TEXT_EXTENSIONS.has(extension); }
function mediaKind(path: string): "image" | "video" | null { const extension = path.split(".").pop()?.toLowerCase(); if (extension && IMAGE_EXTENSIONS.has(extension)) return "image"; if (extension && VIDEO_EXTENSIONS.has(extension)) return "video"; return null; }

function renderFiles(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const toolbar = element("form", { className: "file-toolbar" }); const pathInput = element("input", { className: "input", attrs: { value: "/", "aria-label": "Folder path" } }); const open = element("button", { className: "button primary", text: "Open folder", attrs: { type: "submit" } }); toolbar.append(pathInput, open);
  const breadcrumbs = element("nav", { className: "file-breadcrumbs", attrs: { "aria-label": "Path" } }); const table = element("div", { className: "card file-list" }); const editorCard = element("section", { className: "card tools-panel file-editor", attrs: { hidden: "" } }); const editorTitle = element("h2", { text: "Text editor" }); const editorStatus = element("small", { className: "tools-muted", text: "No file selected." }); const editor = element("textarea", { className: "input code-editor", attrs: { "aria-label": "Text file contents", spellcheck: "false" } }); const wrap = element("input", { className: "switch-input", attrs: { type: "checkbox", checked: "" } }); const save = button("Save", "button primary"); const reload = button("Reload", "button secondary"); const backup = button("Download backup", "button quiet"); const closeEditor = button("Close editor", "button quiet"); const editorActions = element("div", { className: "form-actions" }); editorActions.append(save, reload, backup, closeEditor); editorCard.append(editorTitle, editorStatus, editor, element("label", { text: "Wrap lines" }), wrap, editorActions);
  section.append(heading("Tools", "Files", "Browse /mnt and /media roots, preview media, edit allowlisted text files and remove regular files with confirmation."), message, toolbar, breadcrumbs, table, editorCard); let cancelled = false; let currentPath: string | null = null; let originalContent = ""; let dirty = false; let currentWritable = false;
  const beforeUnload = (event: BeforeUnloadEvent): void => { if (!dirty) return; event.preventDefault(); event.returnValue = ""; };
  const beforeRouteChange = (event: Event): void => {
    if (dirty && !window.confirm("Discard the unsaved file changes?")) event.preventDefault();
  };
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("thingino:before-route-change", beforeRouteChange);
  function setDirty(value: boolean): void { dirty = value; save.disabled = !dirty || !currentWritable; }
  function backupCurrent(): void { if (!currentPath) return; const fileName = currentPath.split("/").pop() || "backup.txt"; downloadText(`${fileName}.backup-${new Date().toISOString().replace(/[:.]/g, "-")}`, originalContent); }
  async function editText(path: string): Promise<void> { try { setMessage(message, "Loading text file…"); const value = await decoded(client, routes.files.text(path), decodeFileText); if (cancelled) return; currentPath = path; originalContent = decodeBase64(value.content); editor.value = originalContent; currentWritable = value.writable; editor.disabled = !value.writable; editorTitle.textContent = `Text editor · ${path}`; editorStatus.textContent = value.writable ? `${value.size} bytes · ${value.lines} lines · ready` : `${value.size} bytes · read-only`; editorCard.hidden = false; setDirty(false); setMessage(message); editor.focus(); } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to open text file.", "error"); } }
  async function load(path: string): Promise<void> {
    setMessage(message, "Loading folder…");
    try {
      const value: FileListResponse = await decoded(client, routes.files.list(path), decodeFileList); if (cancelled) return; pathInput.value = value.directory; breadcrumbs.replaceChildren();
      for (const crumb of value.breadcrumbs) { const link = button(crumb.label, "file-name"); link.addEventListener("click", () => void load(crumb.path)); breadcrumbs.append(link); if (crumb.path !== value.directory) breadcrumbs.append(element("span", { className: "tools-muted", text: "/" })); }
      table.replaceChildren(); if (value.parent !== value.directory) { const up = button("Parent folder", "file-row file-parent"); up.addEventListener("click", () => void load(value.parent)); table.append(up); }
      for (const entry of value.entries) {
        const row = element("div", { className: "file-row" }); const name = entry.is_dir ? button(entry.name, "file-name") : element("strong", { text: entry.name }); if (entry.is_dir) name.addEventListener("click", () => void load(entry.path)); const meta = element("small", { text: `${entry.is_dir ? "Folder" : formatFileSize(entry.size)} · ${entry.perm} · ${formatTimestamp(entry.time)}` }); const actions = element("div", { className: "file-actions" });
        if (!entry.is_dir && !entry.is_link) {
          actions.append(element("a", { className: "button quiet", text: "Download", attrs: { href: routes.media.file(entry.path, "download"), download: entry.name } })); const kind = mediaKind(entry.path); if (kind) actions.append(element("a", { className: "button quiet", text: kind === "image" ? "Preview" : "Play", attrs: { href: routes.media.file(entry.path, "play"), target: "_blank", rel: "noopener" } }));
          if (isTextFile(entry.path)) { const edit = button("Edit text", "button quiet"); edit.addEventListener("click", () => void editText(entry.path)); actions.append(edit); }
          if (entry.deletable) { const remove = button("Delete", "button quiet"); remove.addEventListener("click", async () => { if (!window.confirm(`Delete ${entry.path}? This cannot be undone.`)) return; try { await client.empty(routes.files.remove(entry.path), { method: "POST" }); setMessage(message, "File deleted.", "success"); await load(value.directory); } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to delete file.", "error"); } }); actions.append(remove); }
        }
        if (entry.is_link) meta.textContent += ` · link to ${entry.link_target}`; row.append(name, meta, actions); table.append(row);
      }
      setMessage(message);
    } catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to list files.", "error"); }
  }
  toolbar.addEventListener("submit", (event) => { event.preventDefault(); void load(pathInput.value); }); editor.addEventListener("input", () => setDirty(editor.value !== originalContent)); wrap.addEventListener("change", () => { editor.classList.toggle("file-editor-nowrap", !wrap.checked); });
  async function saveText(): Promise<void> { if (!currentPath || !dirty || !currentWritable) return; try { setMessage(message, "Saving text file…"); const result = decodeFileTextWrite(await client.postText<unknown>(routes.files.text(currentPath), editor.value)); originalContent = editor.value; setDirty(false); editorStatus.textContent = `${result.size} bytes · ${result.lines} lines · saved`; setMessage(message, "Text file saved.", "success"); } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to save text file.", "error"); } }
  save.addEventListener("click", () => void saveText());
  editor.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void saveText(); } });
  reload.addEventListener("click", () => { if (currentPath && (!dirty || window.confirm("Discard unsaved changes and reload?"))) void editText(currentPath); }); backup.addEventListener("click", backupCurrent); closeEditor.addEventListener("click", () => { if (dirty && !window.confirm("Discard unsaved changes?")) return; editorCard.hidden = true; currentPath = null; setDirty(false); }); void load(pathInput.value);
  return { node: section, cleanup: () => { cancelled = true; window.removeEventListener("beforeunload", beforeUnload); window.removeEventListener("thingino:before-route-change", beforeRouteChange); } };
}

function renderStorage(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const refresh = button("Refresh", "button secondary"); const summary = element("div", { className: "tools-detail-grid" }); section.append(heading("Tools", "SD storage", "Inspect the SD card, mounted filesystem and capacity."), message, refresh, summary); let cancelled = false;
  async function load(showLoading = true): Promise<void> {
    if (showLoading) setMessage(message, "Loading SD storage status…");
    try {
      const value: SdStatus = await decoded(client, routes.storage.sd, decodeSdStatus); if (cancelled) return;
      const data = value.data; summary.replaceChildren();
      const status = element("section", { className: "card tools-panel" }); status.append(element("h2", { text: data.has_sdcard ? "SD card detected" : "SD card not detected" }));
      if (data.device) {
        addKeyValue(status, "Device", data.device.node); addKeyValue(status, "Capacity", formatFileSize(String(data.device.size_bytes)));
        if (data.device.model || data.device.vendor) addKeyValue(status, "Card", [data.device.vendor, data.device.model].filter(Boolean).join(" "));
      } else status.append(element("p", { className: "tools-muted", text: data.messages.not_present }));
      status.append(element("p", { className: "tools-muted", text: data.has_sdcard ? `Detected from ${data.debug.detection === "sysfs" ? "the MMC device tree" : "the active mount table"}.` : "No MMC device or mount is currently visible." }));
      const filesystems = element("section", { className: "card tools-panel" }); filesystems.append(element("h2", { text: "Mounted filesystems" }));
      if (!data.filesystems.length) filesystems.append(element("p", { className: "tools-muted", text: "The card is present but no SD filesystem is mounted." }));
      for (const filesystem of data.filesystems) {
        const item = element("div", { className: "tools-usage" });
        addKeyValue(item, "Partition", filesystem.device); addKeyValue(item, "Mount point", filesystem.mountpoint); addKeyValue(item, "Filesystem", filesystem.filesystem); addKeyValue(item, "Access", filesystem.writable ? "Read and write" : "Read only");
        if (filesystem.total_kib > 0) { addKeyValue(item, "Used", formatKiB(filesystem.used_kib)); addKeyValue(item, "Free", formatKiB(filesystem.free_kib)); const meter = element("progress", { className: "tools-meter", attrs: { max: "100", "aria-label": `${filesystem.mountpoint} used` } }); meter.value = Math.max(0, Math.min(100, filesystem.used_kib * 100 / filesystem.total_kib)); item.append(meter); }
        filesystems.append(item);
      }
      const formatCard = element("section", { className: "card tools-panel" }); formatCard.append(element("h2", { text: "Format SD partition" }), element("p", { className: "tools-warning", text: data.messages.format_warning }));
      if (!data.format.supported) formatCard.append(element("p", { className: "tools-muted", text: "The guarded formatter is unavailable until the exact card, mount and storage worker are ready." }));
      else {
        const start = button("Erase and format as FAT32", "button danger");
        start.disabled = data.format.status === "queued" || data.format.status === "running";
        start.addEventListener("click", async () => {
          if (!window.confirm("Erase every file on /dev/mmcblk0p1 and format it as FAT32? This cannot be undone.")) return;
          try {
            decodeSdFormatAccepted(await client.postJson<unknown>(routes.storage.sd, { action: "format", filesystem: "fat32", confirm: "erase" }));
            start.disabled = true; setMessage(message, "Formatting queued. Keep the card inserted and refresh to follow progress.", "info");
          } catch (error) { setMessage(message, error instanceof Error ? error.message : "Unable to queue formatting.", "error"); }
        });
        formatCard.append(start);
      }
      if (data.format.status !== "idle") addKeyValue(formatCard, "Last operation", data.format.status);
      const formatOutput = decodeBase64(data.format.last_output_b64); if (formatOutput) formatCard.append(element("p", { className: "tools-muted", text: formatOutput }));
      const reports = element("details", { className: "card tools-panel tools-wide" }); const reportTitle = element("summary", { text: "Technical partition and mount details" }); const reportBody = element("div"); reportBody.append(element("h3", { text: "Storage usage" }), element("pre", { className: "output", text: decodeBase64(data.reports.partitions_b64) || "No partition report." }), element("h3", { text: "Mount table" }), element("pre", { className: "output", text: decodeBase64(data.reports.mounts_b64) || "No mounted SD filesystem reported." })); reports.append(reportTitle, reportBody);
      summary.append(status, filesystems, formatCard, reports); if (showLoading) setMessage(message);
    } catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to read storage status.", "error"); }
  }
  refresh.addEventListener("click", () => void load()); void load(); return { node: section, cleanup: () => { cancelled = true; } };
}

function renderNetworkProbe(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" });
  const message = statusMessage();
  const form = element("form", { className: "card form-card" });
  const action = element("select", { className: "input", attrs: { id: "probe-action", "aria-label": "Test type" } });
  const target = element("input", { className: "input", attrs: { id: "probe-target", value: "localhost", required: "", maxlength: "253", "aria-label": "Host name" } });
  const port = element("input", { className: "input", attrs: { id: "probe-port", type: "number", value: "80", min: "1", max: "65535", required: "", "aria-label": "TCP port" } });
  const actionHelp = element("small", { className: "tools-muted" });
  const interfaces = element("small", { className: "tools-muted", text: "Loading available interfaces…" });
  const actionField = element("div", { className: "field" }); actionField.append(element("label", { text: "Test type", attrs: { for: "probe-action" } }), action, actionHelp);
  const targetField = element("div", { className: "field" }); targetField.append(element("label", { text: "Host", attrs: { for: "probe-target" } }), target);
  const portField = element("div", { className: "field" }); portField.append(element("label", { text: "TCP port", attrs: { for: "probe-port" } }), port);
  const run = element("button", { className: "button primary", text: "Run test", attrs: { type: "submit" } });
  const output = element("pre", { className: "output", text: "No test run." });
  const actions = element("div", { className: "form-actions" }); actions.append(run);
  form.append(actionField, targetField, portField, interfaces, actions, output);
  section.append(heading("Tools", "Network test", "Bounded DNS resolve and TCP connect tests provided by Control without request-time helper processes."), message, form);
  let cancelled = false;
  const refreshAction = (): void => {
    portField.hidden = action.value !== "connect";
    port.disabled = action.value !== "connect";
    actionHelp.textContent = action.selectedOptions[0]?.dataset.description ?? "";
  };
  void decoded(client, routes.network.probe, decodeNetworkProbeMetadata).then((metadata) => {
    if (cancelled) return;
    action.replaceChildren();
    for (const item of metadata.actions) action.append(element("option", { text: item.label, attrs: { value: item.id, "data-description": item.description } }));
    action.value = metadata.defaults.action;
    interfaces.textContent = `Available interfaces: ${metadata.interfaces.join(", ") || "none reported"}. Control selects the route automatically.`;
    refreshAction();
  }).catch((error: unknown) => setMessage(message, error instanceof Error ? error.message : "Network-test metadata is unavailable.", "error"));
  action.addEventListener("change", refreshAction);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.checkValidity()) return form.reportValidity();
    try {
      setMessage(message, "Running network test…");
      const probeTarget = action.value === "connect" ? `${target.value}:${port.value}` : target.value;
      const result = decodeNetworkProbeResponse(await client.postForm<unknown>(routes.network.probe, new URLSearchParams({ action: action.value, target: probeTarget })));
      if (!cancelled) { output.textContent = decodeBase64(result.output_b64) || result.command; setMessage(message, result.success ? "Network test completed." : "Network test completed with a failed result.", result.success ? "success" : "error"); }
    } catch (error) {
      setMessage(message, error instanceof Error ? error.message : "Network test failed.", "error");
    }
  });
  return { node: section, cleanup: () => { cancelled = true; } };
}

function renderSensorChart(container: HTMLElement, samples: RuntimeHeartbeat[], night: number, day: number): void {
  container.replaceChildren(); const values = samples.filter((sample) => sample.daynight_brightness !== null); if (!values.length) { container.append(element("p", { className: "tools-muted", text: "No brightness samples reported." })); return; }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 720 220"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Day/night brightness history"); const axes = document.createElementNS("http://www.w3.org/2000/svg", "path"); axes.setAttribute("d", "M 42 12 V 190 H 708"); axes.setAttribute("fill", "none"); axes.setAttribute("stroke", "currentColor"); axes.setAttribute("stroke-opacity", "0.35"); svg.append(axes);
  const threshold = (value: number, color: string, label: string) => { const y = 190 - Math.max(0, Math.min(100, value)) * 1.7; const line = document.createElementNS("http://www.w3.org/2000/svg", "line"); line.setAttribute("x1", "42"); line.setAttribute("x2", "708"); line.setAttribute("y1", String(y)); line.setAttribute("y2", String(y)); line.setAttribute("stroke", color); line.setAttribute("stroke-dasharray", "5 4"); const text = document.createElementNS("http://www.w3.org/2000/svg", "text"); text.setAttribute("class", "sensor-chart-label"); text.setAttribute("x", "48"); text.setAttribute("y", String(y - 4)); text.setAttribute("fill", color); text.textContent = `${label} ${value}%`; svg.append(line, text); };
  threshold(night, "#a13b35", "Night"); threshold(day, "#176b4b", "Day"); const points = values.map((sample, index) => `${42 + index * (666 / Math.max(1, values.length - 1))},${190 - Math.max(0, Math.min(100, sample.daynight_brightness ?? 0)) * 1.7}`).join(" "); const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline"); polyline.setAttribute("points", points); polyline.setAttribute("fill", "none"); polyline.setAttribute("stroke", "#7fcba8"); polyline.setAttribute("stroke-width", "3"); svg.append(polyline); container.append(svg);
}

function renderSensor(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const controls = element("div", { className: "tools-toolbar" }); const refresh = button("Refresh", "button secondary"); const pause = button("Pause", "button secondary"); const clear = button("Clear samples", "button quiet"); const windowSelect = element("select", { className: "input compact", attrs: { "aria-label": "Sample window" } }); for (const points of [100, 300, 600, 1200]) windowSelect.append(element("option", { text: `${points} samples`, attrs: { value: String(points) } })); windowSelect.value = "300"; const exportJson = button("Export JSON", "button quiet"); const exportCsv = button("Export CSV", "button quiet"); controls.append(refresh, pause, clear, windowSelect, exportJson, exportCsv);
  const summary = element("div", { className: "metric-grid" }); const chart = element("div", { className: "sensor-chart card" }); const detail = element("div", { className: "tools-detail-grid" }); const samplesTable = element("table", { className: "tools-table" }); section.append(heading("Streamer", "Sensor data", "Current sensor identity, thresholds and a bounded day/night history. Control retains at most 300 history samples; larger windows include samples collected while this page is open."), message, controls, summary, chart, detail, samplesTable); let cancelled = false; let paused = false; let sampleWindow = 300; let samples: RuntimeHeartbeat[] = []; let thresholds: DayNightSensors = { night_threshold_pct: 0, day_threshold_pct: 100, current: null }; let identity: SensorIdentity | null = null;
  function trim(): void { if (samples.length > sampleWindow) samples = samples.slice(-sampleWindow); }
  function render(): void { summary.replaceChildren(); const latest = samples.at(-1); const values: Array<[string, string]> = [["Brightness", latest?.daynight_brightness === null || latest?.daynight_brightness === undefined ? "Not reported" : String(latest.daynight_brightness)], ["Total gain", latest?.total_gain === null || latest?.total_gain === undefined ? "Not reported" : String(latest.total_gain)], ["Mode", latest?.daynight_mode ?? "Not reported"], ["Night threshold", `${thresholds.night_threshold_pct}%`], ["Day threshold", `${thresholds.day_threshold_pct}%`], ["Samples", String(samples.length)]]; for (const [label, value] of values) { const card = element("div", { className: "card metric-card" }); card.append(element("span", { text: label }), element("strong", { text: value })); summary.append(card); } renderSensorChart(chart, samples, thresholds.night_threshold_pct, thresholds.day_threshold_pct); detail.replaceChildren(); const identityCard = element("section", { className: "card tools-panel" }); identityCard.append(element("h2", { text: "Sensor identity" })); if (identity) { addKeyValue(identityCard, "Sensor", identity.sensor_model); addKeyValue(identityCard, "SoC", identity.soc_model); addKeyValue(identityCard, "Family", identity.soc_family); addKeyValue(identityCard, "IQ file", identity.file_path); addKeyValue(identityCard, "MD5", identity.md5); } else identityCard.append(element("p", { className: "tools-muted", text: "Not reported." })); const current = element("section", { className: "card tools-panel" }); current.append(element("h2", { text: "Current sensor state" }), element("pre", { className: "output", text: thresholds.current ? JSON.stringify(thresholds.current, null, 2) : "No current state reported." })); detail.append(identityCard, current); samplesTable.replaceChildren(); const head = element("tr"); for (const title of ["Time", "Brightness", "Gain", "Mode"]) head.append(element("th", { text: title, attrs: { scope: "col" } })); const thead = element("thead"); thead.append(head); samplesTable.append(thead); const body = element("tbody"); for (const sample of samples.slice(-12).reverse()) { const row = element("tr"); for (const value of [formatTimestamp(sample.time_now), sample.daynight_brightness === null ? "—" : String(sample.daynight_brightness), sample.total_gain === null ? "—" : String(sample.total_gain), sample.daynight_mode]) row.append(element("td", { text: value })); body.append(row); } samplesTable.append(body); }
  let polling = false;
  async function loadInitial(): Promise<void> {
    if (polling) return;
    polling = true;
    setMessage(message, "Loading sensor data…");
    try {
      const sensor = await decoded(client, routes.runtime.sensor, decodeSensorIdentity);
      const sensorState = await decoded(client, routes.runtime.daynightSensors, decodeDaynightSensors);
      const history = await decoded(client, routes.runtime.daynightHistory, decodeDaynightHistory);
      const heartbeat = await decoded(client, routes.runtime.heartbeat, decodeHeartbeat);
      if (cancelled) return;
      identity = sensor;
      thresholds = sensorState;
      samples = [...history, heartbeat];
      trim();
      render();
      setMessage(message);
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load sensor data.", "error");
    } finally {
      polling = false;
    }
  }
  async function poll(): Promise<void> {
    if (paused || cancelled || polling) return;
    polling = true;
    try {
      samples.push(await decoded(client, routes.runtime.heartbeat, decodeHeartbeat));
      trim();
      render();
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Sensor polling failed.", "error");
    } finally {
      polling = false;
    }
  }
  const timer = window.setInterval(() => void poll(), 5000); refresh.addEventListener("click", () => void loadInitial()); pause.addEventListener("click", () => { paused = !paused; pause.textContent = paused ? "Resume" : "Pause"; setMessage(message, paused ? "Sensor polling paused." : "Sensor polling resumed.", "info"); }); clear.addEventListener("click", () => { samples = []; render(); }); windowSelect.addEventListener("change", () => { sampleWindow = Number(windowSelect.value); trim(); render(); }); exportJson.addEventListener("click", () => downloadText("thingino-sensor-history.json", JSON.stringify({ identity, thresholds, samples }, null, 2), "application/json")); exportCsv.addEventListener("click", () => downloadText("thingino-sensor-history.csv", ["time_now,daynight_brightness,total_gain,daynight_mode", ...samples.map((sample) => [sample.time_now, sample.daynight_brightness ?? "", sample.total_gain ?? "", sample.daynight_mode].join(","))].join("\n"), "text/csv")); void loadInitial(); return { node: section, cleanup: () => { cancelled = true; window.clearInterval(timer); } };
}

function renderReset(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const card = element("section", { className: "card danger-card" }); const restartMedia = button("Restart media service", "button secondary"); const reboot = button("Reboot camera", "button danger"); const wipeOverlay = button("Reset writable overlay", "button danger"); card.append(element("h2", { text: "Service and device actions" }), element("p", { text: "These operations can interrupt the connection. Reset writable overlay removes resettable configuration, then reboots. The bootloader environment and protected device partitions are preserved." }), restartMedia, reboot, wipeOverlay); section.append(heading("Tools", "Restart and reset", "Administrative actions provided by Thingino Control."), message, card); let cancelled = false;
  async function invoke(path: string, body: JsonObject | null, confirmation: string): Promise<void> { if (!window.confirm(confirmation)) return; try { if (body) await client.postJson<JsonObject>(path, body); else await client.empty(path, { method: "POST" }); if (!cancelled) setMessage(message, "Action accepted. The connection may close while the camera restarts.", "success"); } catch (error) { if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Action failed.", "error"); } }
  restartMedia.addEventListener("click", () => void invoke(routes.actions.restartPrudynt, null, "Restart the media service now?")); reboot.addEventListener("click", () => void invoke(routes.actions.reboot, null, "Reboot the camera now?")); wipeOverlay.addEventListener("click", () => void invoke(routes.actions.factoryReset, { action: "wipeoverlay" }, "Remove the writable overlay contents and reboot? Protected flash partitions remain unchanged.")); return { node: section, cleanup: () => { cancelled = true; } };
}

function renderHelp(client: ApiClient): RenderedPage {
  const section = element("section", { className: "page" }); const message = statusMessage(); const card = element("section", { className: "card tools-panel" }); const links = element("div", { className: "tools-link-list" }); links.append(element("a", { text: "Thingino project", attrs: { href: "https://thingino.com/", target: "_blank", rel: "noopener" } }), element("a", { text: "Thingino firmware source", attrs: { href: "https://github.com/themactep/thingino-firmware", target: "_blank", rel: "noopener" } }), element("a", { text: "Thingino configuration wiki", attrs: { href: "https://github.com/themactep/thingino-firmware/wiki", target: "_blank", rel: "noopener" } })); const metadata = element("div", { className: "tools-detail-grid" }); card.append(element("h2", { text: "D-Link DCS-6100LHV2 A1" }), element("p", { text: "Local Thingino administration interface. This static frontend talks only to the neutral /api/v1 Control contract and direct media ingress." }), element("p", { className: "tools-muted", text: "Firmware installation and persistent device changes are outside this frontend bundle." }), element("h3", { text: "Project links" }), links, element("h3", { text: "Runtime and API" }), metadata); section.append(heading("Help", "About this interface", "Device identity, Control API metadata and project documentation."), message, card); let cancelled = false;
  decoded(client, routes.health, decodeHealth).then((health: HealthResponse) => { if (cancelled) return; metadata.replaceChildren(); const apiCard = element("div", { className: "card tools-panel" }); apiCard.append(element("h2", { text: "Control API" })); addKeyValue(apiCard, "Name", health.control_api.name); addKeyValue(apiCard, "Version", `v${health.control_api.version}`); addKeyValue(apiCard, "Health", health.status); addKeyValue(apiCard, "Backend", `${health.backend.name} · ${health.backend.available ? "available" : "unavailable"}`); const checks = element("div", { className: "card tools-panel" }); checks.append(element("h2", { text: "Runtime checks" }), element("pre", { className: "output", text: JSON.stringify(health.checks, null, 2) })); metadata.append(apiCard, checks); }).catch(() => { if (!cancelled) metadata.append(element("p", { className: "tools-muted", text: "Control health metadata is unavailable." })); }); return { node: section, cleanup: () => { cancelled = true; } };
}

export function renderToolPage(client: ApiClient, id: PageId): RenderedPage | null {
  const information = renderInformationSource(client, id);
  if (information) return information;
  if (id === "status") return renderStatus(client);
  if (id === "usage") return renderUsage(client);
  switch (id) { case "crontab": return renderCrontab(client); case "overlay": return renderOverlay(client); case "files": return renderFiles(client); case "storage": return renderStorage(client); case "network-probe": return renderNetworkProbe(client); case "sensor": return renderSensor(client); case "reset": return renderReset(client); case "help": return renderHelp(client); default: return null; }
}
