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
  blocked?: (heartbeat: RuntimeHeartbeat) => boolean;
  unavailableLabel?: (heartbeat: RuntimeHeartbeat) => string;
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
  // Raptor reports video availability independently from its JPEG slots. Let
  // WHIP prove stream-0 availability and retain the MJPEG fallback if RWD is
  // not listening.
  return selected.enabled === true && (candidate === 0 || selected.available === true);
}

const controls: ControlDefinition[] = [
  { label: "Color mode", detail: "Use color instead of monochrome video", state: (h) => colorState(h.color_mode), command: (enabled) => ({ kind: "color", enabled }) },
  { label: "Microphone", detail: "Capture audio with the active stream", state: (h) => booleanState(h.mic_enabled), command: (enabled) => ({ kind: "microphone", enabled }) },
  { label: "Speaker", detail: "Enable the camera speaker output", state: (h) => booleanState(h.spk_enabled), command: (enabled) => ({ kind: "speaker", enabled }) },
  { label: "Privacy mode", detail: "Hide video using the configured privacy state", state: (h) => booleanState(h.privacy_enabled), command: (enabled) => ({ kind: "privacy", enabled }) },
  { label: "Motion detection", detail: "Enable event detection", state: (h) => h.controls_supported?.motion === false ? null : booleanState(h.motion_enabled), command: (enabled) => ({ kind: "motion", enabled }) },
  { label: "Record main stream", detail: "Write clips from stream 0", state: (h) => booleanState(h.rec_ch0), blocked: (h) => h.rec_ch0 !== true && h.rec_ch0_available === false, unavailableLabel: (h) => h.rec_ch0_reason === "no_sd" ? "Off · No SD card" : h.rec_ch0_reason === "no_space" ? "Off · SD space low" : "Off · Unavailable", command: (enabled) => ({ kind: "recording", enabled, stream: 0 }) },
  { label: "Record substream", detail: "Write clips from stream 1", state: (h) => booleanState(h.rec_ch1), blocked: (h) => h.rec_ch1 !== true && h.rec_ch1_available === false, unavailableLabel: (h) => h.rec_ch1_reason === "no_sd" ? "Off · No SD card" : h.rec_ch1_reason === "no_space" ? "Off · SD space low" : "Off · Unavailable", command: (enabled) => ({ kind: "recording", enabled, stream: 1 }) },
  { label: "IR filter", detail: "Physical IR-cut filter", state: (h) => nullableBinary(h.ircut_state), command: (enabled) => ({ kind: "ircut", enabled }) },
  { label: "850 nm IR LED", detail: "D-Link A1 infrared illumination", state: (h) => nullableBinary(h.ir850_state), command: (enabled) => ({ kind: "ir850", enabled }) },
];

function booleanState(value: boolean | null): boolean | null {
  return value;
}

function nullableBinary(value: 0 | 1 | null | undefined): boolean | null {
  return value === 0 ? false : value === 1 ? true : null;
}

function colorState(value: 0 | 1 | null | undefined): boolean | null {
  // Ingenic running_mode is 0 for day/color and 1 for night/monochrome.
  return value === 0 ? true : value === 1 ? false : null;
}

