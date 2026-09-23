import assert from "node:assert/strict";
import test from "node:test";
import { motionPrivacy, buildMotionEmailUpdate, buildMotionFtpUpdate, buildMotionGotifyUpdate, buildMotionNtfyUpdate, buildMotionPrivacyUpdate, buildMotionTelegramUpdate, buildMotionWebhookUpdate, decodeRaptorMotionRoi, loadRaptorMotionMqttStatus, raptorMotionEmail, raptorMotionFtp, raptorMotionGotify, raptorMotionNtfy, raptorMotionTelegram, raptorMotionWebhook } from "../src/pages/config/motion-privacy";
import { homeAssistantFixture, homeAssistantRuntimeFixture } from "./support/fixtures";
import type { ApiClient } from "../src/api/client";

function motion(enabled: boolean | null = true) {
  return { source: "raptor", persistent: true, supported: true, available: enabled !== null,
    enabled, saved_enabled: false, matches_saved: false };
}

test("Raptor Motion webhook keeps its endpoint write-only and reports bounded delivery status", async () => {
  const calls: Array<[string, unknown?]> = [];
  const client = {
    json: async (route: string) => {
      calls.push([route]);
      if (route === "/api/v1/config/motion-webhook") return {
        saved_enabled: true, enabled: true, url: null, url_set: true, live_url_set: true,
        matches_saved: true, transport_available: true,
      };
      return { running: true, enabled: true, transport_available: true, queue_depth: 0,
        queue_dropped: 2, requests: 4, successes: 3, failures: 1,
        last_result: "http_error", last_http_status: 503, last_event_sequence: 9 };
    },
    postJson: async (route: string, body: unknown) => {
      calls.push([route, body]);
      return { status: "applied", persistent: true };
    },
  } as unknown as ApiClient;
  const loaded = await raptorMotionWebhook.load!(client);
  assert.equal(loaded.url, null);
  assert.match(String(loaded.delivery_status), /HTTP status 503.*dropped 2/);
  assert.deepEqual(buildMotionWebhookUpdate({ ...loaded, enabled: false, url: "" }), { enabled: false });
  assert.deepEqual(buildMotionWebhookUpdate({ ...loaded, enabled: true, url: "https://example.test/hook" }), {
    enabled: true, url: "https://example.test/hook",
  });
  const unchangedEnabled = buildMotionWebhookUpdate({ ...loaded, enabled: true, url: "" });
  assert.deepEqual(unchangedEnabled, { enabled: true });
  assert.equal(raptorMotionWebhook.validate!(unchangedEnabled, loaded), undefined);
  const update = buildMotionWebhookUpdate({ ...loaded, enabled: false, url: "" });
  await raptorMotionWebhook.save!(client, update, loaded);
  assert.deepEqual(calls.at(-1), ["/api/v1/config/motion-webhook", { enabled: false }]);
});

test("Raptor Motion ntfy keeps endpoint and token write-only with explicit token clearing", async () => {
  const calls: Array<[string, unknown?]> = [];
  const client = {
    json: async (route: string) => {
      calls.push([route]);
      if (route === "/api/v1/config/motion-ntfy") return {
        saved_enabled: true, enabled: true, url: null, url_set: true, live_url_set: true,
        token: null, token_set: true, live_token_set: true, matches_saved: true, transport_available: true,
      };
      return { running: true, enabled: true, transport_available: true, queue_capacity: 2,
        queue_depth: 0, queue_dropped: 1, requests: 2, successes: 2, failures: 0,
        last_result: "success", last_http_status: 200 };
    },
    postJson: async (route: string, body: unknown) => {
      calls.push([route, body]);
      return { status: "applied", persistent: true };
    },
  } as unknown as ApiClient;
  const loaded = await raptorMotionNtfy.load!(client);
  assert.equal(loaded.url, null);
  assert.equal(loaded.token, null);
  assert.match(String(loaded.delivery_status), /success.*dropped 1/);
  assert.equal(raptorMotionNtfy.validate!({ ...loaded, enabled: true, url: "", token: "replacement", clear_token: false }, loaded), undefined);
  assert.deepEqual(buildMotionNtfyUpdate({ ...loaded, enabled: false, url: "", token: "", clear_token: false }), { enabled: false });
  const unchangedEnabled = buildMotionNtfyUpdate({ ...loaded, enabled: true, url: "", token: "", clear_token: false });
  assert.deepEqual(unchangedEnabled, { enabled: true });
  assert.equal(raptorMotionNtfy.validate!(unchangedEnabled, loaded), undefined);
  const update = buildMotionNtfyUpdate({ ...loaded, enabled: true, url: "", token: "", clear_token: true });
  assert.deepEqual(update, { enabled: true, clear_token: true });
  await raptorMotionNtfy.save!(client, update, loaded);
  assert.deepEqual(calls.at(-1), ["/api/v1/config/motion-ntfy", { enabled: true, clear_token: true }]);
});

