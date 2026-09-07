import { routes } from "./routes";

export type PreviewState = "idle" | "loading" | "live" | "error";

export interface PreviewTimers {
  setTimeout(callback: () => void, delay: number): number;
  clearTimeout(handle: number): void;
}

type FetchPreview = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type DecodeFrame = (objectUrl: string) => Promise<void>;

export interface WhipPreviewOptions {
  fetcher?: FetchPreview;
  peerFactory?: () => RTCPeerConnection;
  endpoint?: (stream: 0 | 1) => string;
  iceTimeoutMs?: number;
  timers?: PreviewTimers;
}

export interface MjpegPreviewOptions {
  initialTimeoutMs?: number;
  stallTimeoutMs?: number;
  readinessPollMs?: number;
  transport?: MjpegPreviewTransport;
  timers?: PreviewTimers;
  fetcher?: FetchPreview;
  createObjectUrl?: (blob: Blob) => string;
  revokeObjectUrl?: (url: string) => void;
  decodeFrame?: DecodeFrame;
}

export type MjpegPreviewTransport = "fetch" | "native";

/**
 * Apple WebKit renders multipart MJPEG in an image element but does not expose
 * the response incrementally through Fetch. Other engines use the bounded
 * parser so only complete JPEG frames ever reach the visible image.
 */
export function preferredMjpegTransport(
  userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent,
  vendor = typeof navigator === "undefined" ? "" : navigator.vendor,
): MjpegPreviewTransport {
  return vendor === "Apple Computer, Inc." && /AppleWebKit\//.test(userAgent) ? "native" : "fetch";
}