export function previewVideoSummary(
  stream: 0 | 1,
  media: RuntimeMedia | null,
  heartbeat: Pick<RuntimeHeartbeat, "rec_ch0" | "rec_ch1"> | null,
): { value: string; detail: string } {
  const label = stream === 0 ? "Main stream" : "Substream";
  if (!media) return { value: `${label} status unknown`, detail: "Waiting for runtime media state" };
  const active = stream === 0 ? media.stream0 : media.stream1;
  const recording = stream === 0 ? heartbeat?.rec_ch0 : heartbeat?.rec_ch1;
  const state = !active.enabled ? "disabled" : !active.available ? "unavailable"
    : recording === true ? "recording" : "ready";
  return {
    value: `${label} ${state}`,
    detail: `${active.width ?? "—"} × ${active.height ?? "—"} · ${active.fps ?? "—"} fps · ${active.format ?? "video"}`,
  };
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
  const listen = button("Listen", "button secondary");
  listen.disabled = true;
  listen.setAttribute("aria-label", "Listen to camera audio");
  listen.setAttribute("aria-pressed", "false");
  const audioStatus = element("small", { text: "Waiting for WebRTC audio", attrs: { role: "status" } });
  liveHeader.append(liveTitle, streamSelect, listen);
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
  liveCard.append(liveHeader, frame, audioStatus, endpoints);

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
  const controlRows = new Map<ControlDefinition, { button: HTMLButtonElement; state: HTMLSpanElement; recovery: HTMLButtonElement[] }>();
  const pendingControls = new Set<ControlDefinition>();
  for (const [index, control] of controls.entries()) {
    const row = element("div", { className: "control-row" });
    const copy = element("div");
    const detailId = `preview-control-detail-${index}`;
    copy.append(
      element("strong", { text: control.label }),
      element("small", { text: control.detail, attrs: { id: detailId } }),
    );
    const toggle = button("Unavailable", "control-toggle");
    toggle.setAttribute("aria-label", control.label);
    toggle.setAttribute("aria-describedby", detailId);
    const state = element("span", { className: "visually-hidden", text: "Unavailable" });
    toggle.append(state);
    toggle.disabled = true;
    row.append(copy, toggle);
    const recovery: HTMLButtonElement[] = [];
    const kind = control.command(false).kind;
    const targets = kind === "privacy"
      ? [["Retry privacy protection", true], ["Turn privacy off", false]] as const
      : kind === "motion" ? [["Retry stopping motion", false]] as const
      : kind === "recording" ? [[control.label === "Record main stream" ? "Stop main stream recording" : "Stop substream recording", false]] as const : [];
    for (const [label, enabled] of targets) {
      const retryTarget = button(label, "button secondary control-recovery");
      retryTarget.hidden = true;
      retryTarget.addEventListener("click", () => void applyControl(control, enabled));
      recovery.push(retryTarget);
      row.append(retryTarget);
    }
    controlCard.append(row);
    controlRows.set(control, { button: toggle, state, recovery });
  }
  const content = element("div", { className: "preview-content" });
  content.append(message, hero, previewStateLine, statusGrid, liveCard);
  section.append(controlCard, content);

  const mjpegPreview = new MjpegPreview(image);
  let audioAvailable = false;
  let webrtcError = "";
  let listeningEpoch = 0;
  function resetListening(): void {
    listeningEpoch += 1;
    video.muted = true;
    listen.textContent = "Listen";
    listen.setAttribute("aria-pressed", "false");
  }
  listen.addEventListener("click", () => {
    if (!audioAvailable) return;
    if (!video.muted) {
      resetListening();
      audioStatus.textContent = "Playback muted. Camera microphone is unchanged.";
      return;
    }
    const epoch = ++listeningEpoch;
    video.muted = false;
    // play() runs directly inside the click gesture, without requesting a local microphone.
    void video.play().then(() => {
      if (epoch !== listeningEpoch || !audioAvailable) return;
      listen.textContent = "Mute playback";
      listen.setAttribute("aria-pressed", "true");
      audioStatus.textContent = "Playback enabled. Sound requires an enabled, unmuted camera microphone.";
    }).catch(() => {
      if (epoch !== listeningEpoch) return;
      resetListening();
      audioStatus.textContent = "Browser blocked playback. Click Listen to try again.";
    });
  });
  const whipPreview = new WhipPreview(video, {
    receiveAudio: true,
    onError(message): void { webrtcError = message; },
    onAudioAvailable(available): void {
      audioAvailable = available;
      listen.disabled = !available;
      if (!available) resetListening();
      audioStatus.textContent = available
        ? "Playback muted. Click Listen to hear the camera microphone."
        : "Waiting for WebRTC audio";
    },
  });
  const fullscreen = new PreviewFullscreen(frame, [video, image], "live camera preview");
  let previewTransport: "WebRTC" | "MJPEG" = "WebRTC";
  const preview = {
    start(selected: 0 | 1, onState: (state: PreviewState) => void, force = false): void {
      webrtcError = "";
      whipPreview.stop(false);
      mjpegPreview.stop();
      const startMjpeg = (): void => {
        previewTransport = "MJPEG";
        audioStatus.textContent = webrtcError
          ? `WebRTC failed: ${webrtcError}. MJPEG preview has no audio.`
          : "MJPEG preview has no audio.";
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
  let heartbeatRequest: Promise<void> | null = null;
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
    updateVideoSummary();
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
      row.button.disabled = active === null || control.blocked?.(heartbeat) === true || pendingControls.has(control);
      row.button.textContent = active === null ? "Unavailable" : control.blocked?.(heartbeat) ? control.unavailableLabel?.(heartbeat) ?? "Unavailable" : control.label === "Motion detection" && active && heartbeat.motion_active ? "Active" : active ? "On" : "Off";
      row.button.setAttribute("aria-pressed", active === null ? "mixed" : String(active));
      row.button.classList.toggle("active", active === true);
      const kind = control.command(false).kind;
      const command = control.command(false);
      const recorderNeedsStop = command.kind === "recording" && heartbeat[`rec_ch${command.stream}_file_closed`] === false;
      const retrySupported = recorderNeedsStop || ((kind === "privacy" || kind === "motion") && heartbeat.controls_supported?.[kind] === true);
      for (const recovery of row.recovery) {
        recovery.hidden = active !== null || !retrySupported;
        recovery.disabled = pendingControls.has(control);
      }
    }
    updateVideoSummary();
  }

  function updateVideoSummary(): void {
    const summary = previewVideoSummary(stream, media, heartbeat);
    statusValues[0]!.value.textContent = summary.value;
    statusValues[0]!.detail.textContent = summary.detail;
  }

  async function refreshRuntime(): Promise<void> {
    try {
      const media = await api.media();
      if (cancelled) return;
      updateStreamAvailability(media);
      await loadHeartbeat();
      if (cancelled) return;
      const system = await api.system();
      if (cancelled) return;
      updateRuntimeSummary(system, media);
      setMessage(message);
    } catch (error) {
      invalidateMotion();
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

  function invalidateMotion(): void {
    if (!cancelled && heartbeat) updateControls({ ...heartbeat, motion_enabled: null, motion_active: null });
  }

  async function loadHeartbeat(fresh = false): Promise<void> {
    // A read started before a mutation cannot confirm that mutation.
    if (fresh && heartbeatRequest) await heartbeatRequest.catch(() => undefined);
    if (heartbeatRequest) return heartbeatRequest;
    heartbeatRequest = api.heartbeat().then((next) => {
      if (!cancelled) updateControls(next);
    }).catch((error: unknown) => {
      invalidateMotion();
      throw error;
    }).finally(() => { heartbeatRequest = null; });
    return heartbeatRequest;
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
    updateVideoSummary();
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
      { label: "RTSP", value: rtsp?.port && active.rtsp_endpoint ? `rtsp://${host}:${rtsp.port}/${active.rtsp_endpoint}` : "Unavailable" },
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
        await loadHeartbeat(true);
        setMessage(message);
      } catch (error) {
        setMessage(message, error instanceof Error ? error.message : "The day / night request failed.", "error");
      } finally {
        for (const item of dayNightButtons.values()) item.disabled = false;
      }
    });
  }

  for (const [control, row] of controlRows) {
    row.button.addEventListener("click", () => {
      const current = heartbeat ? control.state(heartbeat) : null;
      if (current === null || (heartbeat && control.blocked?.(heartbeat)) || pendingControls.has(control)) return;
      void applyControl(control, !current);
    });
  }

  async function applyControl(control: ControlDefinition, enabled: boolean): Promise<void> {
    if (pendingControls.has(control)) return;
    pendingControls.add(control);
    if (heartbeat) updateControls(heartbeat);
    try {
      const command = control.command(enabled);
      await api.setLiveControl(command);
      await loadHeartbeat(true);
      if (command.kind === "motion") {
        // The action acknowledges the request before the IVS worker has
        // necessarily published its new state. Do not wait for the normal
        // five-second refresh or show an unconfirmed optimistic state.
        const deadline = performance.now() + 4_000;
        while (!cancelled && heartbeat && control.state(heartbeat) !== enabled) {
          if (performance.now() >= deadline) {
            throw new Error("Motion state was not confirmed. Check the camera state before retrying.");
          }
          await new Promise<void>((resolve) => window.setTimeout(resolve, 250));
          if (!cancelled) await loadHeartbeat(true);
        }
      }
      if (cancelled) return;
      setMessage(message);
    } catch (error) {
      // Failed application can change only part of the device state. Read
      // it again so recovery never uses an old toggle value as its target.
      try {
        await loadHeartbeat(true);
      } catch {
        if (!cancelled && heartbeat) {
          const kind = control.command(enabled).kind;
          if (kind === "privacy") updateControls({ ...heartbeat, privacy_enabled: null });
          if (kind === "motion") updateControls({ ...heartbeat, motion_enabled: null, motion_active: null });
        }
      }
      if (!cancelled) setMessage(message, error instanceof Error ? error.message : "The control request failed.", "error");
    } finally {
      pendingControls.delete(control);
      if (!cancelled && heartbeat) updateControls(heartbeat);
    }
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