test("Raptor Motion Gotify keeps endpoint and token write-only", async () => {
  const client = { json: async (route: string) => route.includes("config") ? {
    saved_enabled: false, enabled: false, endpoint: null, endpoint_set: true, live_endpoint_set: true,
    token: null, token_set: true, live_token_set: true, matches_saved: true, transport_available: true,
  } : { running: true, enabled: false, transport_available: true, queue_capacity: 2, queue_depth: 0,
    queue_dropped: 0, requests: 0, successes: 0, failures: 0, last_result: "idle", last_http_status: null },
    postJson: async () => ({ status: "applied", persistent: true }) } as unknown as ApiClient;
  const loaded = await raptorMotionGotify.load!(client);
  assert.equal(loaded.endpoint, null);
  assert.equal(loaded.token, null);
  assert.deepEqual(buildMotionGotifyUpdate({ ...loaded, enabled: true, endpoint: "", token: "", clear_endpoint: false, clear_token: false }), { enabled: true });
  assert.equal(raptorMotionGotify.validate!({ ...loaded, enabled: true, endpoint: "http://gotify.local/message", token: "", clear_token: false }, loaded), "Gotify tokens require an HTTPS endpoint.");
});

test("Raptor Motion Email preserves a blank password and exposes exact saved and live settings", async () => {
  const client = { json: async (route: string) => route.includes("config") ? {
    saved_enabled: true, enabled: false, host: "smtp.example.test", live_host: "old.example.test",
    port: 587, live_port: 465, tls_mode: "starttls", live_tls_mode: "implicit",
    username: "camera", live_username: "camera", password: null, password_set: true,
    live_password_set: true, from_address: "camera@example.test", live_from_address: "camera@example.test",
    to_address: "owner@example.test", live_to_address: "old@example.test", matches_saved: false,
    transport_available: true,
  } : { running: true, enabled: false, transport_available: true, queue_capacity: 2, queue_depth: 0,
    queue_dropped: 0, requests: 1, successes: 0, failures: 1, last_result: "smtp_error", last_smtp_status: 550 },
    postJson: async () => ({ status: "applied", persistent: true }) } as unknown as ApiClient;
  const loaded = await raptorMotionEmail.load!(client);
  assert.equal(loaded.enabled, true);
  assert.equal(loaded.password, null);
  assert.equal(loaded.matches_saved, false);
  assert.match(String(loaded.delivery_status), /SMTP status 550/);
  assert.deepEqual(buildMotionEmailUpdate({ ...loaded, password: "", clear_password: false }), {
    enabled: true, host: "smtp.example.test", port: 587, tls_mode: "starttls", username: "camera",
    from_address: "camera@example.test", to_address: "owner@example.test",
  });
  assert.deepEqual(buildMotionEmailUpdate({ ...loaded, password: "", clear_password: true }), {
    enabled: true, host: "smtp.example.test", port: 587, tls_mode: "starttls", username: "camera",
    from_address: "camera@example.test", to_address: "owner@example.test", clear_password: true,
  });
});

