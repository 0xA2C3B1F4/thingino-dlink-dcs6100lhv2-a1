import type { RuntimeMedia } from "../api/contracts";
import { ControlApi } from "../api/control";
import { MjpegPreview, WhipPreview, type PreviewState, type PreviewTimers } from "../api/media";
import { button, element } from "./dom";
import { PreviewFullscreen } from "./fullscreen-preview";
import type { PageId } from "./navigation";

export const STREAMER_PREVIEW_ROUTES = ["imaging", "streams", "osd", "motion-privacy"] as const satisfies readonly PageId[];
const RETRY_DELAYS_MS = [1_000, 2_000, 5_000] as const;

export function isStreamerPreviewRoute(id: PageId): boolean {
  return (STREAMER_PREVIEW_ROUTES as readonly PageId[]).includes(id);
}

export type PreviewSessionAction = "start" | "stop" | "schedule-retry";

/**
 * Session-only lifecycle state for the shared Streamer preview.  Keeping this
 * separate from the DOM makes route, visibility and retry cancellation
 * testable without starting a browser or a media request.
 */
export class StreamerPreviewSession {
  private routeActive = false;
  private desired = false;
  private visible = true;
  private retryScheduled = false;
  private retryAttempt = 0;

  get wantsStream(): boolean { return this.routeActive && this.desired && this.visible; }
  get isDesired(): boolean { return this.desired; }
  get retryPending(): boolean { return this.retryScheduled; }

  restoreDesired(desired: boolean): void {
    this.desired = desired;
  }

  setRoute(id: PageId): PreviewSessionAction[] {
    const nextActive = isStreamerPreviewRoute(id);
    const wasRunning = this.wantsStream;
    this.routeActive = nextActive;
    if (!nextActive) {
      this.retryScheduled = false;
      return wasRunning || !nextActive ? ["stop"] : [];
    }
    return !wasRunning && this.wantsStream ? ["start"] : [];
  }

  show(): PreviewSessionAction[] {
    this.desired = true;
    return this.wantsStream ? ["start"] : [];
  }

  hide(): PreviewSessionAction[] {
    this.desired = false;
    this.retryScheduled = false;
    return ["stop"];
  }

  setDocumentVisible(visible: boolean): PreviewSessionAction[] {
    if (this.visible === visible) return [];
    this.visible = visible;
    if (!visible) {
      this.retryScheduled = false;
      return ["stop"];
    }
    return this.wantsStream ? ["start"] : [];
  }

  onPreviewState(state: PreviewState): PreviewSessionAction[] {
    if (state === "live") {
      this.retryAttempt = 0;
      this.retryScheduled = false;
      return [];
    }
    if (state !== "error" || !this.wantsStream || this.retryScheduled) return [];
    this.retryScheduled = true;
    return ["schedule-retry"];
  }

  retryDelayMs(): number {
    return RETRY_DELAYS_MS[Math.min(this.retryAttempt, RETRY_DELAYS_MS.length - 1)]!;
  }

  retryTimerFired(): PreviewSessionAction[] {
    this.retryScheduled = false;
    if (!this.wantsStream) return [];
    this.retryAttempt = Math.min(this.retryAttempt + 1, RETRY_DELAYS_MS.length - 1);
    return ["start"];
  }

  resetRetry(): void {
    this.retryScheduled = false;
    this.retryAttempt = 0;
  }

  cancelRetry(): void {
    this.retryScheduled = false;
  }
}

interface StreamerPreviewTimers extends PreviewTimers {}

