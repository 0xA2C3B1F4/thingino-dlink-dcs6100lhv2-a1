import "./styles.css";
import { ApiClient } from "./api/client";
import { ControlApi } from "./api/control";
import { clear } from "./app/dom";
import { pageFromLocation, type PageId } from "./app/navigation";
import { applyThemePreference, buildShell, type Shell, type ThemePreference } from "./app/shell";
import { StreamerPreviewController } from "./app/streamer-preview";
import { renderConfigPage } from "./pages/config";
import { renderLogin } from "./pages/login";
import { renderPreview } from "./pages/preview";
import { renderToolPage } from "./pages/tools";
import type { WebuiConfig } from "./api/contracts";

type Cleanup = () => void;

try {
  const preference = localStorage.getItem("dcs6100-theme") || "light";
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.theme = preference === "auto"
    ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
} catch (_) {
  document.documentElement.dataset.theme = "light";
}

let shell: Shell | null = null;
let streamerPreview: StreamerPreviewController | null = null;
let cleanupPage: Cleanup = () => undefined;
let renderingLogin = false;
let webuiPreferences: Pick<WebuiConfig, "track_focus" | "focus_timeout"> = { track_focus: false, focus_timeout: 0 };
let acceptedHash = location.hash || "#/preview";

const http = new ApiClient({
  onUnauthorized: () => showLogin("Your session expired. Sign in again."),
  timeoutMs: 10_000,
});
const api = new ControlApi(http);

function renderPage(id: PageId): void {
  if (!shell) return;
  cleanupPage();
  shell.setActive(id);
  streamerPreview?.setRoute(id);
  const rendered = id === "preview"
    ? renderPreview(http, api, webuiPreferences)
    : renderConfigPage(http, id) ?? renderToolPage(http, id);
  if (!rendered) {
    location.hash = "#/preview";
    return;
  }
  clear(shell.page);
  shell.page.append(rendered.node);
  cleanupPage = rendered.cleanup;
  document.title = `${id === "preview" ? "Preview" : rendered.node.querySelector("h1")?.textContent ?? "Camera"} · DCS-6100LHV2`;
  shell.main.focus({ preventScroll: true });
}

async function showApp(): Promise<void> {
  try {
    const session = await api.session();
    if (!session.authenticated) {
      showLogin();
      return;
    }
    let browserTheme: string | null = null;
    try { browserTheme = localStorage.getItem("dcs6100-theme"); } catch (_) { /* storage can be unavailable */ }
    let cameraWebui: WebuiConfig | null = null;
    try {
      cameraWebui = await api.webui();
      webuiPreferences = { track_focus: cameraWebui.track_focus, focus_timeout: cameraWebui.focus_timeout };
    } catch (_) {
      webuiPreferences = { track_focus: false, focus_timeout: 0 };
    }
    if (browserTheme === "light" || browserTheme === "dark" || browserTheme === "auto") {
      applyThemePreference(browserTheme);
    } else if (cameraWebui) {
      applyThemePreference(cameraWebui.theme as ThemePreference);
    }
    renderingLogin = false;
    shell = buildShell(() => void logout());
    streamerPreview = new StreamerPreviewController(api, shell.streamerPreviewHost);
    shell.setSession(session);
    const id = pageFromLocation();
    if (!location.hash) history.replaceState(null, "", "#/preview");
    acceptedHash = location.hash;
    renderPage(id);
  } catch (error) {
    // A 401 calls showLogin through onUnauthorized. Transport failures must
    // not make a valid session look as though it was destroyed.
    if (!renderingLogin) showConnectionError(error);
  }
}

function showConnectionError(error: unknown): void {
  cleanupPage();
  streamerPreview?.destroy();
  streamerPreview = null;
  shell = null;
  const root = document.querySelector("#app")!;
  const card = document.createElement("section");
  card.className = "login-card";
  const heading = document.createElement("h1");
  heading.textContent = "Camera connection interrupted";
  const message = document.createElement("p");
  message.className = "message";
  message.textContent = error instanceof Error ? error.message : "The camera did not respond.";
  const retry = document.createElement("button");
  retry.className = "button primary";
  retry.type = "button";
  retry.textContent = "Retry connection";
  retry.addEventListener("click", () => {
    retry.disabled = true;
    void showApp();
  });
  card.append(heading, message, retry);
  root.replaceChildren(card);
}

function showLogin(note?: string): void {
  if (renderingLogin) return;
  renderingLogin = true;
  cleanupPage();
  streamerPreview?.destroy();
  streamerPreview = null;
  shell = null;
  const root = document.querySelector("#app")!;
  const login = renderLogin(api, () => void showApp());
  if (note) {
    const notice = document.createElement("p");
    notice.className = "message";
    notice.setAttribute("role", "status");
    notice.dataset.variant = "info";
    notice.textContent = note;
    login.querySelector(".login-card")?.prepend(notice);
  }
  root.replaceChildren(login);
}

async function logout(): Promise<void> {
  try { await api.logout(); } finally {
    renderingLogin = false;
    showLogin("You have been signed out.");
  }
}

window.addEventListener("hashchange", () => {
  if (!shell) return;
  const guard = new Event("thingino:before-route-change", { cancelable: true });
  if (!window.dispatchEvent(guard)) {
    history.replaceState(null, "", acceptedHash);
    return;
  }
  acceptedHash = location.hash;
  renderPage(pageFromLocation());
});
window.addEventListener("thingino:webui-config", (event) => {
  const detail = (event as CustomEvent<{ track_focus?: unknown; focus_timeout?: unknown }>).detail;
  if (!detail || typeof detail.track_focus !== "boolean" || typeof detail.focus_timeout !== "number") return;
  webuiPreferences = { track_focus: detail.track_focus, focus_timeout: detail.focus_timeout };
});
window.addEventListener("pagehide", () => {
  cleanupPage();
  streamerPreview?.destroy();
  streamerPreview = null;
});

void showApp();