test("Raptor Motion FTP preserves a blank password and exposes bounded transfer status", async () => {
  const client = { json: async (route: string) => route.includes("config") ? {
    saved_enabled: true, enabled: false, host: "ftp.example.test", live_host: "old.example.test",
    port: 21, live_port: 21, tls_mode: "explicit", live_tls_mode: "explicit",
    username: "camera", live_username: "camera", password: null, password_set: true,
    live_password_set: true, path: "motion", live_path: "old", matches_saved: false,
    transport_available: true,
  } : { running: true, enabled: false, transport_available: true, queue_capacity: 2,
    queue_depth: 0, queue_dropped: 0, captures: 1, requests: 1, successes: 0,
    failures: 1, cancellations: 1, last_result: "cancelled", last_ftp_status: null },
    postJson: async () => ({ status: "applied", persistent: true }) } as unknown as ApiClient;
  const loaded = await raptorMotionFtp.load!(client);
  assert.equal(loaded.enabled, true);
  assert.equal(loaded.password, null);
  assert.equal(loaded.matches_saved, false);
  assert.match(String(loaded.delivery_status), /cancellations 1/);
  assert.deepEqual(buildMotionFtpUpdate({ ...loaded, password: "", clear_password: false }), {
    enabled: true, host: "ftp.example.test", port: 21, tls_mode: "explicit",
    username: "camera", path: "motion",
  });
  assert.deepEqual(buildMotionFtpUpdate({ ...loaded, password: "", clear_password: true }), {
    enabled: true, host: "ftp.example.test", port: 21, tls_mode: "explicit",
    username: "camera", path: "motion", clear_password: true,
  });
});

test("Raptor Motion Telegram keeps the official API credentials write-only", async () => {
  const client = { json: async (route: string) => route.includes("config") ? {
    saved_enabled: false, enabled: false, bot_token: null, bot_token_set: true, live_bot_token_set: true,
    chat_id: null, chat_id_set: true, live_chat_id_set: true, matches_saved: true, transport_available: true,
  } : { running: true, enabled: false, transport_available: true, queue_capacity: 2, queue_depth: 0,
    queue_dropped: 0, requests: 0, successes: 0, failures: 0, last_result: "idle", last_http_status: null },
    postJson: async () => ({ status: "applied", persistent: true }) } as unknown as ApiClient;
  const loaded = await raptorMotionTelegram.load!(client);
  assert.equal(loaded.bot_token, null);
  assert.equal(loaded.chat_id, null);
  assert.deepEqual(buildMotionTelegramUpdate({ ...loaded, enabled: false, bot_token: "", chat_id: "", clear_bot_token: true, clear_chat_id: false }), { enabled: false, clear_bot_token: true });
  assert.equal(raptorMotionTelegram.validate!({ ...loaded, enabled: true, bot_token: "", chat_id: "" }, loaded), undefined);
});

test("Raptor motion loads without privacy configuration and saves an unchanged explicit choice", async () => {
  const calls: string[] = [];
  const client = {
    json: async (route: string) => { calls.push(route); return motion(); },
    postJson: async (route: string, body: unknown) => {
      calls.push(route);
      assert.deepEqual(body, { motion: { enabled: true } });
      return { status: "accepted", persistent: true };
    },
  } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_saved_enabled, false);
  assert.equal(loaded.motion_matches_saved, false);
  const body = buildMotionPrivacyUpdate(loaded, loaded);
  await motionPrivacy.save!(client, body, loaded);
  assert.deepEqual(calls, ["/api/v1/prudynt/motion", "/api/v1/prudynt"]);
});

test("Raptor reports the Home Assistant-owned Motion MQTT destination without duplicating its configuration", async () => {
  const client = {
    json: async (route: string) => {
      if (route === "/api/v1/prudynt/motion") return motion();
      if (route === "/api/v1/config/ha") return { ...homeAssistantFixture, enabled: true, enable_motion: true };
      if (route === "/api/v1/runtime/ha") return homeAssistantRuntimeFixture;
      throw new Error(`unexpected route ${route}`);
    },
  } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_mqtt_enabled, null);
  assert.match(String(loaded.motion_mqtt_status), /Checking/);
  const mqtt = await loadRaptorMotionMqttStatus(client);
  assert.equal(mqtt.enabled, true);
  assert.match(mqtt.status, /connected.*retained ON\/OFF/);
  assert.match(String(loaded.motion_destinations_note), /FTP uploads one bounded stream 1 JPEG/);
  assert.match(String(loaded.motion_destinations_note), /Each destination has separate settings/);
  assert.equal(motionPrivacy.fields.some((field) => field.path === "motion_mqtt_enabled" && field.readOnly === true), true);
  assert.equal(motionPrivacy.fields.some((field) => field.path === "motion.send2mqtt" && field.visibleWhen?.equals === false), false);
});

