import assert from "node:assert/strict";
import test from "node:test";
import {
  MjpegPreview,
  MultipartMjpegParser,
  WhipPreview,
  multipartBoundary,
  preferredMjpegTransport,
  rwdWhipEndpoint,
  type PreviewTimers,
} from "../src/api/media";

class FakeImage {
  src = "";
  naturalWidth = 0;
  private readonly listeners = new Map<string, Array<() => void>>();
  getAttribute(name: string): string | null { return name === "src" && this.src ? this.src : null; }
  removeAttribute(name: string): void { if (name === "src") this.src = ""; }
  hasAttribute(name: string): boolean { return name === "src" && this.src !== ""; }
  addEventListener(name: string, listener: () => void): void {
    const listeners = this.listeners.get(name) ?? [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }
  dispatch(name: "load" | "error"): void {
    for (const listener of this.listeners.get(name) ?? []) listener();
  }
}

class FakeTimers implements PreviewTimers {
  private nextId = 1;
  readonly jobs = new Map<number, () => void>();
  setTimeout(callback: () => void): number { const id = this.nextId++; this.jobs.set(id, callback); return id; }
  clearTimeout(handle: number): void { this.jobs.delete(handle); }
  runNext(): void {
    const job = this.jobs.entries().next().value as [number, () => void] | undefined;
    if (!job) return;
    this.jobs.delete(job[0]);
    job[1]();
  }
}

class FakeVideo {
  muted = true;
  srcObject: MediaProvider | null = null;
  plays = 0;
  pauses = 0;
  play(): Promise<void> { this.plays += 1; return Promise.resolve(); }
  pause(): void { this.pauses += 1; }
}

class FakePeer {
  readonly transceivers: Array<{ kind: string; direction: RTCRtpTransceiverDirection | undefined }> = [];
  iceGatheringState: RTCIceGatheringState = "complete";
  connectionState: RTCPeerConnectionState = "new";
  localDescription: RTCSessionDescription | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  closed = false;
  addTransceiver(kind: string, init?: RTCRtpTransceiverInit): RTCRtpTransceiver {
    this.transceivers.push({ kind, direction: init?.direction });
    return { setCodecPreferences: () => undefined } as unknown as RTCRtpTransceiver;
  }
  addEventListener(_name: string, _listener: EventListenerOrEventListenerObject): void {}
  removeEventListener(_name: string, _listener: EventListenerOrEventListenerObject): void {}
  async createOffer(): Promise<RTCSessionDescriptionInit> { return { type: "offer", sdp: "v=0\r\n" }; }
  async setLocalDescription(value: RTCLocalSessionDescriptionInit): Promise<void> {
    this.localDescription = value as RTCSessionDescription;
  }
  async setRemoteDescription(): Promise<void> {}
  close(): void { this.closed = true; this.connectionState = "closed"; }
}

class GatheringPeer extends FakePeer {
  override iceGatheringState: RTCIceGatheringState = "gathering";
  private readonly iceListeners = new Set<() => void>();
  override addEventListener(name: string, listener: EventListenerOrEventListenerObject): void {
    if (name === "icegatheringstatechange" && typeof listener === "function") this.iceListeners.add(listener as () => void);
  }
  override removeEventListener(name: string, listener: EventListenerOrEventListenerObject): void {
    if (name === "icegatheringstatechange" && typeof listener === "function") this.iceListeners.delete(listener as () => void);
  }
}

const encoder = new TextEncoder();
const jpegA = Uint8Array.of(0xff, 0xd8, 0x11, 0x22, 0xff, 0xd9);
const jpegB = Uint8Array.of(0xff, 0xd8, 0x33, 0x44, 0x55, 0xff, 0xd9);

function concat(...parts: Uint8Array[]): Uint8Array {
  const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function multipartPart(frame: Uint8Array, boundary = "frame"): Uint8Array {
  return concat(
    encoder.encode(`--${boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`),
    frame,
    encoder.encode("\r\n"),
  );
}

function controlledResponse(): {
  response: Response;
  push: (bytes: Uint8Array) => void;
  fail: () => void;
  wasCancelled: () => boolean;
} {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel() { cancelled = true; },
  });
  return {
    response: new Response(body, { headers: { "Content-Type": "multipart/x-mixed-replace; boundary=frame" } }),
    push: (bytes) => controller.enqueue(bytes),
    fail: () => controller.error(new Error("connection lost")),
    wasCancelled: () => cancelled,
  };
}

