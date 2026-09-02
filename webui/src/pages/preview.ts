import type { LiveControlCommand, RuntimeHeartbeat, RuntimeMedia } from "../api/contracts";
import { ApiClient } from "../api/client";
import { ControlApi } from "../api/control";
import { MjpegPreview, WhipPreview, type PreviewState } from "../api/media";
import { routes } from "../api/routes";
import { button, element, setMessage, statusMessage } from "../app/dom";
import { PreviewFullscreen } from "../app/fullscreen-preview";

interface ControlDefinition {
  label: string;
  detail: string;
  state: (heartbeat: RuntimeHeartbeat) => boolean | null;
  command: (enabled: boolean) => LiveControlCommand;
}

export type DayNightSelection = "auto" | "day" | "night";

export function dayNightSelection(
  state: Pick<RuntimeHeartbeat, "daynight_enabled" | "daynight_mode">,
): DayNightSelection | null {
  if (state.daynight_enabled) return "auto";
  return state.daynight_mode === "day" || state.daynight_mode === "night"
    ? state.daynight_mode
    : null;
}

export function previewStreamUsable(candidate: 0 | 1, state: RuntimeMedia): boolean {
  const selected = candidate === 0 ? state.stream0 : state.stream1;
  // The hybrid runtime keeps Prudynt as media owner, so Control can report the
  // enabled main stream as unavailable when the volatile executable path is
  // not /usr/bin/prudynt. Let WHIP prove stream-0 availability and retain the
  // existing MJPEG fallback if rwd is not listening.
  return selected.enabled === true && (candidate === 0 || selected.available === true);
}

const controls: ControlDefinition[] = [
  { label: "Color mode", detail: "Use color instead of monochrome video", state: (h) => colorState(h.color_mode), command: (enabled) => ({ kind: "color", enabled }) },
  { label: "Microphone", detail: "Capture audio with the active stream", state: (h) => booleanState(h.mic_enabled), command: (enabled) => ({ kind: "microphone", enabled }) },
  { label: "Speaker", detail: "Enable the camera speaker output", state: (h) => booleanState(h.spk_enabled), command: (enabled) => ({ kind: "speaker", enabled }) },
  { label: "Privacy mode", detail: "Hide video using the configured privacy state", state: (h) => booleanState(h.privacy_enabled), command: (enabled) => ({ kind: "privacy", enabled }) },
  { label: "Motion detection", detail: "Enable event detection", state: (h) => booleanState(h.motion_enabled), command: (enabled) => ({ kind: "motion", enabled }) },
  { label: "Record main stream", detail: "Write clips from stream 0", state: (h) => booleanState(h.rec_ch0), command: (enabled) => ({ kind: "recording", enabled, stream: 0 }) },
  { label: "Record substream", detail: "Write clips from stream 1", state: (h) => booleanState(h.rec_ch1), command: (enabled) => ({ kind: "recording", enabled, stream: 1 }) },
  { label: "IR filter", detail: "Physical IR-cut filter", state: (h) => nullableBinary(h.ircut_state), command: (enabled) => ({ kind: "ircut", enabled }) },
  { label: "850 nm IR LED", detail: "D-Link A1 infrared illumination", state: (h) => nullableBinary(h.ir850_state), command: (enabled) => ({ kind: "ir850", enabled }) },
];

function booleanState(value: boolean): boolean {
  return value;
}

function nullableBinary(value: 0 | 1 | null | undefined): boolean | null {
  return value === 0 ? false : value === 1 ? true : null;
}

function colorState(value: 0 | 1 | null | undefined): boolean | null {
  // Ingenic running_mode is 0 for day/color and 1 for night/monochrome.
  return value === 0 ? true : value === 1 ? false : null;
}

function formatUptime(value: RuntimeHeartbeat["uptime"]): string {
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  return `${days} days, ${hours} hours`;
}