test("Home Assistant status failures remain separate from Motion loading", async () => {
  const client = {
    json: async (route: string) => {
      if (route === "/api/v1/prudynt/motion") return motion();
      throw new Error("HA unavailable");
    },
  } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_mqtt_enabled, null);
  assert.match(String(loaded.motion_mqtt_status), /Checking/);
  assert.deepEqual(await loadRaptorMotionMqttStatus(client), {
    enabled: null, status: "Home Assistant settings could not be read. Motion MQTT state is unknown.",
  });
});

test("Motion MQTT status distinguishes disabled publication and disconnected runtime", async () => {
  const response = async (config: object, runtime: object = homeAssistantRuntimeFixture) =>
    loadRaptorMotionMqttStatus({ json: async (route: string) => route === "/api/v1/config/ha" ? config : runtime } as unknown as ApiClient);
  assert.deepEqual(await response({ ...homeAssistantFixture, enabled: false }), {
    enabled: false, status: "Home Assistant is disabled. No Motion MQTT events are published.",
  });
  assert.deepEqual(await response({ ...homeAssistantFixture, enabled: true, enable_motion: false }), {
    enabled: false, status: "Home Assistant is enabled, but Motion publishing is disabled.",
  });
  const runtimeUnavailable = await loadRaptorMotionMqttStatus({
    json: async (route: string) => {
      if (route === "/api/v1/config/ha") return { ...homeAssistantFixture, enabled: true, enable_motion: true };
      throw new Error("runtime unavailable");
    },
  } as unknown as ApiClient);
  assert.deepEqual(runtimeUnavailable, {
    enabled: true, status: "Motion MQTT publishing is enabled. Broker status could not be read.",
  });
  const disconnected = await response(
    { ...homeAssistantFixture, enabled: true, enable_motion: true },
    { ...homeAssistantRuntimeFixture, connected: false, state: "backoff", last_error: "connect failed" },
  );
  assert.equal(disconnected.enabled, true);
  assert.match(disconnected.status, /worker is backoff.*connect failed/);
});

test("unknown motion needs an explicit choice and rejects unconfirmed persistence", async () => {
  const client = { json: async () => motion(null), postJson: async () => ({ status: "accepted" }) } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.throws(() => buildMotionPrivacyUpdate(loaded, loaded), /explicit motion setting/);
  const body = buildMotionPrivacyUpdate({ motion: { enabled: false } }, loaded);
  assert.deepEqual(body, { motion: { enabled: false } });
  await assert.rejects(motionPrivacy.save!(client, body, loaded), /saving was not confirmed/);
});

test("Prudynt motion payload does not include page metadata", () => {
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: true }, privacy: { enabled: false },
    motion_full_controls: true, motion_saved_enabled: false }), {
    motion: { enabled: true }, privacy: { enabled: false, stream0_enabled: false, stream1_enabled: false },
  });
});


test("Raptor sensitivity uses its own range and is omitted while disabling", async () => {
  const client = { json: async () => ({ ...motion(), sensitivity: 3, sensitivity_available: true,
    saved_sensitivity: 2, sensitivity_matches_saved: false }) } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_sensitivity, 3);
  assert.equal(loaded.motion_saved_sensitivity, 2);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion_sensitivity: 0 }, loaded), { motion: { enabled: true, sensitivity: 0 } });
  assert.throws(() => buildMotionPrivacyUpdate({ ...loaded, motion_sensitivity: 8 }, loaded), /0 to 4/);
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: false }, motion_sensitivity: 8 }, loaded), { motion: { enabled: false } });
});