const browserTimers: PreviewTimers = {
  setTimeout: (callback, delay) => window.setTimeout(callback, delay),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

export function rwdWhipEndpoint(
  stream: 0 | 1,
  pageUrl = typeof location === "undefined" ? "http://127.0.0.1/" : location.href,
): string {
  return new URL(routes.media.whip(stream), pageUrl).href;
}

/** Owns one video-only WHIP session and deletes it before releasing WebRTC state. */
export class WhipPreview {
  private active = false;
  private epoch = 0;
  private peer: RTCPeerConnection | null = null;
  private sessionUrl = "";
  private onState: (state: PreviewState) => void = () => undefined;
  private readonly fetcher: FetchPreview;
  private readonly peerFactory: () => RTCPeerConnection;
  private readonly endpoint: (stream: 0 | 1) => string;
  private readonly iceTimeoutMs: number;
  private readonly timers: PreviewTimers;

  constructor(private readonly video: HTMLVideoElement, options: WhipPreviewOptions = {}) {
    this.fetcher = options.fetcher ?? ((input, init) => window.fetch(input, init));
    this.peerFactory = options.peerFactory ?? (() => new RTCPeerConnection());
    this.endpoint = options.endpoint ?? rwdWhipEndpoint;
    this.iceTimeoutMs = options.iceTimeoutMs ?? 5000;
    this.timers = options.timers ?? browserTimers;
  }

  private emit(state: PreviewState): void {
    this.onState(state);
  }

  private async waitForIce(peer: RTCPeerConnection): Promise<void> {
    if (peer.iceGatheringState === "complete") return;
    await new Promise<void>((resolve, reject) => {
      const changed = (): void => {
        if (peer.iceGatheringState !== "complete") return;
        this.timers.clearTimeout(timeout);
        peer.removeEventListener("icegatheringstatechange", changed);
        resolve();
      };
      const timeout = this.timers.setTimeout(() => {
        peer.removeEventListener("icegatheringstatechange", changed);
        reject(new Error("WHIP ICE gathering timed out"));
      }, this.iceTimeoutMs);
      peer.addEventListener("icegatheringstatechange", changed);
    });
  }

  private async open(epoch: number, stream: 0 | 1): Promise<void> {
    const endpoint = this.endpoint(stream);
    const peer = this.peerFactory();
    this.peer = peer;
    const transceiver = peer.addTransceiver("video", { direction: "recvonly" });
    const h264 = (typeof RTCRtpReceiver === "undefined" ? undefined : RTCRtpReceiver.getCapabilities?.("video"))?.codecs.filter(
      (codec) => codec.mimeType.toLowerCase() === "video/h264",
    ) ?? [];
    if (h264.length && typeof transceiver.setCodecPreferences === "function") {
      transceiver.setCodecPreferences(h264);
    }
    peer.ontrack = (event): void => {
      if (!this.active || epoch !== this.epoch || event.track.kind !== "video") return;
      this.video.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void this.video.play().catch(() => undefined);
      this.emit("live");
    };
    const fail = (): void => {
      if (!this.active || epoch !== this.epoch) return;
      if (peer.connectionState === "failed" || peer.connectionState === "closed") {
        this.active = false;
        this.release();
        this.emit("error");
      }
    };
    peer.addEventListener("connectionstatechange", fail);

    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await this.waitForIce(peer);
    if (!this.active || epoch !== this.epoch || !peer.localDescription?.sdp) return;
    const response = await this.fetcher(endpoint, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/sdp" },
      body: peer.localDescription.sdp,
    });
    if (!response.ok) throw new Error(`WHIP request failed (${response.status})`);
    const answer = await response.text();
    const locationHeader = response.headers.get("location");
    if (!locationHeader) throw new Error("WHIP response omitted session location");
    this.sessionUrl = new URL(locationHeader, endpoint).href;
    if (!this.active || epoch !== this.epoch) {
      this.release();
      return;
    }
    await peer.setRemoteDescription({ type: "answer", sdp: answer });
  }

  start(stream: 0 | 1, onState: (state: PreviewState) => void): void {
    this.stop(false);
    this.active = true;
    const epoch = ++this.epoch;
    this.onState = onState;
    this.emit("loading");
    void this.open(epoch, stream).catch(() => {
      if (!this.active || epoch !== this.epoch) return;
      this.active = false;
      this.release();
      this.emit("error");
    });
  }

  private release(): void {
    const sessionUrl = this.sessionUrl;
    this.sessionUrl = "";
    if (sessionUrl) void this.fetcher(sessionUrl, {
      method: "DELETE",
      cache: "no-store",
      keepalive: true,
    }).catch(() => undefined);
    this.peer?.close();
    this.peer = null;
    this.video.pause();
    this.video.srcObject = null;
  }

  stop(emit = true): void {
    const wasActive = this.active || this.peer !== null || this.sessionUrl !== "";
    this.active = false;
    this.epoch += 1;
    this.release();
    if (emit && wasActive) this.emit("idle");
  }
}

function bytesEqualAt(buffer: Uint8Array, expected: Uint8Array, offset = 0): boolean {
  if (buffer.length - offset < expected.length) return false;
  for (let index = 0; index < expected.length; index += 1) {
    if (buffer[offset + index] !== expected[index]) return false;
  }
  return true;
}

function findHeaderEnd(buffer: Uint8Array): number {
  for (let index = 0; index + 3 < buffer.length; index += 1) {
    if (buffer[index] === 13 && buffer[index + 1] === 10 && buffer[index + 2] === 13 && buffer[index + 3] === 10) return index;
  }
  return -1;
}