export function renderPreview(
  http: ApiClient,
  api: ControlApi,
  focusPolicy: { track_focus: boolean; focus_timeout: number } = { track_focus: false, focus_timeout: 0 },
): { node: HTMLElement; cleanup: () => void } {
  const section = element("section", { className: "page preview-page" });
  const message = statusMessage();
  const hero = element("header", { className: "page-heading page-heading-with-actions" });
  const heroText = element("div");
  heroText.append(
    element("h1", { text: "Preview" }),
  );
  const heroActions = element("div", { className: "page-heading-actions" });
  const reload = button("Reload", "button secondary");
  const snapshot = button("Take snapshot", "button primary");
  heroActions.append(reload, snapshot);
  hero.append(heroText, heroActions);

  const previewStateLine = element("p", { className: "preview-state-line", text: "Main stream · Loading" });

  const statusGrid = element("div", { className: "status-grid" });
  const statusValues = ["Video", "Network", "Storage", "Memory"].map((label) => {
    const card = element("div", { className: "status-cell" });
    const value = element("strong", { text: "Loading" });
    const detail = element("small", { text: "Waiting for camera" });
    card.append(element("span", { text: label }), value, detail);
    statusGrid.append(card);
    return { value, detail };
  });

  const liveCard = element("section", { className: "card live-card" });
  const liveHeader = element("div", { className: "live-header" });
  const liveTitle = element("div", { className: "live-title" });
  const liveBadge = element("span", { className: "badge", text: "Idle" });
  liveTitle.append(liveBadge, element("strong", { text: "Main stream" }));
  const streamSelect = element("select", { className: "input compact", attrs: { "aria-label": "Preview stream" } });
  const streamOptions = {
    0: element("option", { text: "Main stream · CH0", attrs: { value: "0" } }),
    1: element("option", { text: "Substream · CH1", attrs: { value: "1" } }),
  } as const;
  streamSelect.append(streamOptions[0], streamOptions[1]);
  liveHeader.append(liveTitle, streamSelect);
  const frame = element("div", { className: "preview-frame" });
  const video = element("video", {
    attrs: { "aria-label": "Live camera preview", autoplay: "", muted: "", playsinline: "", hidden: "" },
  });
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  const image = element("img", { attrs: { alt: "Live camera preview" } });
  const streamMessage = element("div", { className: "stream-message", text: "Starting preview…", attrs: { role: "status" } });
  const retry = button("Retry preview", "button secondary");
  retry.hidden = true;
  streamMessage.append(retry);
  frame.append(video, image, streamMessage);
  const endpoints = element("div", { className: "endpoint-list" });
  liveCard.append(liveHeader, frame, endpoints);

  const controlCard = element("aside", { className: "card control-card", attrs: { "aria-label": "Live controls" } });
  controlCard.append(element("h2", { text: "Live controls" }), element("p", { text: "Each row shows the confirmed camera state." }));
  const dayNightRow = element("div", { className: "control-row daynight-row" });
  const dayNightCopy = element("div");
  const dayNightDetailId = "preview-daynight-detail";
  dayNightCopy.append(
    element("strong", { text: "Day / night" }),
    element("small", { text: "Automatic sensing or a forced image mode", attrs: { id: dayNightDetailId } }),
  );
  const dayNightGroup = element("div", {
    className: "daynight-controls",
    attrs: { role: "group", "aria-label": "Day and night mode", "aria-describedby": dayNightDetailId },
  });
  const dayNightButtons = new Map<DayNightSelection, HTMLButtonElement>();
  for (const [mode, label] of [["auto", "Auto"], ["day", "Day"], ["night", "Night"]] as const) {
    const modeButton = button(label, "control-toggle");
    modeButton.disabled = true;
    modeButton.setAttribute("aria-pressed", "false");
    dayNightGroup.append(modeButton);
    dayNightButtons.set(mode, modeButton);
  }
  dayNightRow.append(dayNightCopy, dayNightGroup);
  controlCard.append(dayNightRow);
  const controlRows = new Map<ControlDefinition, { button: HTMLButtonElement; state: HTMLSpanElement }>();
  for (const [index, control] of controls.entries()) {
    const row = element("div", { className: "control-row" });
    const copy = element("div");
    const detailId = `preview-control-detail-${index}`;
    copy.append(
      element("strong", { text: control.label }),
      element("small", { text: control.detail, attrs: { id: detailId } }),
    );
    const toggle = button("Unavailable", "control-toggle");
    toggle.setAttribute("aria-describedby", detailId);
    const state = element("span", { className: "visually-hidden", text: "Unavailable" });
    toggle.append(state);
    toggle.disabled = true;
    row.append(copy, toggle);
    controlCard.append(row);
    controlRows.set(control, { button: toggle, state });
  }
  const content = element("div", { className: "preview-content" });
  content.append(message, hero, previewStateLine, statusGrid, liveCard);
  section.append(controlCard, content);

  const mjpegPreview = new MjpegPreview(image);
  const whipPreview = new WhipPreview(video);
  const fullscreen = new PreviewFullscreen(frame, [video, image], "live camera preview");
  let previewTransport: "WebRTC" | "MJPEG" = "WebRTC";
  const preview = {
    start(selected: 0 | 1, onState: (state: PreviewState) => void, force = false): void {
      whipPreview.stop(false);
      mjpegPreview.stop();
      const startMjpeg = (): void => {
        previewTransport = "MJPEG";
        video.hidden = true;
        image.hidden = false;
        mjpegPreview.start(selected, onState, force);
      };
      if (typeof RTCPeerConnection === "undefined") {
        startMjpeg();
        return;
      }
      previewTransport = "WebRTC";
      image.hidden = true;
      video.hidden = false;
      whipPreview.start(selected, (state) => {
        if (state === "error") startMjpeg();
        else onState(state);
      });
    },
    stop(): void {
      whipPreview.stop(false);
      mjpegPreview.stop();
    },
  };
  let stream = 0 as 0 | 1;
  let heartbeat: RuntimeHeartbeat | null = null;
  let media: RuntimeMedia | null = null;
  let previewStarted = false;
  let previewAttempted = false;
  let previewState: PreviewState = "idle";
  let interval = 0;
  let focusTimeout = 0;
  let cancelled = false;

  function updatePreviewState(state: PreviewState): void {
    previewState = state;
    previewStarted = state === "loading" || state === "live";
    liveBadge.textContent = state === "live" ? "Live" : state === "loading" ? "Connecting" : state === "error" ? "Offline" : "Idle";
    liveBadge.dataset.state = state;
    previewStateLine.textContent = `${stream === 0 ? "Main stream" : "Substream"} · ${liveBadge.textContent} · ${previewTransport}`;
    streamMessage.hidden = state === "live";
    retry.hidden = state !== "error";
    if (state === "loading") streamMessage.firstChild!.textContent = "Connecting to the camera stream…";
    if (state === "error") streamMessage.firstChild!.textContent = "The stream is slow or unavailable. ";
  }

  function startPreview(force = false): void {
    window.clearTimeout(focusTimeout);
    if (!media || !previewStreamUsable(stream, media)) {
      previewStarted = false;
      previewAttempted = false;
      preview.stop();
      streamMessage.hidden = false;
      retry.hidden = true;
      streamMessage.firstChild!.textContent = media ? "No enabled camera stream is available." : "Waiting for runtime media state…";
      return;
    }
    previewAttempted = true;
    preview.start(stream, updatePreviewState, force);
  }

  function updateStreamAvailability(next: RuntimeMedia): void {
    media = next;
    streamOptions[0].disabled = !previewStreamUsable(0, next);
    streamOptions[1].disabled = !previewStreamUsable(1, next);
    const fallback = previewStreamUsable(stream, next) ? stream : previewStreamUsable(0, next) ? 0 : previewStreamUsable(1, next) ? 1 : null;
    if (fallback === null) {
      previewStarted = false;
      previewAttempted = false;
      preview.stop();
      streamMessage.hidden = false;
      retry.hidden = true;
      streamMessage.firstChild!.textContent = "No enabled camera stream is available.";
      snapshot.disabled = true;
      return;
    }
    const changed = stream !== fallback;
    stream = fallback;
    streamSelect.value = String(stream);
    liveTitle.querySelector("strong")!.textContent = stream === 0 ? "Main stream" : "Substream";
    previewStateLine.textContent = `${stream === 0 ? "Main stream" : "Substream"} · ${liveBadge.textContent}`;
    snapshot.disabled = false;
    if (changed || !previewAttempted) startPreview();
  }

  function stopForLostFocus(): void {
    if (!focusPolicy.track_focus) return;
    window.clearTimeout(focusTimeout);
    if (focusPolicy.focus_timeout > 0) {
      focusTimeout = window.setTimeout(() => preview.stop(), focusPolicy.focus_timeout * 1000);
    } else {
      preview.stop();
    }
  }

  function updateControls(next: RuntimeHeartbeat): void {
    heartbeat = next;
    const selectedDayNight = dayNightSelection(next);
    for (const [mode, modeButton] of dayNightButtons) {
      const active = mode === selectedDayNight;
      modeButton.disabled = false;
      modeButton.classList.toggle("active", active);
      modeButton.setAttribute("aria-pressed", String(active));
    }
    for (const [control, row] of controlRows) {
      const active = control.state(heartbeat);
      row.button.disabled = active === null;
      row.button.textContent = active === null ? "Unavailable" : control.label === "Motion detection" && active && heartbeat.motion_active ? "Active" : active ? "On" : "Off";
      row.button.setAttribute("aria-pressed", active === null ? "false" : String(active));
      row.button.classList.toggle("active", active === true);
    }
    statusValues[0]!.value.textContent = heartbeat.rec_ch0 ? "Main stream recording" : "Main stream ready";
    statusValues[0]!.detail.textContent = `Day / night: ${heartbeat.daynight_mode}`;
    statusValues[1]!.value.textContent = "Local connection";
    statusValues[1]!.detail.textContent = `Uptime ${formatUptime(heartbeat.uptime)}`;
  }

  async function refreshRuntime(): Promise<void> {
    try {
      const next = await api.heartbeat();
      const system = await api.system();
      const media = await api.media();
      if (cancelled) return;
      updateControls(next);
      updateStreamAvailability(media);
      updateRuntimeSummary(system, media);
      setMessage(message);
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to load live state.", "error");
    }
  }

  async function refreshHeartbeat(): Promise<void> {
    try {
      await loadHeartbeat();
    } catch (error) {
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "Unable to refresh live state.", "error");
    }
  }

  async function loadHeartbeat(): Promise<void> {
    const next = await api.heartbeat();
    if (!cancelled) updateControls(next);
  }

  function applyAcceptedDayNight(mode: DayNightSelection): void {
    if (!heartbeat) return;
    updateControls({
      ...heartbeat,
      daynight_enabled: mode === "auto",
      daynight_mode: mode === "auto" ? heartbeat.daynight_mode : mode,
    });
  }

  function updateRuntimeSummary(system: Awaited<ReturnType<ControlApi["system"]>>, media: RuntimeMedia): void {
    const active = stream === 0 ? media.stream0 : media.stream1;
    statusValues[0]!.detail.textContent = `${active.width ?? "—"} × ${active.height ?? "—"} · ${active.fps ?? "—"} fps · ${active.format ?? "video"}`;
    statusValues[1]!.value.textContent = system.network.online ? "Network connected" : "Disconnected";
    statusValues[1]!.detail.textContent = system.network.ip || "Address unavailable";
    statusValues[2]!.value.textContent = "Storage available";
    statusValues[2]!.detail.textContent = `${system.extras.free} KiB free`;
    statusValues[3]!.value.textContent = `${system.memory.free} KiB free`;
    statusValues[3]!.detail.textContent = `${system.memory.total} KiB Linux allocation`;
    const host = location.hostname;
    const rtsp = media.rtsp;
    endpoints.replaceChildren();
    endpoints.append(element("span", { className: "endpoint-label", text: "Player endpoints" }));
    for (const entry of [
      { label: "RTSP", value: `rtsp://${host}:${rtsp?.port ?? 554}/${active.rtsp_endpoint ?? (stream === 0 ? "ch0" : "ch1")}` },
      { label: "MJPEG", value: new URL(routes.media.mjpeg(stream), location.origin).href },
    ]) {
      const row = element("div", { className: "endpoint-row" });
      row.append(element("strong", { text: entry.label }), element("code", { text: entry.value }));
      endpoints.append(row);
    }
    endpoints.append(element("small", {
      className: "tools-muted",
      text: "WebRTC is played by the preview above; its WHIP signaling endpoint is not a player URL.",
    }));
  }

  for (const [mode, modeButton] of dayNightButtons) {
    modeButton.addEventListener("click", async () => {
      for (const item of dayNightButtons.values()) item.disabled = true;
      try {
        await api.setLiveControl({ kind: "daynight", mode });
        // Control acknowledges this action only after the direct media/GPIO
        // transition and configuration update complete. Reflect that accepted
        // state immediately, then reconcile it with the runtime heartbeat.
        applyAcceptedDayNight(mode);
        await loadHeartbeat();
        setMessage(message);
      } catch (error) {
        setMessage(message, error instanceof Error ? error.message : "The day / night request failed.", "error");
      } finally {
        for (const item of dayNightButtons.values()) item.disabled = false;
      }
    });
  }

  for (const [control, row] of controlRows) {
    row.button.addEventListener("click", async () => {
      const current = heartbeat ? control.state(heartbeat) : null;
      if (current === null) return;
      row.button.disabled = true;
      try {
        await api.setLiveControl(control.command(!current));
        await refreshHeartbeat();
      } catch (error) {
        setMessage(message, error instanceof Error ? error.message : "The control request failed.", "error");
      } finally {
        row.button.disabled = false;
      }
    });
  }

  streamSelect.addEventListener("change", () => {
    const requested = streamSelect.value === "1" ? 1 : 0;
    if (!media || !previewStreamUsable(requested, media)) {
      streamSelect.value = String(stream);
      return;
    }
    stream = requested;
    startPreview(true);
    void refreshRuntime();
  });
  reload.addEventListener("click", () => { startPreview(true); void refreshRuntime(); });
  retry.addEventListener("click", () => startPreview(true));
  snapshot.addEventListener("click", async () => {
    if (!media || !previewStreamUsable(stream, media)) return;
    snapshot.disabled = true;
    try {
      const blob = await http.blob(routes.actions.snapshot(stream));
      const url = URL.createObjectURL(blob);
      const link = element("a", { attrs: { href: url, download: `snapshot-ch${stream}.jpg` } });
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setMessage(message, error instanceof Error ? error.message : "Snapshot failed.", "error");
    } finally {
      snapshot.disabled = false;
    }
  });

  const pageHide = () => { void fullscreen.exit(); preview.stop(); };
  const visibilityChange = (): void => {
    if (document.hidden) { void fullscreen.exit(); preview.stop(); }
    else if (previewState !== "error") startPreview();
  };
  const windowFocus = (): void => { if (previewState !== "error" && !previewStarted) startPreview(); };
  const windowBlur = (): void => stopForLostFocus();
  window.addEventListener("pagehide", pageHide);
  document.addEventListener("visibilitychange", visibilityChange);
  if (focusPolicy.track_focus) {
    window.addEventListener("focus", windowFocus);
    window.addEventListener("blur", windowBlur);
  }
  snapshot.disabled = true;
  void refreshRuntime();
  interval = window.setInterval(() => void refreshHeartbeat(), 5_000);

  return {
    node: section,
    cleanup: () => {
      cancelled = true;
      window.clearInterval(interval);
      window.clearTimeout(focusTimeout);
      window.removeEventListener("pagehide", pageHide);
      document.removeEventListener("visibilitychange", visibilityChange);
      window.removeEventListener("focus", windowFocus);
      window.removeEventListener("blur", windowBlur);
      fullscreen.destroy();
      preview.stop();
    },
  };
}