test("Raptor skip count uses checked native capability and is omitted while disabling", async () => {
  const client = { json: async () => ({ ...motion(), skip_frame_count: 5, skip_frame_count_available: true,
    saved_skip_frame_count: 3, skip_frame_count_matches_saved: false }) } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_skip_frame_count, 5);
  assert.equal(loaded.motion_saved_skip_frame_count, 3);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion_skip_frame_count: 12 }, loaded),
    { motion: { enabled: true, skip_frame_count: 12 } });
  assert.throws(() => buildMotionPrivacyUpdate({ ...loaded, motion_skip_frame_count: 65_536 }, loaded), /0 to 65535/);
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: false }, motion_skip_frame_count: 12 }, loaded),
    { motion: { enabled: false } });
  await assert.rejects(async () => motionPrivacy.load!({ json: async () => ({ ...motion(), skip_frame_count: 65_536,
    skip_frame_count_available: true, saved_skip_frame_count: 3, skip_frame_count_matches_saved: false }) } as unknown as ApiClient));
});

test("Raptor lifecycle timing stays complete and preserves sample debounce semantics", async () => {
  const client = { json: async () => ({ ...motion(), lifecycle_available: true, lifecycle_matches_saved: true,
    debounce_time: 2, cooldown_time: 7, init_time: 3, min_time: 4, post_time: 6 }) } as unknown as ApiClient;
  const loaded = await motionPrivacy.load!(client);
  assert.equal(loaded.motion_debounce_time, 2);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion_debounce_time: 3 }, loaded), {
    motion: { enabled: true, debounce_time: 3, cooldown_time: 7, init_time: 3, min_time: 4, post_time: 6 },
  });
  assert.throws(() => buildMotionPrivacyUpdate({ ...loaded, motion_cooldown_time: 61 }, loaded), /complete bounded/);
  assert.equal(motionPrivacy.fields.some((field) => field.path === "motion_debounce_time" && field.label === "Debounce samples"), true);
});

test("Raptor event actions keep the complete checked settings and expose worker outcomes", async () => {
  const actions = { event_actions_available: true, video_length: 15, send2storage: true, playonspeaker: true, speaker_repeats: 2,
    saved_video_length: 10, saved_send2storage: false, saved_playonspeaker: false, saved_speaker_repeats: 1, event_actions_match_saved: false,
    speaker_action_available: true, speaker_playback: "completed",
    storage_action_available: true, event_action_worker_running: true, event_storage_owned_channel: 1,
    event_storage_starts: 3, event_storage_stops: 2, event_storage_failures: 1, event_action_queue_dropped: 0,
    event_speaker_requests: 4, event_speaker_failures: 1, event_speaker_rate_limited: 2 };
  const loaded = await motionPrivacy.load!({ json: async () => ({ ...motion(), ...actions }) } as unknown as ApiClient);
  assert.equal(loaded.motion_video_length, 15);
  assert.equal(loaded.motion_send2storage, true);
  assert.equal(loaded.motion_playonspeaker, true);
  assert.equal(loaded.motion_speaker_repeats, 2);
  assert.match(String(loaded.motion_event_actions_note), /owned channel 1; starts 3, stops 2, failures 1/);
  assert.match(String(loaded.motion_event_actions_note), /Speaker action control is reachable; last playback state completed/);
  assert.doesNotMatch(String(loaded.motion_event_actions_note), /clip is available/);
  assert.match(motionPrivacy.fields.find((field) => field.path === "motion_playonspeaker")!.description!, /mute and Privacy off/);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion_video_length: 20, motion_send2storage: false, motion_playonspeaker: true, motion_speaker_repeats: 3 }, loaded),
    { motion: { enabled: true, video_length: 20, send2storage: false, playonspeaker: true, speaker_repeats: 3 } });
  assert.throws(() => buildMotionPrivacyUpdate({ ...loaded, motion_video_length: 0 }, loaded), /complete bounded event actions/);
  await assert.rejects(async () => motionPrivacy.load!({ json: async () => ({ ...motion(), ...actions,
    event_actions_match_saved: true }) } as unknown as ApiClient), /event action observation/);
  await assert.rejects(async () => motionPrivacy.load!({ json: async () => ({ ...motion(), ...actions,
    event_storage_owned_channel: 2 }) } as unknown as ApiClient), /event action observation/);
});

