import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { decodeAccess, decodeAdmin, decodeRemoteLogging, decodeWebui } from "../../api/decode";
import { button, element, setMessage, statusMessage } from "../../app/dom";
import { type ConfigFormSpec } from "../../app/forms";
import { applyThemePreference, type ThemePreference } from "../../app/shell";
import { bool, text, number, secret } from "./common";

export const access: ConfigFormSpec = {
  eyebrow: "Settings / RTSP and ONVIF",
  title: "Media access",
  description: "Credentials and endpoints for RTSP clients and ONVIF discovery.",
  endpoint: routes.config.access,
  decode: decodeAccess,
  fields: [
    text("username", "RTSP / ONVIF username"),
    secret("password", "New RTSP / ONVIF password", "Updates RTSP and ONVIF access together. Leave blank to keep the current password."),
    number("rtsp_port", "RTSP port", 1, 65535),
    { ...text("rtsp_ch0", "Main stream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...text("rtsp_ch1", "Substream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...text("rtsp_mic", "Microphone stream path"), pattern: "[A-Za-z0-9._~-]{1,64}", maxLength: 64 },
    { ...number("onvif_port", "ONVIF port", 1, 65535), readOnly: true },
    { ...bool("onvif_enabled", "ONVIF active through same-origin ingress"), readOnly: true },
  ],
  saveTransform: buildAccessUpdate,
};

export function buildAccessUpdate(value: JsonObject): JsonObject {
  const update: JsonObject = { onvif_port: 80, onvif_enabled: true };
  for (const field of ["username", "rtsp_ch0", "rtsp_ch1", "rtsp_mic"] as const) {
    if (typeof value[field] === "string" && value[field].trim()) update[field] = value[field];
  }
  if (typeof value.password === "string" && value.password) update.password = value.password;
  if (typeof value.rtsp_port === "number") update.rtsp_port = value.rtsp_port;
  return update;
}

export const webui: ConfigFormSpec = {
  eyebrow: "Settings / Web interface",
  title: "Web interface",
  description: "Session policy and interface preferences shared by browsers using this camera.",
  endpoint: routes.config.webui,
  decode: decodeWebui,
  onLoaded: (value) => window.dispatchEvent(new CustomEvent("thingino:webui-config", { detail: {
    track_focus: value.track_focus === true,
    focus_timeout: typeof value.focus_timeout === "number" ? value.focus_timeout : 0,
  } })),
  fields: [
    { ...text("username", "Management username"), readOnly: true },
    { path: "theme", label: "Default theme", type: "select", options: [
      { label: "Light", value: "light" },
      { label: "Dark", value: "dark" },
      { label: "Automatic", value: "auto" },
    ] },
    text("auth_bypass_ips", "Trusted / bypass IP addresses"),
    bool("paranoid", "Require authentication for trusted LAN clients"),
    bool("track_focus", "Track Preview window focus"),
    number("focus_timeout", "Stop Preview after losing focus (seconds)", 0, 300),
  ],
  saveTransform: buildWebuiUpdate,
};

export function buildWebuiUpdate(value: JsonObject): JsonObject {
  const update = { ...value };
  delete update.username;
  return update;
}

export const admin: ConfigFormSpec = {
  eyebrow: "Settings / admin profile",
  title: "Admin profile",
  description: "Contact values used by local notification and service integrations.",
  endpoint: routes.config.admin,
  decode: decodeAdmin,
  fields: [text("name", "Name"), text("email", "Email"), text("telegram", "Telegram"), text("discord", "Discord")],
};

export const logging: ConfigFormSpec = {
  eyebrow: "Settings / remote logging",
  title: "Remote logging",
  description: "Forward local system logs to a server on the trusted network.",
  endpoint: routes.config.remoteLogging,
  decode: decodeRemoteLogging,
  fields: [bool("enabled", "Forward logs to the remote server"), text("host", "Log server"), number("port", "Port", 1, 65535), bool("file", "Keep a local log file")],
};

export function addWebuiSecurity(client: ApiClient, rendered: { node: HTMLElement }): void {
  const themeSelect = rendered.node.querySelector<HTMLSelectElement>('[name="theme"]');
  themeSelect?.addEventListener("change", () => {
    if (themeSelect.value === "auto" || themeSelect.value === "light" || themeSelect.value === "dark") {
      try { localStorage.setItem("dcs6100-theme", themeSelect.value); } catch (_) { /* storage can be unavailable */ }
      applyThemePreference(themeSelect.value as ThemePreference);
    }
  });
  const security = element("section", { className: "settings-extras" });
  const passwordCard = element("form", { className: "card compact-form" });
  const passwordMessage = statusMessage();
  const password = element("input", { className: "input", attrs: { type: "password", autocomplete: "new-password", required: "", minlength: "10", maxlength: "128", "aria-label": "New management password" } });
  const confirm = element("input", { className: "input", attrs: { type: "password", autocomplete: "new-password", required: "", minlength: "10", maxlength: "128", "aria-label": "Confirm management password" } });
  const change = element("button", { className: "button primary", text: "Change password", attrs: { type: "submit" } });
  passwordCard.append(element("h2", { text: "Management password" }), element("p", { text: "Changes the shared WebUI, RTSP and ONVIF password together. SSH remains key-only. You will sign in again after a successful change." }), password, confirm, passwordMessage, change);
  passwordCard.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!password.value || password.value !== confirm.value) {
      setMessage(passwordMessage, "Enter the same non-empty password twice.", "error");
      return;
    }
    change.disabled = true;
    try {
      await client.postJson<JsonObject>(routes.auth.password, { password: password.value });
      password.value = "";
      confirm.value = "";
      setMessage(passwordMessage, "Shared WebUI, RTSP and ONVIF password changed.", "success");
      window.location.assign("/login.html");
    } catch (error) {
      setMessage(passwordMessage, error instanceof Error ? error.message : "Password change failed.", "error");
    } finally {
      change.disabled = false;
    }
  });

  const keyCard = element("section", { className: "card compact-form" });
  const keyStatus = element("p", { text: "Checking API key…" });
  const keyValue = element("code", { className: "key-value", text: "" });
  const generate = button("Generate API key", "button secondary");
  const copy = button("Copy API key", "button secondary");
  copy.disabled = true;
  const remove = button("Delete API key", "button danger");
  const actions = element("div", { className: "form-actions" });
  actions.append(generate, copy, remove);
  keyCard.append(element("h2", { text: "Service API key" }), element("p", { text: "For explicit non-browser clients. This WebUI always uses its HttpOnly session cookie." }), keyStatus, keyValue, actions);
  async function refreshKey(): Promise<void> {
    try {
      const value = await client.json<{ exists: boolean }>(routes.webuiApiKey);
      keyStatus.textContent = value.exists ? "An API key exists." : "No API key exists.";
      keyValue.textContent = "Hidden after generation";
      remove.disabled = !value.exists;
      copy.disabled = true;
    } catch (error) {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to read API-key status.";
    }
  }
  generate.addEventListener("click", async () => {
    const value = await client.json<{ api_key: string }>(routes.webuiApiKey, { method: "POST" }).catch((error: unknown) => {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to generate API key.";
      return null;
    });
    if (value) { keyStatus.textContent = "New API key generated. Store it in the intended client."; keyValue.textContent = value.api_key; copy.disabled = false; remove.disabled = false; }
  });
  copy.addEventListener("click", async () => {
    if (!keyValue.textContent) return;
    try {
      await navigator.clipboard.writeText(keyValue.textContent);
      keyStatus.textContent = "API key copied to the clipboard.";
    } catch (_) {
      keyStatus.textContent = "Clipboard access was unavailable. Select and copy the key manually.";
    }
  });
  remove.addEventListener("click", async () => {
    if (!window.confirm("Delete the current service API key? Existing non-browser clients will stop authenticating.")) return;
    try {
      await client.empty(routes.webuiApiKey, { method: "DELETE" });
      keyStatus.textContent = "API key deleted.";
      keyValue.textContent = "";
      copy.disabled = true;
      remove.disabled = true;
    } catch (error) {
      keyStatus.textContent = error instanceof Error ? error.message : "Unable to delete API key.";
    }
  });
  security.append(passwordCard, keyCard);
  rendered.node.append(security);
  void refreshKey();
}