async function settle(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("multipart boundary accepts quoted values and rejects non-MJPEG responses", () => {
  assert.equal(multipartBoundary("multipart/x-mixed-replace; boundary=frame"), "frame");
  assert.equal(multipartBoundary('multipart/x-mixed-replace; boundary="--camera"'), "camera");
  assert.throws(() => multipartBoundary("image/jpeg"), /not multipart/);
  assert.throws(() => multipartBoundary("multipart/x-mixed-replace"), /invalid boundary/);
});

test("Apple WebKit uses native MJPEG while Chromium keeps the bounded fetch parser", () => {
  assert.equal(preferredMjpegTransport("Mozilla/5.0 AppleWebKit/605.1.15 Safari/605.1.15", "Apple Computer, Inc."), "native");
  assert.equal(preferredMjpegTransport("Mozilla/5.0 AppleWebKit/537.36 Chrome/140.0", "Google Inc."), "fetch");
  assert.equal(preferredMjpegTransport("Mozilla/5.0 Gecko/20100101 Firefox/142.0", ""), "fetch");
});

test("Raptor WHIP signaling stays on the authenticated page origin", () => {
  assert.equal(rwdWhipEndpoint(0, "https://camera.local/#/preview"), "https://camera.local/api/v1/media/webrtc/whip?stream=0");
  assert.equal(rwdWhipEndpoint(1, "https://camera.local/#/preview"), "https://camera.local/api/v1/media/webrtc/whip?stream=1");
});

test("video-only WHIP session is deleted before peer state is released", async () => {
  const video = new FakeVideo();
  const peer = new FakePeer();
  const requests: Array<{ url: string; method: string; keepalive: boolean }> = [];
  const states: string[] = [];
  const preview = new WhipPreview(video as unknown as HTMLVideoElement, {
    peerFactory: () => peer as unknown as RTCPeerConnection,
    endpoint: (stream) => `https://camera.local/api/v1/media/webrtc/whip?stream=${stream}`,
    fetcher: async (input, init) => {
      const method = init?.method ?? "GET";
      requests.push({ url: String(input), method, keepalive: init?.keepalive === true });
      if (method === "POST") {
        return new Response("v=0\r\n", { status: 201, headers: { Location: "/api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef" } });
      }
      return new Response(null, { status: 204 });
    },
  });

  preview.start(1, (state) => states.push(state));
  await settle();
  assert.deepEqual(peer.transceivers, [{ kind: "video", direction: "recvonly" }]);
  peer.ontrack?.({
    track: { kind: "video" },
    streams: [{} as MediaStream],
  } as unknown as RTCTrackEvent);
  assert.equal(states.at(-1), "live");
  assert.equal(video.plays, 1);
  preview.stop();
  await settle();
  assert.deepEqual(requests, [
    { url: "https://camera.local/api/v1/media/webrtc/whip?stream=1", method: "POST", keepalive: false },
    { url: "https://camera.local/api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef", method: "DELETE", keepalive: true },
  ]);
  assert.equal(peer.closed, true);
  assert.equal(video.srcObject, null);
});

test("audio preview merges streamless audio and video tracks and resets playback on stop", async () => {
  const original = globalThis.MediaStream;
  class FakeStream {
    readonly tracks: MediaStreamTrack[] = [];
    addTrack(track: MediaStreamTrack): void { this.tracks.push(track); }
  }
  globalThis.MediaStream = FakeStream as unknown as typeof MediaStream;
  try {
    const video = new FakeVideo();
    const peer = new FakePeer();
    const availability: boolean[] = [];
    const states: string[] = [];
    let ended: (() => void) | undefined;
    const preview = new WhipPreview(video as unknown as HTMLVideoElement, {
      receiveAudio: true,
      onAudioAvailable: (value) => availability.push(value),
      peerFactory: () => peer as unknown as RTCPeerConnection,
      fetcher: async (_input, init) => init?.method === "POST"
        ? new Response("v=0\r\n", { status: 201, headers: { Location: "/session" } })
        : new Response(null, { status: 204 }),
    });
    preview.start(0, (state) => states.push(state));
    await settle();
    assert.deepEqual(peer.transceivers, [
      { kind: "video", direction: "recvonly" }, { kind: "audio", direction: "recvonly" },
    ]);
    const audio = { kind: "audio", addEventListener: (_name: string, listener: () => void) => { ended = listener; } };
    const picture = { kind: "video" };
    peer.ontrack?.({ track: audio, streams: [] } as unknown as RTCTrackEvent);
    assert.equal(states.at(-1), "loading", "audio alone must not mark video live");
    peer.ontrack?.({ track: picture, streams: [] } as unknown as RTCTrackEvent);
    assert.deepEqual((video.srcObject as unknown as FakeStream).tracks, [audio, picture]);
    assert.equal(states.at(-1), "live");
    assert.equal(availability.at(-1), true);
    assert.equal(video.muted, true, "receiving audio must not automatically enable listening");
    video.muted = false;
    ended?.();
    assert.equal(video.muted, true);
    assert.equal(availability.at(-1), false);
    video.muted = false;
    preview.stop();
    assert.equal(video.muted, true);
    assert.equal(video.srcObject, null);
    peer.ontrack?.({ track: audio, streams: [] } as unknown as RTCTrackEvent);
    assert.equal(availability.at(-1), false, "stale tracks must not revive audio availability");
  } finally {
    globalThis.MediaStream = original;
  }
});

test("WHIP ICE deadline closes the peer before MJPEG fallback", async () => {
  const video = new FakeVideo();
  const peer = new GatheringPeer();
  const timers = new FakeTimers();
  const states: string[] = [];
  let fetches = 0;
  const preview = new WhipPreview(video as unknown as HTMLVideoElement, {
    peerFactory: () => peer as unknown as RTCPeerConnection,
    timers,
    iceTimeoutMs: 250,
    fetcher: async () => { fetches += 1; throw new Error("must not signal before ICE completes"); },
  });

  preview.start(0, (state) => states.push(state));
  await settle();
  timers.runNext();
  await settle();
  assert.equal(states.at(-1), "error");
  assert.equal(peer.closed, true);
  assert.equal(fetches, 0);
});

test("WHIP reports the negotiation error after deleting its failed session", async () => {
  const video = new FakeVideo();
  const peer = new FakePeer();
  peer.setRemoteDescription = async () => { throw new Error("invalid audio direction"); };
  const errors: string[] = [];
  const methods: string[] = [];
  const preview = new WhipPreview(video as unknown as HTMLVideoElement, {
    peerFactory: () => peer as unknown as RTCPeerConnection,
    onError: message => errors.push(message),
    fetcher: async (_input, init) => {
      methods.push(init?.method ?? "GET");
      return init?.method === "POST"
        ? new Response("v=0\r\n", { status: 201, headers: { Location: "/session" } })
        : new Response(null, { status: 204 });
    },
  });
  preview.start(0, () => undefined);
  await settle();
  assert.deepEqual(errors, ["invalid audio direction"]);
  assert.deepEqual(methods, ["POST", "DELETE"]);
  assert.equal(peer.closed, true);
  assert.equal(video.srcObject, null);
});

test("native MJPEG becomes live without Fetch and disconnects cleanly", () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const states: string[] = [];
  let fetches = 0;
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    transport: "native",
    timers,
    fetcher: async () => { fetches += 1; throw new Error("native transport must not fetch"); },
  });

  preview.start(0, (state) => states.push(state));
  assert.equal(image.src, "/media/v1/mjpeg?stream=0");
  image.naturalWidth = 1920;
  image.dispatch("load");
  assert.equal(states.at(-1), "live");
  assert.equal(fetches, 0);

  preview.stop();
  assert.equal(image.src, "");
  image.dispatch("load");
  assert.equal(states.at(-1), "idle", "a stale native load must not revive a stopped preview");
});

