import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeNetwork, decodeTime, decodeWifiScan } from "../../api/decode";
import { button, element, setMessage } from "../../app/dom";
import { type ConfigFormSpec, type FieldOption } from "../../app/forms";
import { bool, text, secret, section } from "./common";

const primaryTimezoneRegions = new Set([
  "Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic",
  "Australia", "Europe", "Indian", "Pacific",
]);

export function visibleTimezoneOptions(options: FieldOption[], currentValue: unknown): FieldOption[] {
  const current = typeof currentValue === "string" ? currentValue : "";
  return options.filter(({ value }) => {
    if (value === current || value === "Etc/UTC") return true;
    const [region, city] = value.split("/");
    return Boolean(city) && primaryTimezoneRegions.has(region!);
  });
}

export function resolveTimezoneSearch(query: unknown, options: unknown): string | undefined {
  if (typeof query !== "string" || !Array.isArray(options)) return undefined;
  const normalized = query.trim().toLocaleLowerCase("en-US");
  if (!normalized) return undefined;
  const names = options.flatMap((option) => {
    if (typeof option === "string") return [option];
    if (typeof option !== "object" || option === null || Array.isArray(option)) return [];
    const name = (option as { name?: unknown }).name;
    return typeof name === "string" ? [name] : [];
  });
  const exact = names.filter((name) => {
    const folded = name.toLocaleLowerCase("en-US");
    return folded === normalized || folded.split("/").at(-1) === normalized;
  });
  if (exact.length === 1) return exact[0];
  const partial = names.filter((name) => name.toLocaleLowerCase("en-US").includes(normalized));
  return partial.length === 1 ? partial[0] : undefined;
}
export const network: ConfigFormSpec = {
  eyebrow: "Settings / network",
  title: "Network settings",
  description: "Saved wireless credentials, addressing and hostname. Restart the camera to apply changes to the running network. Check the new connection details before restarting.",
  successMessage: () => "Settings saved. Restart the camera to apply Wi-Fi, addressing and hostname changes. The current connection has not been reconfigured.",
  endpoint: routes.config.network,
  decode: decodeNetwork,
  groupSections: true,
  fields: [
    ...section("Wi-Fi network", [
      text("wifi.ssid", "Network name (SSID)"),
      { ...text("wifi.bssid", "Preferred access point (BSSID)"), pattern: "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|" },
      secret("wifi.password", "Wi-Fi password", "Leave blank to keep the stored password. The camera never returns or masks the current secret."),
    ]),
    ...section("Wireless addressing", [
      bool("interfaces.wlan0.enabled", "Enable Wi-Fi"),
      bool("interfaces.wlan0.dhcp", "Obtain an IPv4 address automatically (DHCP)"),
      { ...text("interfaces.wlan0.address", "IPv4 address"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.netmask", "Netmask"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.gateway", "Gateway"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.broadcast", "Broadcast address"), enabledWhen: { path: "interfaces.wlan0.dhcp", equals: false } },
      { ...text("interfaces.wlan0.mac", "Wi-Fi MAC address"), readOnly: true },
      { ...bool("interfaces.wlan0.link_up", "Wi-Fi link active"), readOnly: true },
    ]),
    ...section("Device identity and DNS", [
      { ...text("hostname", "Hostname", true), pattern: "[A-Za-z0-9][A-Za-z0-9.-]{0,62}", maxLength: 63 },
      text("dns.primary", "Primary DNS"),
      text("dns.secondary", "Secondary DNS"),
    ]),
  ],
  saveTransform: (value) => {
    const body = structuredClone(value);
    const wifi = body.wifi as JsonObject;
    delete wifi.password_set;
    const wifiAp = body.wifi_ap as JsonObject;
    const interfaces = body.interfaces as JsonObject;
    const wlan0 = interfaces.wlan0 as JsonObject;
    return {
      ...body,
      wifi_ap: { ...wifiAp, enabled: false },
      interfaces: { ...interfaces, wlan0: { ...wlan0, ipv6: false } },
    };
  },
};

export const time: ConfigFormSpec = {
  eyebrow: "Settings / time",
  title: "Time and timezone",
  description: "Choose the camera timezone. The local clock is read back after every load and save so the change is visible without a reboot.",
  endpoint: routes.config.time,
  decode: (value) => {
    const decoded = decodeTime(value);
    if (decoded.source === "raptor") {
      if (typeof decoded.timezone_reload_supported !== "boolean" || typeof decoded.timezone_applied !== "boolean" || (decoded.timezone_applied && !decoded.timezone_reload_supported)) throw new TypeError("Timezone apply state is inconsistent.");
      const applyStatus = !decoded.timezone_reload_supported
        ? "A required media service could not be verified. Timezone changes are unavailable."
        : decoded.timezone_applied ? "Applied to required services" : "Not confirmed; reload and retry saving";
      return { ...decoded, time_control: decoded.timezone_reload_supported, raptor_time: true, timezone_apply_status: applyStatus };
    }
    return { ...decoded, time_control: true, raptor_time: false };
  },
  save: async (client, value, loaded) => {
    const reply = await client.postJson<unknown>(routes.config.time, value);
    if (loaded.source === "raptor" && (typeof reply !== "object" || reply === null || !("persistent" in reply) || reply.persistent !== true || !("applied" in reply) || reply.applied !== true)) throw new TypeError("Raptor timezone application was not confirmed. Reload and retry saving.");
  },
  groupSections: true,
  loadTransform: loadTimeConfig,
  fields: [
    ...section("Timezone", [
      {
        path: "timezone",
        label: "Timezone",
        type: "search",
        optionsFrom: "timezone_options",
        optionsFilter: visibleTimezoneOptions,
        placeholder: "Search by city, for example Helsinki",
        required: true,
        description: "Search by city or choose a Region/City IANA timezone. Technical fixed-offset aliases stay hidden unless one is currently selected.",
      },
      { ...text("current_local_time", "Camera local time", true), readOnly: true, description: "Read-only time reported by the camera after the last load." },
    ]),
    { ...text("timezone_apply_status", "Timezone application"), readOnly: true, visibleWhen: { path: "raptor_time", equals: true } },
    ...section("Advanced", [
      bool("dhcp_ignore_timezone", "Ignore DHCP timezone"),
      text("ntp_server_0", "NTP server 1"),
      text("ntp_server_1", "NTP server 2"),
      text("ntp_server_2", "NTP server 3"),
      text("ntp_server_3", "NTP server 4"),
    ]),
  ],
  saveTransform: buildTimeUpdate,
  validate: (value) => {
    const servers = [value.ntp_server_0, value.ntp_server_1, value.ntp_server_2, value.ntp_server_3];
    return servers.some((server) => typeof server === "string" && server.trim()) ? undefined : "Configure at least one NTP server.";
  },
};

for (const field of time.fields) {
  if (!field.readOnly) field.enabledWhen = { path: "time_control", equals: true };
}

/**
 * Build the canonical time mutation from the visible form model.  The
 * read-only camera clock and any response-only metadata are never sent back.
 * The backend remains the authority for the IANA -> /etc/TZ mapping.
 */
export function buildTimeUpdate(value: JsonObject): JsonObject {
  const next = structuredClone(value);
  const timezone = resolveTimezoneSearch(next.timezone, next.timezone_options);
  delete next.current_local_time;
  delete next.timezone_options;
  delete next.current_unix_time;
  delete next.current_unix_ms;
  delete next.local_time;
  delete next.sync_status_raw_base64;
  delete next.tz_name;
  delete next.tz_data;
  if (next.source === "raptor" && next.time_control !== true) throw new TypeError("Timezone reload is unavailable.");
  for (const key of ["source", "time_control", "raptor_time", "timezone_reload_supported", "timezone_applied", "timezone_apply_status"]) delete next[key];
  if (!timezone) throw new TypeError("Choose one supported timezone from the suggestions.");
  next.timezone = timezone;
  return { ...next, action: "update" };
}

function unixMilliseconds(value: JsonObject): number {
  if (typeof value.current_unix_ms === "number") return value.current_unix_ms;
  if (typeof value.current_unix_time === "number") return value.current_unix_time * 1000;
  throw new TypeError("Camera did not return a current clock value.");
}

export function cameraLocalTime(value: JsonObject): string {
  const timezone = value.timezone;
  if (typeof timezone !== "string" || !timezone) throw new TypeError("Camera timezone is missing.");
  let formatter: Intl.DateTimeFormat;
  try {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
      timeZoneName: "short",
    });
  } catch {
    throw new TypeError(`Unsupported camera timezone: ${timezone}`);
  }
  const parts = Object.fromEntries(formatter.formatToParts(new Date(unixMilliseconds(value))).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} ${parts.timeZoneName ?? ""}`.trim();
}

export function loadTimeConfig(value: JsonObject): JsonObject {
  const next = structuredClone(value);
  next.current_local_time = cameraLocalTime(next);
  return next;
}

export function addTimeSync(client: ApiClient, rendered: { node: HTMLElement }): () => void {
  const sync = button("Sync time now", "button secondary");
  rendered.node.querySelector(".form-actions")?.prepend(sync);
  const message = rendered.node.querySelector<HTMLElement>(".message")!;
  const clock = rendered.node.querySelector<HTMLInputElement>('[name="current_local_time"]')!;
  let cancelled = false;
  sync.addEventListener("click", async () => {
    if (cancelled || sync.disabled) return;
    sync.disabled = true;
    setMessage(message, "Synchronizing time…");
    let synchronized = false;
    try {
      await client.empty(routes.actions.syncTime, { method: "POST" });
      synchronized = true;
      if (cancelled) return;
      const observed = decodeTime(await client.json<unknown>(routes.config.time));
      if (cancelled) return;
      // Refresh only the observed clock. A full form reload would discard drafts.
      clock.value = cameraLocalTime(observed);
      setMessage(message, "Time synchronized.", "success");
    } catch (error) {
      if (cancelled) return;
      if (synchronized) {
        clock.value = "";
        setMessage(message, "Time synchronized, but the camera clock could not be read back. Reload to check the time.", "error");
      } else {
        setMessage(message, error instanceof Error ? error.message : "Time synchronization failed.", "error");
      }
    } finally {
      if (!cancelled) sync.disabled = false;
    }
  });
  return () => { cancelled = true; };
}

export function addWifiScan(client: ApiClient, rendered: { node: HTMLElement }): void {
  const scan = button("Scan Wi-Fi networks", "button secondary");
  const results = element("div", { className: "wifi-results", attrs: { "aria-live": "polite" } });
  results.hidden = true;
  const toolbar = element("div", { className: "section-toolbar" });
  toolbar.append(scan);
  const wifiSection = rendered.node.querySelector(".form-section-card");
  const firstField = wifiSection?.querySelector(".field") ?? null;
  if (wifiSection) {
    wifiSection.insertBefore(toolbar, firstField);
    wifiSection.append(results);
  } else {
    rendered.node.querySelector(".form-actions")?.prepend(scan);
    rendered.node.querySelector(".form-card")?.append(results);
  }
  const message = rendered.node.querySelector<HTMLElement>(".message")!;
  scan.addEventListener("click", async () => {
    scan.disabled = true;
    setMessage(message, "Scanning for Wi-Fi networks…");
    try {
      const response = decodeWifiScan(await client.json<unknown>(routes.network.wifiScan));
      results.replaceChildren();
      results.hidden = false;
      if (response.networks.length === 0) {
        results.append(element("p", { text: "No Wi-Fi networks were found. Move the camera closer to the access point and scan again." }));
        setMessage(message, "Scan completed; no networks were found.");
        return;
      }
      const networks = [...response.networks].sort((left, right) => (right.signal ?? -999) - (left.signal ?? -999));
      const selector = element("select", { className: "input", attrs: { "aria-label": "Available Wi-Fi networks" } });
      const currentSsid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.ssid"]')?.value ?? "";
      const currentBssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.bssid"]')?.value.toLowerCase() ?? "";
      let currentIndex = 0;
      networks.forEach((network, index) => {
        const current = (network.bssid?.toLowerCase() === currentBssid && currentBssid !== "") || (network.ssid === currentSsid && currentBssid === "");
        if (current) currentIndex = index;
        const details = [network.security, network.signal === undefined ? undefined : `${network.signal} dBm`].filter(Boolean).join(" · ");
        selector.append(element("option", { text: `${network.ssid || "Hidden network"}${details ? ` — ${details}` : ""}${current ? " — current" : ""}`, attrs: { value: String(index) } }));
      });
      selector.value = String(currentIndex);
      const useSelected = button("Use selected network", "button primary");
      useSelected.addEventListener("click", () => {
        const network = networks[Number(selector.value)];
        if (!network) return;
        const ssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.ssid"]');
        const bssid = rendered.node.querySelector<HTMLInputElement>('[name="wifi.bssid"]');
        if (ssid) { ssid.value = network.ssid; ssid.dispatchEvent(new Event("input", { bubbles: true })); }
        if (bssid) { bssid.value = network.bssid ?? ""; bssid.dispatchEvent(new Event("input", { bubbles: true })); }
        setMessage(message, `${network.ssid || "Hidden network"} selected. Enter its password if needed, then save settings.`, "success");
      });
      const count = networks.length;
      results.append(
        element("strong", { text: `${count} Wi-Fi network${count === 1 ? "" : "s"} found` }),
        element("p", { text: "Choose a network from the list. Nothing changes on the camera until you select it and save the form." }),
        selector,
        useSelected,
      );
      setMessage(message, `Scan completed; ${count} network${count === 1 ? "" : "s"} found.`);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Wi-Fi scan failed.";
      results.hidden = false;
      results.replaceChildren(element("p", { className: "tools-warning", text }));
      setMessage(message, text, "error");
    } finally {
      scan.disabled = false;
    }
  });
}