test("Raptor shows fixed detection status and hides unsupported legacy controls", async () => {
  const loaded = await motionPrivacy.load!({ json: async () => ({ ...motion(), roi: region() }) } as unknown as ApiClient);
  assert.equal(loaded.motion_monitor_stream, 1);
  for (const path of ["motion_monitor_stream", "motion_frame_width", "motion_frame_height"]) {
    assert.equal(motionPrivacy.fields.some((field) => field.path === path && field.readOnly === true && field.visibleWhen?.equals === false), true);
  }
  for (const path of ["motion.cooldown_time", "motion.video_length", "motion.send2email", "motion.send2webhook"]) {
    assert.equal(motionPrivacy.fields.some((field) => field.path === path && field.visibleWhen?.equals === true), true);
  }
});

test("known lifecycle settings remain visible but not writable while observations are unavailable", async () => {
  const response = { ...motion(), lifecycle_available: false, lifecycle_matches_saved: true,
    debounce_time: 2, cooldown_time: 7, init_time: 3, min_time: 4, post_time: 6 };
  const loaded = await motionPrivacy.load!({ json: async () => response } as unknown as ApiClient);
  assert.equal(loaded.motion_lifecycle_control, false);
  assert.equal(loaded.motion_debounce_time, 2);
  assert.equal(loaded.motion_cooldown_time, 7);
  assert.equal(loaded.motion_lifecycle_matches_saved, true);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion_debounce_time: 9 }, loaded),
    { motion: { enabled: true } });
});

test("unavailable lifecycle rejects partial or invalid settings without rejecting a complete unknown state", async () => {
  const known = { ...motion(), lifecycle_available: false, lifecycle_matches_saved: true,
    debounce_time: 2, cooldown_time: 7, init_time: 3, min_time: 4, post_time: 6 };
  const unknown = { ...known, lifecycle_matches_saved: false,
    debounce_time: null, cooldown_time: null, init_time: null, min_time: null, post_time: null };
  const loaded = await motionPrivacy.load!({ json: async () => unknown } as unknown as ApiClient);
  assert.equal(loaded.motion_lifecycle_control, false);
  assert.equal(loaded.motion_debounce_time, null);
  for (const response of [
    { ...known, debounce_time: null }, { ...known, cooldown_time: 61 },
    { ...known, min_time: -1 }, { ...known, post_time: 0.5 },
    { ...unknown, lifecycle_matches_saved: true }, { ...unknown, lifecycle_available: true },
  ]) {
    await assert.rejects(motionPrivacy.load!({ json: async () => response } as unknown as ApiClient),
      /Motion lifecycle observation is incomplete/);
  }
});


const rectangle = { roi_0_x: 64, roi_0_y: 32, roi_1_x: 320, roi_1_y: 240 };
function region(count = 1) {
  return { supported: true, available: true, applied: true, frame_width: 640, frame_height: 360,
    count, single: count === 1 ? rectangle : null, saved_single: null, matches_saved: false };
}

test("Raptor multi-region load requires all four edges before replacing it with one rectangle", async () => {
  const loaded = await motionPrivacy.load!({ json: async () => ({ ...motion(), roi: region(16) }) } as unknown as ApiClient);
  assert.equal(loaded.motion_roi_control, true);
  assert.equal(loaded.motion_roi_count, 16);
  assert.equal((loaded.motion as Record<string, unknown>).roi_0_x, null);
  assert.deepEqual(buildMotionPrivacyUpdate(loaded, loaded), { motion: { enabled: true } });
  assert.throws(() => buildMotionPrivacyUpdate({ ...loaded, motion: { enabled: true, roi_0_x: 64 } }, loaded), /all four/);
  assert.deepEqual(buildMotionPrivacyUpdate({ ...loaded, motion: { enabled: true, ...rectangle } }, loaded),
    { motion: { enabled: true, ...rectangle, roi_count: 1 } });
});