export function multipartBoundary(contentType: string | null): string {
  if (!contentType || !/^multipart\/x-mixed-replace(?:\s*;|$)/i.test(contentType)) {
    throw new Error("MJPEG response is not multipart");
  }
  const match = /(?:^|;)\s*boundary\s*=\s*(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
  const value = match?.[1] ?? match?.[2] ?? "";
  if (!value || value.length > 70 || /[^\x21-\x7e]/.test(value)) {
    throw new Error("MJPEG response has an invalid boundary");
  }
  const normalized = value.startsWith("--") ? value.slice(2) : value;
  if (!normalized) throw new Error("MJPEG response has an invalid boundary");
  return normalized;
}

/**
 * Incrementally parses one bounded multipart MJPEG response. A frame is
 * returned only after Content-Length bytes and the following CRLF have arrived.
 */
export class MultipartMjpegParser {
  private readonly boundary: Uint8Array;
  private readonly maxFrameBytes: number;
  private readonly maxHeaderBytes: number;
  private buffer = new Uint8Array();
  private state: "boundary" | "headers" | "body" | "trailer" | "ended" = "boundary";
  private frame: Uint8Array | null = null;
  private frameOffset = 0;

  constructor(boundary: string, maxFrameBytes = 4 * 1024 * 1024, maxHeaderBytes = 4096) {
    if (!boundary || maxFrameBytes < 4 || maxHeaderBytes < 64) throw new Error("invalid MJPEG parser limits");
    this.boundary = new TextEncoder().encode(`--${boundary}`);
    this.maxFrameBytes = maxFrameBytes;
    this.maxHeaderBytes = maxHeaderBytes;
  }

  private discard(bytes: number): void {
    this.buffer = bytes >= this.buffer.length ? new Uint8Array() : this.buffer.slice(bytes);
  }

  private append(chunk: Uint8Array): void {
    if (!chunk.length) return;
    const limit = this.maxFrameBytes + this.maxHeaderBytes + this.boundary.length + 8;
    if (this.buffer.length + chunk.length > limit) throw new Error("MJPEG parser buffer limit exceeded");
    const joined = new Uint8Array(this.buffer.length + chunk.length);
    joined.set(this.buffer);
    joined.set(chunk, this.buffer.length);
    this.buffer = joined;
  }

  push(chunk: Uint8Array): Uint8Array[] {
    if (this.state === "ended") return [];
    this.append(chunk);
    const frames: Uint8Array[] = [];

    while (true) {
      if (this.state === "boundary") {
        if (this.buffer.length >= 2 && this.buffer[0] === 13 && this.buffer[1] === 10) this.discard(2);
        if (this.buffer.length < this.boundary.length + 2) break;
        if (!bytesEqualAt(this.buffer, this.boundary)) throw new Error("MJPEG boundary mismatch");
        const suffix = this.boundary.length;
        if (this.buffer[suffix] === 45 && this.buffer[suffix + 1] === 45) {
          this.discard(suffix + 2);
          this.state = "ended";
          break;
        }
        if (this.buffer[suffix] !== 13 || this.buffer[suffix + 1] !== 10) throw new Error("malformed MJPEG boundary");
        this.discard(suffix + 2);
        this.state = "headers";
        continue;
      }

      if (this.state === "headers") {
        const headerEnd = findHeaderEnd(this.buffer);
        if (headerEnd < 0) {
          if (this.buffer.length > this.maxHeaderBytes) throw new Error("MJPEG part headers are too large");
          break;
        }
        const headerText = new TextDecoder("ascii").decode(this.buffer.subarray(0, headerEnd));
        const headers = new Map<string, string>();
        for (const line of headerText.split("\r\n")) {
          const separator = line.indexOf(":");
          if (separator <= 0) throw new Error("malformed MJPEG part header");
          const name = line.slice(0, separator).trim().toLowerCase();
          if (headers.has(name)) throw new Error("duplicate MJPEG part header");
          headers.set(name, line.slice(separator + 1).trim());
        }
        const mediaType = (headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase();
        const rawLength = headers.get("content-length") ?? "";
        if (mediaType !== "image/jpeg" || !/^[0-9]+$/.test(rawLength)) throw new Error("invalid MJPEG part metadata");
        const length = Number(rawLength);
        if (!Number.isSafeInteger(length) || length < 4 || length > this.maxFrameBytes) throw new Error("MJPEG frame size is outside limits");
        this.discard(headerEnd + 4);
        this.frame = new Uint8Array(length);
        this.frameOffset = 0;
        this.state = "body";
        continue;
      }

      if (this.state === "body") {
        if (!this.frame) throw new Error("MJPEG parser lost frame state");
        const needed = this.frame.length - this.frameOffset;
        const available = Math.min(needed, this.buffer.length);
        this.frame.set(this.buffer.subarray(0, available), this.frameOffset);
        this.frameOffset += available;
        this.discard(available);
        if (this.frameOffset !== this.frame.length) break;
        this.state = "trailer";
        continue;
      }

      if (this.state === "trailer") {
        if (this.buffer.length < 2) break;
        if (this.buffer[0] !== 13 || this.buffer[1] !== 10 || !this.frame) throw new Error("malformed MJPEG frame trailer");
        if (this.frame[0] !== 0xff || this.frame[1] !== 0xd8 || this.frame.at(-2) !== 0xff || this.frame.at(-1) !== 0xd9) {
          throw new Error("incomplete JPEG frame");
        }
        this.discard(2);
        frames.push(this.frame);
        this.frame = null;
        this.frameOffset = 0;
        this.state = "boundary";
        continue;
      }

      break;
    }
    return frames;
  }
}

async function browserDecodeFrame(objectUrl: string): Promise<void> {
  const staging = new Image();
  if (typeof staging.decode === "function") {
    staging.src = objectUrl;
    await staging.decode();
    return;
  }
  await new Promise<void>((resolve, reject) => {
    staging.addEventListener("load", () => resolve(), { once: true });
    staging.addEventListener("error", () => reject(new Error("JPEG decode failed")), { once: true });
    staging.src = objectUrl;
  });
}

/** Owns one bounded multipart fetch and presents only fully decoded frames. */
export class MjpegPreview {
  private active = false;
  private stream: 0 | 1 = 0;
  private state: PreviewState = "idle";
  private epoch = 0;
  private retryRevision = 0;
  private deadlineTimer: number | null = null;
  private controller: AbortController | null = null;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private objectUrl = "";
  private nativeSource = "";
  private onState: (state: PreviewState) => void = () => undefined;
  private readonly initialTimeoutMs: number;
  private readonly stallTimeoutMs: number;
  private readonly readinessPollMs: number;
  private readonly transport: MjpegPreviewTransport;
  private readonly timers: PreviewTimers;
  private readonly fetcher: FetchPreview;
  private readonly createObjectUrl: (blob: Blob) => string;
  private readonly revokeObjectUrl: (url: string) => void;
  private readonly decodeFrame: DecodeFrame;

  constructor(private readonly image: HTMLImageElement, options: MjpegPreviewOptions = {}) {
    this.initialTimeoutMs = options.initialTimeoutMs ?? 12_000;
    this.stallTimeoutMs = options.stallTimeoutMs ?? 12_000;
    this.readinessPollMs = options.readinessPollMs ?? 250;
    this.transport = options.transport ?? preferredMjpegTransport();
    this.timers = options.timers ?? browserTimers;
    this.fetcher = options.fetcher ?? ((input, init) => window.fetch(input, init));
    this.createObjectUrl = options.createObjectUrl ?? ((blob) => URL.createObjectURL(blob));
    this.revokeObjectUrl = options.revokeObjectUrl ?? ((url) => URL.revokeObjectURL(url));
    this.decodeFrame = options.decodeFrame ?? browserDecodeFrame;
    this.image.addEventListener("load", () => {
      if (this.transport !== "native" || !this.active || this.image.getAttribute("src") !== this.nativeSource) return;
      this.clearDeadline();
      this.emit("live");
    });
    this.image.addEventListener("error", () => {
      if (this.transport !== "native" || !this.active || this.image.getAttribute("src") !== this.nativeSource) return;
      this.fail(this.epoch);
    });
  }

  private emit(state: PreviewState): void {
    this.state = state;
    this.onState(state);
  }

  private clearDeadline(): void {
    if (this.deadlineTimer === null) return;
    this.timers.clearTimeout(this.deadlineTimer);
    this.deadlineTimer = null;
  }

  private armDeadline(epoch: number, delay: number): void {
    this.clearDeadline();
    this.deadlineTimer = this.timers.setTimeout(() => {
      this.deadlineTimer = null;
      if (this.active && epoch === this.epoch) this.fail(epoch);
    }, delay);
  }

  private disconnect(clearImage: boolean): void {
    this.controller?.abort();
    this.controller = null;
    if (this.reader) void this.reader.cancel().catch(() => undefined);
    this.reader = null;
    if (!clearImage) return;
    this.image.src = "data:,";
    this.image.removeAttribute("src");
    this.nativeSource = "";
    if (this.objectUrl) this.revokeObjectUrl(this.objectUrl);
    this.objectUrl = "";
  }

  private openNative(source: string, epoch: number): void {
    this.nativeSource = source;
    this.image.src = source;
    let elapsed = 0;
    const checkReady = (): void => {
      this.deadlineTimer = null;
      if (!this.active || epoch !== this.epoch || this.image.getAttribute("src") !== source) return;
      if (this.image.naturalWidth > 0) {
        this.emit("live");
        return;
      }
      elapsed += this.readinessPollMs;
      if (elapsed >= this.initialTimeoutMs) {
        this.fail(epoch);
        return;
      }
      this.deadlineTimer = this.timers.setTimeout(checkReady, this.readinessPollMs);
    };
    this.deadlineTimer = this.timers.setTimeout(checkReady, this.readinessPollMs);
  }

  private async present(frame: Uint8Array, epoch: number): Promise<void> {
    // MultipartMjpegParser allocates each frame in its own exact ArrayBuffer.
    // Hand it to Blob without creating an additional full-frame JS copy.
    const bytes = frame.buffer as ArrayBuffer;
    const nextUrl = this.createObjectUrl(new Blob([bytes], { type: "image/jpeg" }));
    try {
      await this.decodeFrame(nextUrl);
      if (!this.active || epoch !== this.epoch) {
        this.revokeObjectUrl(nextUrl);
        return;
      }
      const previousUrl = this.objectUrl;
      this.objectUrl = nextUrl;
      this.image.src = nextUrl;
      if (previousUrl) this.revokeObjectUrl(previousUrl);
      this.emit("live");
      this.armDeadline(epoch, this.stallTimeoutMs);
    } catch (error) {
      this.revokeObjectUrl(nextUrl);
      throw error;
    }
  }

  private async open(stream: 0 | 1, force: boolean, epoch: number): Promise<void> {
    // Chromium can coalesce a synchronous same-URL retry into the failed image
    // request. uhttpd validates q but deliberately discards it before opening
    // Prudynt, so it is a bounded request revision with no encoder-side effect.
    const revision = force ? `&q=${50 + (++this.retryRevision % 50)}` : "";
    const source = `${routes.media.preview(stream)}${revision}`;
    if (this.transport === "native") {
      this.openNative(source, epoch);
      return;
    }
    const controller = new AbortController();
    this.controller = controller;
    this.armDeadline(epoch, this.initialTimeoutMs);

    try {
      const response = await this.fetcher(source, {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`MJPEG request failed (${response.status})`);
      const parser = new MultipartMjpegParser(multipartBoundary(response.headers.get("content-type")));
      const reader = response.body.getReader();
      this.reader = reader;
      while (this.active && epoch === this.epoch) {
        const result = await reader.read();
        if (result.done) throw new Error("MJPEG stream ended");
        const frames = parser.push(result.value);
        const latest = frames.at(-1);
        if (latest) await this.present(latest, epoch);
      }
    } catch (error) {
      if (this.active && epoch === this.epoch && !controller.signal.aborted) this.fail(epoch);
    }
  }

  private fail(epoch: number): void {
    if (epoch !== this.epoch) return;
    this.active = false;
    this.clearDeadline();
    this.disconnect(this.transport === "native");
    this.emit("error");
  }

  start(stream: 0 | 1, onState: (state: PreviewState) => void, force = false): void {
    this.onState = onState;
    if (!force && this.active && this.stream === stream) {
      onState(this.state);
      return;
    }
    this.stop();
    const epoch = ++this.epoch;
    this.stream = stream;
    this.active = true;
    this.emit("loading");
    void this.open(stream, force, epoch);
  }

  stop(): void {
    ++this.epoch;
    const wasActive = this.active || this.deadlineTimer !== null || this.controller !== null || this.objectUrl !== "" || this.image.hasAttribute("src");
    this.active = false;
    this.clearDeadline();
    if (wasActive) this.disconnect(true);
    this.emit("idle");
  }
}