const browserTimers: StreamerPreviewTimers = {
  setTimeout: (callback, delay) => window.setTimeout(callback, delay),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

/** Schedules at most one bounded retry and can be cancelled by route/hide. */
export class StreamerPreviewRetry {
  private timer: number | null = null;

  constructor(
    private readonly session: StreamerPreviewSession,
    private readonly timers: StreamerPreviewTimers,
    private readonly restart: () => void,
  ) {}

  handleState(state: PreviewState): void {
    const actions = this.session.onPreviewState(state);
    if (actions.includes("schedule-retry")) this.schedule();
  }

  cancel(): void {
    if (this.timer !== null) {
      this.timers.clearTimeout(this.timer);
      this.timer = null;
    }
    this.session.cancelRetry();
  }

  private schedule(): void {
    if (this.timer !== null || !this.session.wantsStream) return;
    this.timer = this.timers.setTimeout(() => {
      this.timer = null;
      if (this.session.retryTimerFired().includes("start")) this.restart();
    }, this.session.retryDelayMs());
  }
}

export function streamerPreviewStreamUsable(stream: 0 | 1, media: RuntimeMedia): boolean {
  const value = stream === 0 ? media.stream0 : media.stream1;
  return value.enabled === true;
}

function streamLabel(stream: 0 | 1): string {
  return stream === 0 ? "Main stream" : "Substream";
}

/**
 * Owns the one authenticated WebRTC-or-MJPEG preview used by all Streamer configuration
 * pages.  The host is mounted by the shell, never by a page renderer, so
 * changing Image -> Streams -> OSD cannot abort a healthy multipart response.
 */
export class StreamerPreviewController {
  private readonly session = new StreamerPreviewSession();
  private readonly mjpegPreview: MjpegPreview;
  private readonly whipPreview: WhipPreview;
  private readonly fullscreen: PreviewFullscreen;
  private readonly retry: StreamerPreviewRetry;
  private readonly timers: StreamerPreviewTimers;
  private readonly body: HTMLElement;
  private readonly toggle: HTMLButtonElement;
  private readonly streamSelect: HTMLSelectElement;
  private readonly status: HTMLElement;
  private readonly frame: HTMLElement;
  private readonly video: HTMLVideoElement;
  private readonly image: HTMLImageElement;
  private readonly host: HTMLElement;
  private stream: 0 | 1 = 0;
  private media: RuntimeMedia | null = null;
  private mediaRequest: Promise<void> | null = null;
  private cancelled = false;
  private route: PageId = "preview";
  private transport: "WebRTC" | "MJPEG" = "WebRTC";
  private static readonly storageKey = "thingino-streamer-preview";

  constructor(private readonly api: ControlApi, host: HTMLElement, timers: StreamerPreviewTimers = browserTimers) {
    this.host = host;
    this.timers = timers;
    host.classList.add("streamer-preview-host");
    const pane = element("section", { className: "card streamer-preview-details" });
    const header = element("div", { className: "streamer-preview-header" });
    header.append(element("h2", { text: "Live preview" }));
    const body = element("div", { className: "streamer-preview-body" });
    this.body = body;
    const description = element("p", { className: "tools-muted", text: "One shared camera stream for Streamer settings." });
    this.status = element("p", { className: "streamer-preview-status", text: "Preview is off." });
    this.toggle = button("Show live preview", "button secondary");
    this.streamSelect = element("select", { className: "input compact", attrs: { "aria-label": "Live preview stream" } });
    this.streamSelect.append(
      element("option", { text: "Main stream", attrs: { value: "0" } }),
      element("option", { text: "Substream", attrs: { value: "1" } }),
    );
    const controls = element("div", { className: "streamer-preview-controls" });
    controls.append(this.streamSelect);
    this.video = element("video", { attrs: { autoplay: "", muted: "", playsinline: "", "aria-label": "Shared live camera preview" } });
    this.image = element("img", { attrs: { alt: "Shared live camera preview", hidden: "" } });
    this.frame = element("div", { className: "preview-frame streamer-preview-frame" });
    this.frame.append(this.video, this.image);
    this.frame.hidden = true;
    body.append(description, this.status, controls, this.frame);
    header.append(this.toggle);
    pane.append(header, body);
    host.replaceChildren(pane);
    host.hidden = true;
    this.mjpegPreview = new MjpegPreview(this.image);
    this.whipPreview = new WhipPreview(this.video);
    this.fullscreen = new PreviewFullscreen(this.frame, [this.video, this.image], "Streamer live preview");
    this.retry = new StreamerPreviewRetry(this.session, this.timers, () => { void this.start(true); });
    this.session.restoreDesired(this.readDesiredState());
    this.streamSelect.hidden = !this.session.isDesired;

    this.toggle.addEventListener("click", () => {
      if (this.session.isDesired) this.hide();
      else this.show();
    });
    this.streamSelect.addEventListener("change", () => {
      const next = this.streamSelect.value === "1" ? 1 : 0;
      if (this.media && !streamerPreviewStreamUsable(next, this.media)) {
        this.streamSelect.value = String(this.stream);
        return;
      }
      this.stream = next;
      this.session.resetRetry();
      this.retry.cancel();
      if (this.session.wantsStream) void this.start(true);
      else this.updateStatus("Preview is off.");
    });
    document.addEventListener("visibilitychange", this.onVisibilityChange);
    window.addEventListener("pagehide", this.onPageHide);
    window.addEventListener("thingino:config-saved", this.onConfigSaved as EventListener);
    window.addEventListener("thingino:stream-card-focus", this.onStreamCardFocus as EventListener);
    this.setRoute("preview");
  }

  setRoute(id: PageId): void {
    if (this.cancelled) return;
    this.route = id;
    const actions = this.session.setRoute(id);
    const eligible = isStreamerPreviewRoute(id);
    this.host.hidden = !eligible;
    if (!eligible) {
      this.retry.cancel();
      void this.fullscreen.exit();
      this.stop();
      return;
    }
    this.updateMediaControls();
    if (this.session.isDesired) {
      this.body.hidden = false;
      this.toggle.textContent = "Hide live preview";
      if (actions.includes("start")) void this.start();
    } else {
      this.body.hidden = true;
      this.updateStatus("Preview is off.");
    }
  }

  refresh(): void {
    if (this.cancelled || !this.session.wantsStream) return;
    this.session.resetRetry();
    this.retry.cancel();
    this.media = null;
    void this.start(true);
  }

  destroy(): void {
    if (this.cancelled) return;
    this.cancelled = true;
    this.session.hide();
    this.retry.cancel();
    this.fullscreen.destroy();
    this.stop();
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
    window.removeEventListener("pagehide", this.onPageHide);
    window.removeEventListener("thingino:config-saved", this.onConfigSaved as EventListener);
    window.removeEventListener("thingino:stream-card-focus", this.onStreamCardFocus as EventListener);
    this.host.hidden = true;
  }

  private readonly onVisibilityChange = (): void => {
    const actions = this.session.setDocumentVisible(!document.hidden);
    if (document.hidden) {
      this.retry.cancel();
      void this.fullscreen.exit();
      this.stop();
      return;
    }
    if (actions.includes("start")) void this.start(true);
  };

  private readonly onPageHide = (): void => {
    this.retry.cancel();
    void this.fullscreen.exit();
    this.stop();
  };

  private readonly onConfigSaved = (event: Event): void => {
    const detail = (event as CustomEvent<{ endpoint?: unknown; refreshStreamerPreview?: unknown }>).detail;
    if (detail?.refreshStreamerPreview !== true || !isStreamerPreviewRoute(this.route) || !this.session.wantsStream) return;
    this.refresh();
  };

  private readonly onStreamCardFocus = (event: Event): void => {
    const stream = (event as CustomEvent<{ stream?: unknown }>).detail?.stream;
    if (stream !== 0 && stream !== 1) return;
    if (this.route !== "streams" || !this.session.isDesired) return;
    if (this.media && !streamerPreviewStreamUsable(stream, this.media)) return;
    if (this.stream === stream) return;
    this.stream = stream;
    this.streamSelect.value = String(stream);
    this.session.resetRetry();
    this.retry.cancel();
    void this.start(true);
  };

  private show(): void {
    const actions = this.session.show();
    this.writeDesiredState(true);
    this.body.hidden = false;
    this.toggle.textContent = "Hide live preview";
    this.streamSelect.hidden = false;
    this.updateMediaControls();
    if (actions.includes("start")) void this.start();
  }

  private hide(): void {
    this.session.hide();
    this.writeDesiredState(false);
    this.retry.cancel();
    void this.fullscreen.exit();
    this.stop();
    this.body.hidden = true;
    this.toggle.textContent = "Show live preview";
    this.streamSelect.hidden = true;
    this.updateStatus("Preview is off.");
  }

  private updateStatus(text: string): void {
    this.status.textContent = text;
  }

  private updateMediaControls(): void {
    this.streamSelect.hidden = !this.session.isDesired;
    const main = this.media ? streamerPreviewStreamUsable(0, this.media) : true;
    const sub = this.media ? streamerPreviewStreamUsable(1, this.media) : true;
    this.streamSelect.options[0]!.disabled = !main;
    this.streamSelect.options[1]!.disabled = !sub;
    if (this.media && !streamerPreviewStreamUsable(this.stream, this.media)) {
      this.stream = main ? 0 : sub ? 1 : 0;
      this.streamSelect.value = String(this.stream);
    }
  }

  private async start(force = false): Promise<void> {
    if (this.cancelled || !this.session.wantsStream) return;
    if (this.mediaRequest) return;
    if (!this.media) {
      this.updateStatus("Reading stream availability…");
      this.mediaRequest = this.api.media().then((media) => {
        if (this.cancelled) return;
        this.media = media;
        this.updateMediaControls();
      }).catch((error: unknown) => {
        if (this.cancelled) return;
        this.updateStatus(error instanceof Error ? error.message : "Unable to read stream availability.");
        this.retry.handleState("error");
      }).finally(() => { this.mediaRequest = null; });
      await this.mediaRequest;
      if (this.cancelled || !this.session.wantsStream || !this.media) return;
    }
    if (!this.media || !streamerPreviewStreamUsable(this.stream, this.media)) {
      this.stop();
      this.updateStatus("No enabled camera stream is available.");
      return;
    }
    this.retry.cancel();
    this.frame.hidden = false;
    this.toggle.textContent = "Hide live preview";
    this.updateStatus(`Connecting to ${streamLabel(this.stream)}…`);
    this.startTransport(force);
  }

  private startTransport(force: boolean): void {
    this.whipPreview.stop(false);
    this.mjpegPreview.stop();
    const startMjpeg = (): void => {
      this.transport = "MJPEG";
      this.video.hidden = true;
      this.image.hidden = false;
      this.mjpegPreview.start(this.stream, this.onPreviewState, force);
    };
    if (typeof RTCPeerConnection === "undefined") {
      startMjpeg();
      return;
    }
    this.transport = "WebRTC";
    this.image.hidden = true;
    this.video.hidden = false;
    this.whipPreview.start(this.stream, (state) => {
      if (state === "error") startMjpeg();
      else this.onPreviewState(state);
    });
  }

  private readonly onPreviewState = (state: PreviewState): void => {
    if (this.cancelled) return;
    this.frame.hidden = state !== "loading" && state !== "live";
    if (state === "live") this.updateStatus(`${streamLabel(this.stream)} · Live · ${this.transport}`);
    else if (state === "loading") this.updateStatus(`Connecting to ${streamLabel(this.stream)} · ${this.transport}…`);
    else if (state === "error") {
      this.updateStatus("The stream is slow or unavailable. Retrying…");
      this.retry.handleState(state);
    } else if (!this.session.isDesired) this.updateStatus("Preview is off.");
  };

  private stop(): void {
    this.frame.hidden = true;
    this.whipPreview.stop(false);
    this.mjpegPreview.stop();
  }

  private readDesiredState(): boolean {
    try { return window.sessionStorage.getItem(StreamerPreviewController.storageKey) === "on"; } catch (_) { return false; }
  }

  private writeDesiredState(desired: boolean): void {
    try { window.sessionStorage.setItem(StreamerPreviewController.storageKey, desired ? "on" : "off"); } catch (_) { /* storage can be unavailable */ }
  }
}
