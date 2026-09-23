import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("/__fixture__/reset");
});

// State-machine tests with synthetic tracks, not proof of audible playback.
async function mockAudio(page: import("@playwright/test").Page, videoFirst = false): Promise<void> {
  await page.addInitScript((videoFirst) => {
    Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value() {
      return Promise.resolve();
    } });
    class Peer {
      iceGatheringState = "complete";
      connectionState = "connected";
      localDescription: RTCSessionDescriptionInit | null = null;
      ontrack: ((event: unknown) => void) | null = null;
      statsReads = 0;
      getStats() {
        return Promise.resolve(new Map([["audio", { type: "inbound-rtp", kind: "audio",
          packetsReceived: ++this.statsReads, bytesReceived: this.statsReads * 40, totalAudioEnergy: 0,
          address: "not-for-diagnostics" }]]));
      }
      addTransceiver() { return { setCodecPreferences() {} }; }
      addEventListener() {}
      removeEventListener() {}
      createOffer() { return Promise.resolve({ type: "offer", sdp: "v=0\r\n" }); }
      setLocalDescription(value: RTCSessionDescriptionInit) { this.localDescription = value; return Promise.resolve(); }
      setRemoteDescription() {
        const canvas = document.createElement("canvas");
        const video = canvas.captureStream().getVideoTracks()[0];
        const context = new AudioContext();
        const audio = context.createMediaStreamDestination().stream.getAudioTracks()[0];
        for (const track of videoFirst ? [video, audio] : [audio, video]) this.ontrack?.({ track, streams: [] });
        return Promise.resolve();
      }
      close() { this.connectionState = "closed"; }
    }
    Object.defineProperty(window, "RTCPeerConnection", { value: Peer });
  }, videoFirst);
  await page.route("**/api/v1/media/webrtc/whip**", route => route.fulfill(
    route.request().method() === "POST"
      ? { status: 201, contentType: "application/sdp", body: "v=0\r\n", headers: { Location: "/api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef" } }
      : { status: 204 },
  ));
}

test("Listen only unmutes browser playback and resets on stream change", async ({ page }) => {
  await mockAudio(page);
  let cameraWrites = 0;
  page.on("request", request => { if (request.url().includes("/actions/control")) cameraWrites += 1; });
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const listen = page.getByRole("button", { name: "Listen to camera audio", exact: true });
  const video = page.locator(".preview-page .preview-frame video");
  await expect(listen).toBeEnabled();
  await expect(listen).toHaveAttribute("aria-pressed", "false");
  expect(await video.evaluate((element: HTMLVideoElement) => element.muted)).toBe(true);
  await listen.click();
  await expect(listen).toHaveAttribute("aria-pressed", "true");
  expect(await video.evaluate((element: HTMLVideoElement) => element.muted)).toBe(false);
  await listen.click();
  await expect(listen).toHaveAttribute("aria-pressed", "false");
  await listen.click();
  await page.getByLabel("Preview stream", { exact: true }).selectOption("1");
  await expect(listen).toHaveAttribute("aria-pressed", "false");
  expect(await video.evaluate((element: HTMLVideoElement) => element.muted)).toBe(true);
  expect(cameraWrites).toBe(0);
});

for (const videoFirst of [false, true]) {
  test(`temporary visibility reconnect preserves Listen, ${videoFirst ? "video" : "audio"}-first tracks`, async ({ page }) => {
    await mockAudio(page, videoFirst);
    let posts = 0;
    let cameraWrites = 0;
    page.on("request", request => {
      if (request.method() === "POST" && request.url().includes("/media/webrtc/whip")) ++posts;
      if (request.url().includes("/actions/control")) ++cameraWrites;
    });
    await page.goto("/");
    await page.getByLabel("Password").fill("thingino");
    await page.getByRole("button", { name: "Log in", exact: true }).click();
    const listen = page.getByRole("button", { name: "Listen to camera audio", exact: true });
    await expect(listen).toBeEnabled();
    await listen.click();
    await expect(listen).toHaveAttribute("aria-pressed", "true");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(listen).toBeDisabled();
    await expect(listen).toHaveAttribute("aria-pressed", "false");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await expect(listen).toHaveAttribute("aria-pressed", "true");
    expect(posts).toBe(2);
    expect(cameraWrites).toBe(0);
    expect(await page.locator(".preview-page .preview-frame video").evaluate((v: HTMLVideoElement) => v.muted)).toBe(false);
    await page.getByRole("button", { name: "Reload", exact: true }).click();
    await expect(listen).toBeEnabled();
    await expect(listen).toHaveAttribute("aria-pressed", "false");
    expect(await page.locator(".preview-page .preview-frame video").evaluate((v: HTMLVideoElement) => v.muted)).toBe(true);
  });
}