test("native MJPEG timeout closes the multipart response", () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const states: string[] = [];
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    transport: "native",
    initialTimeoutMs: 250,
    readinessPollMs: 250,
    timers,
  });

  preview.start(1, (state) => states.push(state));
  timers.runNext();
  assert.equal(states.at(-1), "error");
  assert.equal(image.src, "");
});

test("multipart parser publishes a frame only after its full body and trailer", () => {
  const parser = new MultipartMjpegParser("frame");
  const wire = multipartPart(jpegA);
  assert.deepEqual(parser.push(wire.subarray(0, wire.length - 1)), []);
  const frames = parser.push(wire.subarray(wire.length - 1));
  assert.equal(frames.length, 1);
  assert.deepEqual(frames[0], jpegA);
});

test("multipart parser handles split headers and multiple frames without an unbounded body buffer", () => {
  const parser = new MultipartMjpegParser("frame", 64, 256);
  const wire = concat(multipartPart(jpegA), multipartPart(jpegB));
  const frames: Uint8Array[] = [];
  for (let offset = 0; offset < wire.length; offset += 3) {
    frames.push(...parser.push(wire.subarray(offset, offset + 3)));
  }
  assert.deepEqual(frames, [jpegA, jpegB]);
});

test("multipart parser rejects incomplete JPEGs and oversized declarations", () => {
  const bad = Uint8Array.of(0xff, 0xd8, 0x11, 0x22, 0x00, 0x00);
  const parser = new MultipartMjpegParser("frame");
  assert.throws(() => parser.push(multipartPart(bad)), /incomplete JPEG/);

  const oversized = new MultipartMjpegParser("frame", 8, 256);
  assert.throws(
    () => oversized.push(encoder.encode("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 9\r\n\r\n")),
    /outside limits/,
  );
});