test("Raptor single-region retry stays explicit and disabled motion omits ROI", async () => {
  const loaded = await motionPrivacy.load!({ json: async () => ({ ...motion(), roi: region() }) } as unknown as ApiClient);
  assert.deepEqual(buildMotionPrivacyUpdate(loaded, loaded), { motion: { enabled: true, ...rectangle, roi_count: 1 } });
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: false, ...rectangle } }, loaded), { motion: { enabled: false } });
  assert.throws(() => buildMotionPrivacyUpdate({ motion: { enabled: true, ...rectangle, roi_1_x: 641 } }, loaded), /fit inside 640/);
  assert.throws(() => buildMotionPrivacyUpdate({ motion: { enabled: true, ...rectangle, roi_1_x: 65 } }, loaded), /at least 2/);
});

test("unconfirmed paused ROI remains editable for an explicit on-and-save retry", async () => {
  const loaded = await motionPrivacy.load!({ json: async () => ({ ...motion(false), matches_saved: true,
    roi: { ...region(), applied: false } }) } as unknown as ApiClient);
  assert.equal(loaded.motion_roi_control, true);
  assert.equal(loaded.motion_roi_matches_saved, false);
  assert.match(String(loaded.motion_roi_note), /unconfirmed/);
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: true, ...rectangle } }, loaded),
    { motion: { enabled: true, ...rectangle, roi_count: 1 } });
});

test("ROI capability rejects inconsistent counts, flags, geometry and saved comparisons", () => {
  assert.equal(decodeRaptorMotionRoi(undefined), null);
  assert.equal(decodeRaptorMotionRoi(null), null);
  for (const roi of [
    { ...region(), count: 52 }, { ...region(), count: 2 }, { ...region(), available: false },
    { ...region(), supported: false }, { ...region(), frame_width: null },
    { ...region(), single: { ...rectangle, roi_0_x: 1.5 } },
    { ...region(), single: { ...rectangle, roi_1_y: 361 } },
    { ...region(), saved_single: rectangle, matches_saved: false },
    { ...region(), saved_single: { ...rectangle, extra: 1 } },
  ]) assert.throws(() => decodeRaptorMotionRoi(roi));
  assert.equal(decodeRaptorMotionRoi({ ...region(), saved_single: rectangle, matches_saved: true })?.matches_saved, true);
});

test("missing ROI capability keeps region fields disabled and preserves old Prudynt payloads", async () => {
  const loaded = await motionPrivacy.load!({ json: async () => motion() } as unknown as ApiClient);
  assert.equal(loaded.motion_roi_control, false);
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: true, ...rectangle } }, loaded), { motion: { enabled: true } });
  assert.deepEqual(buildMotionPrivacyUpdate({ motion: { enabled: true, ...rectangle, roi_count: 8 }, privacy: { enabled: false } }),
    { motion: { enabled: true, ...rectangle, roi_count: 8 }, privacy: { enabled: false, stream0_enabled: false, stream1_enabled: false } });
});


test("ROI field conditions follow the unsaved motion choice while retaining capability and Prudynt behavior", () => {
  for (const key of Object.keys(rectangle)) {
    const condition = motionPrivacy.fields.find((field) => field.path === `motion.${key}`)!.enabledWhen;
    assert.equal(typeof condition, "function");
    if (typeof condition !== "function") throw new Error("Expected dynamic ROI condition");
    for (const enabled of [false, true]) {
      const view = { motion_roi_control: true, motion_full_controls: false, "motion.enabled": enabled };
      assert.equal(condition((path) => view[path as keyof typeof view]), enabled);
      assert.equal(condition((path) => path === "motion_roi_control" ? false : view[path as keyof typeof view]), false);
      assert.equal(condition((path) => path === "motion_full_controls" ? true : view[path as keyof typeof view]), true);
    }
  }
  assert.match(motionPrivacy.successMessage!({ motion: { source: "raptor", enabled: false } }), /Region edits were not saved/);
  assert.equal(motionPrivacy.successMessage!({ motion: { source: "raptor", enabled: true } }), "Settings saved.");
  assert.equal(motionPrivacy.successMessage!({ motion: { enabled: false } }), "Settings saved.");
});