test("focus return cancels a pending stop, and resumes after an elapsed stop", async ({ page }) => {
  await mockAudio(page);
  await page.route("**/api/v1/config/webui", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), track_focus: true, focus_timeout: 1 } });
  });
  let posts = 0;
  page.on("request", request => { if (request.method() === "POST" && request.url().includes("/media/webrtc/whip")) ++posts; });
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const listen = page.getByRole("button", { name: "Listen to camera audio", exact: true });
  await expect(listen).toBeEnabled();
  await listen.click();
  await expect(listen).toHaveAttribute("aria-pressed", "true");
  await page.evaluate(() => { window.dispatchEvent(new Event("blur")); window.dispatchEvent(new Event("focus")); });
  await page.waitForTimeout(1200); // Must exceed the configured stop deadline.
  await expect(listen).toBeEnabled();
  await expect(listen).toHaveAttribute("aria-pressed", "true");
  expect(posts).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect(listen).toBeDisabled();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(listen).toHaveAttribute("aria-pressed", "true");
  expect(posts).toBe(2);
});

test("diagnostics expose only bounded audio counters and do not change playback", async ({ page }) => {
  await mockAudio(page);
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const listen = page.getByRole("button", { name: "Listen to camera audio", exact: true });
  await expect(listen).toBeEnabled();
  await page.getByText("Audio diagnostics", { exact: true }).click();
  await page.getByRole("button", { name: "Inspect received audio" }).click();
  const diagnostic = page.locator(".preview-page details pre");
  await expect(diagnostic).toContainText('"packetDelta": 1');
  await expect(diagnostic).toContainText('"byteDelta": 40');
  await expect(diagnostic).not.toContainText("not-for-diagnostics");
  await expect(listen).toHaveAttribute("aria-pressed", "false");
});

test("MJPEG fallback explicitly disables audio listening", async ({ page }) => {
  await page.addInitScript(() => { Object.defineProperty(window, "RTCPeerConnection", { value: undefined }); });
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Listen to camera audio", exact: true })).toBeDisabled();
  await expect(page.getByText("MJPEG preview has no audio.", { exact: true })).toBeVisible();
});

test("rejected browser playback stays muted and allows an explicit retry", async ({ page }) => {
  await mockAudio(page);
  await page.goto("/");
  await page.getByLabel("Password").fill("thingino");
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  const listen = page.getByRole("button", { name: "Listen to camera audio", exact: true });
  await expect(listen).toBeEnabled();
  await page.evaluate(() => {
    Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value() {
      return Promise.reject(new Error("playback blocked"));
    } });
  });
  await listen.click();
  await expect(page.getByText("Browser blocked playback. Click Listen to try again.", { exact: true })).toBeVisible();
  await expect(listen).toHaveAttribute("aria-pressed", "false");
  expect(await page.locator(".preview-page .preview-frame video").evaluate((element: HTMLVideoElement) => element.muted)).toBe(true);
});

test("receive-only offer rejects a sendrecv audio answer", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const peer = new RTCPeerConnection();
    try {
      peer.addTransceiver("video", { direction: "recvonly" });
      peer.addTransceiver("audio", { direction: "recvonly" });
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const sections = offer.sdp!.replaceAll("a=setup:actpass", "a=setup:passive").split("m=");
      const answer = sections.map(section => section.replaceAll("a=recvonly", section.startsWith("audio ") ? "a=sendrecv" : "a=sendonly")).join("m=");
      let incompatible = "accepted";
      try {
        await peer.setRemoteDescription({ type: "answer", sdp: answer });
      } catch (error) { incompatible = error instanceof Error ? error.message : "unknown error"; }
      if (incompatible === "accepted") return { incompatible, corrected: false };
      await peer.setRemoteDescription({ type: "answer", sdp: answer.replaceAll("a=sendrecv", "a=sendonly") });
      return { incompatible, corrected: true };
    } finally { peer.close(); }
  });
  expect(result.incompatible).toContain("Incompatible send direction");
  expect(result.corrected).toBe(true);
});
