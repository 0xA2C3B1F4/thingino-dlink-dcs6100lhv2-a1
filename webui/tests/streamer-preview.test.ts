import assert from "node:assert/strict";
import test from "node:test";
import { StreamerPreviewRetry, StreamerPreviewSession, isStreamerPreviewRoute, streamerPreviewStreamUsable } from "../src/app/streamer-preview";
import type { PreviewTimers } from "../src/api/media";
import { previewStreamUsable } from "../src/pages/preview";

test("Preview and Streamer retain their distinct enabled and available policies for both streams", () => {
  for (const stream of [0, 1] as const) {
    for (const enabled of [false, true]) {
      for (const available of [false, true]) {
        const value = { enabled, available, snapshot_url: null };
        const media = { stream0: value, stream1: value } as Parameters<typeof previewStreamUsable>[1];
        const context = JSON.stringify({ stream, enabled, available });
        assert.equal(previewStreamUsable(stream, media), enabled && (stream === 0 || available), context);
        assert.equal(streamerPreviewStreamUsable(stream, media), enabled, context);
      }
    }
  }
});

class FakeTimers implements PreviewTimers {
  private nextId = 1;
  readonly jobs = new Map<number, () => void>();
  setTimeout(callback: () => void): number { const id = this.nextId++; this.jobs.set(id, callback); return id; }
  clearTimeout(handle: number): void { this.jobs.delete(handle); }
  runOne(): void {
    const [id, callback] = this.jobs.entries().next().value!;
    this.jobs.delete(id);
    callback();
  }
}

test("Streamer preview eligibility is limited to the four configuration routes", () => {
  assert.equal(isStreamerPreviewRoute("imaging"), true);
  assert.equal(isStreamerPreviewRoute("streams"), true);
  assert.equal(isStreamerPreviewRoute("osd"), true);
  assert.equal(isStreamerPreviewRoute("motion-privacy"), true);
  assert.equal(isStreamerPreviewRoute("sensor"), false);
  assert.equal(isStreamerPreviewRoute("preview"), false);
});

test("Streamer preview can probe either enabled stream while media is starting", () => {
  const media = {
    stream0: { available: false, enabled: true, snapshot_url: null },
    stream1: { available: false, enabled: true, snapshot_url: null },
  } as Parameters<typeof streamerPreviewStreamUsable>[1];
  assert.equal(streamerPreviewStreamUsable(0, media), true);
  assert.equal(streamerPreviewStreamUsable(1, media), true);
  assert.equal(streamerPreviewStreamUsable(0, { ...media, stream0: { available: true, enabled: false, snapshot_url: null } }), false);
});

test("Streamer preview keeps the desired session state across eligible routes but stops on exit", () => {
  const session = new StreamerPreviewSession();
  assert.deepEqual(session.setRoute("imaging"), []);
  assert.deepEqual(session.show(), ["start"]);
  assert.equal(session.isDesired, true);
  assert.deepEqual(session.setRoute("streams"), []);
  assert.deepEqual(session.setRoute("sensor"), ["stop"]);
  assert.equal(session.isDesired, true);
  assert.deepEqual(session.setRoute("osd"), ["start"]);
  assert.deepEqual(session.hide(), ["stop"]);
  assert.equal(session.isDesired, false);
});

test("desired state can be restored for a new controller in the same browser session", () => {
  const session = new StreamerPreviewSession();
  session.restoreDesired(true);
  assert.deepEqual(session.setRoute("imaging"), ["start"]);
  assert.equal(session.isDesired, true);
  session.restoreDesired(false);
  assert.deepEqual(session.setRoute("streams"), []);
  assert.equal(session.isDesired, false);
});

test("visibility and hide cancel retries, while one error schedules one bounded retry", () => {
  const session = new StreamerPreviewSession();
  session.setRoute("osd");
  session.show();
  assert.deepEqual(session.onPreviewState("error"), ["schedule-retry"]);
  assert.equal(session.retryPending, true);
  assert.deepEqual(session.onPreviewState("error"), []);
  assert.equal(session.retryDelayMs(), 1_000);
  assert.deepEqual(session.setDocumentVisible(false), ["stop"]);
  assert.equal(session.retryPending, false);
  assert.deepEqual(session.setDocumentVisible(true), ["start"]);
  assert.deepEqual(session.onPreviewState("error"), ["schedule-retry"]);
  assert.deepEqual(session.hide(), ["stop"]);
  assert.equal(session.retryPending, false);
  assert.deepEqual(session.retryTimerFired(), []);
});

test("a live frame resets retry backoff and timer fire produces one restart", () => {
  const session = new StreamerPreviewSession();
  session.setRoute("streams");
  session.show();
  assert.deepEqual(session.onPreviewState("error"), ["schedule-retry"]);
  assert.deepEqual(session.retryTimerFired(), ["start"]);
  assert.equal(session.retryDelayMs(), 2_000);
  assert.deepEqual(session.onPreviewState("live"), []);
  assert.equal(session.retryDelayMs(), 1_000);
  assert.deepEqual(session.onPreviewState("error"), ["schedule-retry"]);
  assert.deepEqual(session.retryTimerFired(), ["start"]);
});

test("retry controller has one outstanding timer and cancellation prevents a late restart", () => {
  const session = new StreamerPreviewSession();
  const timers = new FakeTimers();
  const starts: number[] = [];
  const retry = new StreamerPreviewRetry(session, timers, () => starts.push(1));
  session.setRoute("osd");
  session.show();
  retry.handleState("error");
  retry.handleState("error");
  assert.equal(timers.jobs.size, 1);
  retry.cancel();
  assert.equal(timers.jobs.size, 0);
  assert.deepEqual(starts, []);
  retry.handleState("error");
  timers.runOne();
  assert.deepEqual(starts, [1]);
});