test("preview keeps one request and presents only a completely parsed frame", async () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const stream = controlledResponse();
  const requests: string[] = [];
  const states: string[] = [];
  let nextUrl = 0;
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    timers,
    fetcher: async (input) => { requests.push(String(input)); return stream.response; },
    createObjectUrl: () => `blob:frame-${++nextUrl}`,
    revokeObjectUrl: () => undefined,
    decodeFrame: async () => undefined,
  });

  preview.start(1, (state) => states.push(state));
  preview.start(1, (state) => states.push(state));
  assert.deepEqual(requests, ["/media/v1/mjpeg?stream=1"]);
  const wire = multipartPart(jpegA);
  stream.push(wire.subarray(0, wire.length - 1));
  await settle();
  assert.equal(image.src, "", "partial JPEG bytes must never reach the visible image");
  stream.push(wire.subarray(wire.length - 1));
  await settle();
  assert.equal(image.src, "blob:frame-1");
  assert.equal(states.at(-1), "live");
});

test("forced retries use distinct URLs and cancel the previous response", async () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const streams = [controlledResponse(), controlledResponse()];
  const requests: string[] = [];
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    timers,
    fetcher: async (input) => { requests.push(String(input)); return streams[requests.length - 1]!.response; },
    createObjectUrl: () => "blob:unused",
    revokeObjectUrl: () => undefined,
    decodeFrame: async () => undefined,
  });

  preview.start(0, () => undefined, true);
  await settle();
  preview.start(0, () => undefined, true);
  await settle();
  assert.match(requests[0]!, /^\/media\/v1\/mjpeg\?stream=0&q=51$/);
  assert.match(requests[1]!, /^\/media\/v1\/mjpeg\?stream=0&q=52$/);
  assert.equal(streams[0]!.wasCancelled(), true);
});

test("callbacks from an older decode cannot replace a newer frame", async () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const streams = [controlledResponse(), controlledResponse()];
  const revoked: string[] = [];
  let nextUrl = 0;
  let releaseFirst: (() => void) | undefined;
  const firstDecode = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let requestIndex = 0;
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    timers,
    fetcher: async () => streams[requestIndex++]!.response,
    createObjectUrl: () => `blob:frame-${++nextUrl}`,
    revokeObjectUrl: (url) => revoked.push(url),
    decodeFrame: async (url) => { if (url === "blob:frame-1") await firstDecode; },
  });

  preview.start(0, () => undefined);
  streams[0]!.push(multipartPart(jpegA));
  await settle();
  preview.start(0, () => undefined, true);
  streams[1]!.push(multipartPart(jpegB));
  await settle();
  assert.equal(image.src, "blob:frame-2");
  releaseFirst?.();
  await settle();
  assert.equal(image.src, "blob:frame-2");
  assert.ok(revoked.includes("blob:frame-1"));
});

test("stream failure keeps the last complete frame and stop releases it", async () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const stream = controlledResponse();
  const states: string[] = [];
  const revoked: string[] = [];
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    timers,
    fetcher: async () => stream.response,
    createObjectUrl: () => "blob:last-good",
    revokeObjectUrl: (url) => revoked.push(url),
    decodeFrame: async () => undefined,
  });

  preview.start(0, (state) => states.push(state));
  stream.push(multipartPart(jpegA));
  await settle();
  stream.fail();
  await settle();
  assert.equal(states.at(-1), "error");
  assert.equal(image.src, "blob:last-good");
  preview.stop();
  assert.equal(image.src, "");
  assert.deepEqual(revoked, ["blob:last-good"]);
});

test("initial frame deadline fails a silent response", async () => {
  const image = new FakeImage();
  const timers = new FakeTimers();
  const stream = controlledResponse();
  const states: string[] = [];
  const preview = new MjpegPreview(image as unknown as HTMLImageElement, {
    initialTimeoutMs: 100,
    timers,
    fetcher: async () => stream.response,
    createObjectUrl: () => "blob:unused",
    revokeObjectUrl: () => undefined,
    decodeFrame: async () => undefined,
  });
  preview.start(0, (state) => states.push(state));
  timers.runNext();
  assert.equal(states.at(-1), "error");
});
