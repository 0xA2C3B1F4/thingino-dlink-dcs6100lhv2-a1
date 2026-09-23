import assert from "node:assert/strict";
import test from "node:test";
import { PreviewAudioPlayback, type PreviewPlaybackState } from "../src/api/preview-audio";

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
function setup() {
  const video = { muted: true, play: () => Promise.resolve() };
  const states: PreviewPlaybackState[] = [];
  const playback = new PreviewAudioPlayback(video as HTMLVideoElement, state => states.push(state));
  return { video, states, playback };
}

test("transient audio loss preserves explicit intent, but reset clears it", async () => {
  const { video, states, playback } = setup();
  playback.setAvailable(true);
  playback.mediaReady();
  await settle();
  assert.equal(video.muted, true);
  playback.toggle();
  assert.equal(video.muted, false, "unmute/play must happen in the click gesture");
  await settle();
  assert.equal(states.at(-1)?.active, true);
  playback.setAvailable(false);
  assert.equal(video.muted, true);
  assert.equal(states.at(-1)?.active, false);
  playback.mediaReady(); // video before audio on the replacement connection
  await settle();
  assert.equal(video.muted, true);
  playback.setAvailable(true);
  playback.mediaReady();
  await settle();
  assert.equal(video.muted, false);
  assert.equal(states.at(-1)?.active, true);
  playback.reset(); // explicit Mute, Reload, stream change, or disposal
  playback.setAvailable(false);
  playback.setAvailable(true);
  playback.mediaReady();
  await settle();
  assert.equal(video.muted, true);
  assert.equal(states.at(-1)?.active, false);
});

test("stale play success after explicit mute cannot re-enable playback", async () => {
  const { video, states, playback } = setup();
  let resolve!: () => void;
  video.play = () => new Promise<void>(done => { resolve = done; });
  playback.setAvailable(true);
  playback.toggle();
  playback.toggle();
  resolve();
  await settle();
  assert.equal(video.muted, true);
  assert.equal(states.at(-1)?.active, false);
});

test("stale play rejection from old connection cannot mute resumed playback", async () => {
  const { video, states, playback } = setup();
  let reject!: (error: Error) => void;
  video.play = () => new Promise<void>((_done, fail) => { reject = fail; });
  playback.setAvailable(true);
  playback.toggle();
  playback.setAvailable(false);
  video.play = () => Promise.resolve();
  playback.setAvailable(true);
  playback.mediaReady();
  await settle();
  reject(new Error("old playback failed"));
  await settle();
  assert.equal(video.muted, false);
  assert.equal(states.at(-1)?.active, true);
});

test("blocked resume remains muted and supports a fresh explicit Listen", async () => {
  const { video, states, playback } = setup();
  playback.setAvailable(true);
  playback.toggle();
  await settle();
  playback.setAvailable(false);
  video.play = () => Promise.reject(new Error("autoplay blocked"));
  playback.setAvailable(true);
  playback.mediaReady();
  await settle();
  assert.equal(video.muted, true);
  assert.equal(states.at(-1)?.active, false);
  assert.match(states.at(-1)!.message, /Browser blocked playback/);
  video.play = () => Promise.resolve();
  playback.toggle();
  await settle();
  assert.equal(video.muted, false);
  assert.equal(states.at(-1)?.active, true);
});

test("blocked audible playback retries video muted once without claiming audio", async () => {
  const { video, states, playback } = setup();
  const attempts: boolean[] = [];
  video.play = () => {
    attempts.push(video.muted);
    return video.muted ? Promise.resolve() : Promise.reject(new Error("audible autoplay blocked"));
  };
  playback.setAvailable(true);
  playback.toggle();
  await settle();
  assert.deepEqual(attempts, [false, true]);
  assert.equal(video.muted, true);
  assert.equal(states.at(-1)?.active, false);
  assert.match(states.at(-1)!.message, /Browser blocked playback/);
});
