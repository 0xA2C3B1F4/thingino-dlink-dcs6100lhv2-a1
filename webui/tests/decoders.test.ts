import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeAdmin, decodeAudio, decodeDayNight, decodeGpio, decodeHomeAssistant, decodeHomeAssistantRuntime, decodeImage, decodeImagingRuntime,
  decodeMotion, decodeNetwork, decodeOsd, decodePrivacy, decodeRecorder,
  decodeRemoteLogging, decodeRuntimeMedia, decodeSendConfig, decodeStream, decodeTime, decodeWebui, decodeWifiScan,
} from "../src/api/decode";
import {
  audioFixture, daynightFixture, gpioFixture, homeAssistantFixture, homeAssistantRuntimeFixture, imageFixture, imagingRuntimeFixture,
  motionFixture, networkFixture, osdFixture, privacyFixture, recorderFixture,
  rsyslogFixture, sendFixture, stream0Fixture, stream1Fixture, timeFixture, webuiFixture,
} from "./support/fixtures";
import { assertNetworkShape, assertRanges, assertSecretSemantics } from "./support/contracts";

test("all P0/P1 domain fixtures pass typed runtime decoders", () => {
  assert.deepEqual(decodeNetwork(networkFixture), networkFixture);
  assert.deepEqual(decodeAudio(audioFixture), audioFixture);
  assert.deepEqual(decodeImage(imageFixture), imageFixture);
  assert.deepEqual(decodeImagingRuntime(imagingRuntimeFixture), {
    ...imagingRuntimeFixture.message, source: "raptor", persistent: true,
  });
  assert.deepEqual(decodeStream(stream0Fixture, "stream0"), stream0Fixture);
  assert.deepEqual(decodeStream(stream1Fixture, "stream1"), stream1Fixture);
  assert.deepEqual(decodeWebui(webuiFixture), webuiFixture);
  assert.deepEqual(decodeRemoteLogging(rsyslogFixture), rsyslogFixture);
  assert.deepEqual(decodeDayNight(daynightFixture), daynightFixture);
  assert.deepEqual(decodeGpio(gpioFixture), gpioFixture);
  assert.deepEqual(decodeHomeAssistant(homeAssistantFixture), homeAssistantFixture);
  assert.deepEqual(decodeHomeAssistantRuntime(homeAssistantRuntimeFixture), homeAssistantRuntimeFixture);
  assert.deepEqual(decodeRecorder(recorderFixture), recorderFixture);
  assert.deepEqual(decodeOsd(osdFixture), osdFixture);
  assert.deepEqual(decodeMotion(motionFixture), motionFixture);
  assert.deepEqual(decodePrivacy(privacyFixture), privacyFixture);
  assert.deepEqual(decodeSendConfig(sendFixture), sendFixture);
});

test("imaging runtime decoder requires complete ranges only for supported controls", () => {
  assert.equal(decodeImagingRuntime(imagingRuntimeFixture).fields.tone?.supported, false);
  const missingRange = structuredClone(imagingRuntimeFixture);
  delete (missingRange.message.fields.brightness as Record<string, unknown>).max;
  assert.throws(() => decodeImagingRuntime(missingRange), /valid range/);
  const invertedRange = structuredClone(imagingRuntimeFixture);
  invertedRange.message.fields.brightness.min = 255;
  assert.throws(() => decodeImagingRuntime(invertedRange), /valid range/);
});

test("imaging runtime decoder rejects the retired non-Raptor envelope", () => {
  const legacy = structuredClone(imagingRuntimeFixture) as Record<string, unknown>;
  delete legacy.source;
  assert.throws(() => decodeImagingRuntime(legacy), /source/);
});

test("imaging runtime decoder keeps white-balance modes and manual gains coherent", () => {
  const value = structuredClone(imagingRuntimeFixture) as Record<string, any>;
  value.source = "raptor";
  value.persistent = true;
  value.message.white_balance = {
    supported: true, available: true, verification: "sdk-readback",
    modes: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], gain_min: 0, gain_max: 1024,
    mode: 2, gains_effective: false, rgain: null, bgain: null,
    configured_mode: 2, configured_rgain: 300, configured_bgain: 400,
    saved_mode: 2, saved_rgain: 300, saved_bgain: 400, matches_saved: true,
  };
  assert.equal(decodeImagingRuntime(value).white_balance?.rgain, null);
  for (const mutate of [
    (copy: Record<string, any>) => { copy.message.white_balance.mode = 10; },
    (copy: Record<string, any>) => { copy.message.white_balance.rgain = 300; },
    (copy: Record<string, any>) => { copy.message.white_balance.configured_bgain = null; },
    (copy: Record<string, any>) => { copy.message.white_balance.matches_saved = false; },
  ]) {
    const malformed = structuredClone(value);
    mutate(malformed);
    assert.throws(() => decodeImagingRuntime(malformed), /white balance/);
  }
  const manual = structuredClone(value);
  Object.assign(manual.message.white_balance, { mode: 1, gains_effective: true, rgain: 300, bgain: 400, configured_mode: 1, saved_mode: 1 });
  assert.equal(decodeImagingRuntime(manual).white_balance?.rgain, 300);
});

test("network fixture keeps every interface address field and fixed D-Link controls", () => {
  assertNetworkShape(networkFixture);
  const malformed = structuredClone(networkFixture);
  delete ((malformed.interfaces as Record<string, unknown>).eth0 as Record<string, unknown>).gateway;
  assert.throws(() => decodeNetwork(malformed), /network/);
});

test("Wi-Fi scan response is decoded as typed entries", () => {
  const scan = { networks: [{ ssid: "Quiet Grid Lab", bssid: "02:00:00:00:00:11", frequency: 2412, signal: -47, security: "WPA2" }] };
  assert.deepEqual(decodeWifiScan(scan), scan);
  assert.throws(() => decodeWifiScan({ networks: [{ ssid: 5 }] }), /ssid/);
});

test("time and admin config decoders validate every displayed field", () => {
  const time = timeFixture;
  const admin = { name: "Camera administrator", email: "", telegram: "", discord: "" };
  assert.deepEqual(decodeTime(time), time);
  assert.deepEqual(decodeAdmin(admin), admin);
  assert.throws(() => decodeTime({ ...time, dhcp_ignore_timezone: "yes" }), /boolean/);
  assert.throws(() => decodeTime({ ...time, timezone_options: [{ label: "missing name" }] }), /name/);
  const missingEpoch = { ...time } as Record<string, unknown>;
  delete missingEpoch.current_unix_time;
  delete missingEpoch.current_unix_ms;
  assert.throws(() => decodeTime(missingEpoch), /current_unix_time|current_unix_ms/);
  assert.throws(() => decodeAdmin({ ...admin, email: 1 }), /string/);
});

test("stream decoder accepts disabled-stream sentinels fps=0 and buffers=-1", () => {
  assert.equal(decodeStream(stream1Fixture, "stream1").fps, 0);
  assert.equal(decodeStream(stream1Fixture, "stream1").buffers, -1);
  assert.throws(() => decodeStream({ ...stream1Fixture, format: "VP9" }, "stream1"), /unsupported/);
  assert.throws(() => decodeStream({ ...stream1Fixture, mode: "unknown" }, "stream1"), /unsupported/);
  assert.throws(() => decodeStream({ ...stream1Fixture, buffers: 0 }, "stream1"), /buffers/);
  assert.throws(() => decodeStream({ ...stream1Fixture, gop: 0 }, "stream1"), /gop/);
  assert.throws(() => decodeStream({ ...stream1Fixture, bitrate: 100_000_001 }, "stream1"), /bitrate/);
  assert.throws(() => decodeStream({ ...stream1Fixture, profile: 3 }, "stream1"), /profile/);
});

test("motion decoder enforces every active bounded field", () => {
  assert.deepEqual(decodeMotion(motionFixture), motionFixture);
  assert.throws(() => decodeMotion({ ...motionFixture, sensitivity: 9 }), /sensitivity/);
  assert.throws(() => decodeMotion({ ...motionFixture, ivs_polling_timeout: 99 }), /ivs_polling_timeout/);
  assert.throws(() => decodeMotion({ ...motionFixture, roi_count: 53 }), /roi_count/);
  assert.throws(() => decodeMotion({ ...motionFixture, playonspeaker: "yes" }), /playonspeaker/);
});

test("runtime media decoder keeps availability, enabled state, and nullable snapshots typed", () => {
  const media = {
    streams: {
      ch0: { available: true, enabled: true, snapshot_url: "/api/v1/actions/snapshot?stream_id=0" },
      ch1: { available: false, enabled: false, snapshot_url: null },
    },
  };
  assert.deepEqual(decodeRuntimeMedia(media), media);
  assert.throws(() => decodeRuntimeMedia({ ...media, streams: { ...media.streams, ch1: { ...media.streams.ch1, snapshot_url: 5 } } }), /snapshot_url/);
  assert.throws(() => decodeRuntimeMedia({ streams: { ch0: media.streams.ch0, ch1: { snapshot_url: null } } }), /available/);
});

test("fixed profile controls are rejected when a response advertises unsupported values", () => {
  assert.throws(() => decodeNetwork({ ...networkFixture, wifi_ap: { enabled: true } }), /D-Link|fixed|wifi_ap|invalid/i);
  const ipv6 = { ...networkFixture, interfaces: { ...networkFixture.interfaces, eth0: { ...networkFixture.interfaces.eth0, ipv6: true } } };
  assert.throws(() => decodeNetwork(ipv6), /ipv6|fixed/i);
});

test("redacted send and service fixture secrets are null with *_set metadata", () => {
  assertSecretSemantics(sendFixture);
  assertSecretSemantics(homeAssistantFixture);
  assertSecretSemantics(networkFixture);
});

test("service and OSD decoders enforce backend-aligned numeric and color bounds", () => {
  assert.throws(() => decodeRemoteLogging({ ...rsyslogFixture, port: 0 }), /port/);
  assert.throws(() => decodeHomeAssistant({ ...homeAssistantFixture, mqtt: { ...homeAssistantFixture.mqtt, port: 65_536 } }), /port/);
  assert.throws(() => decodeHomeAssistant({ ...homeAssistantFixture, state_interval: 604_801 }), /state_interval/);
  assert.throws(() => decodeHomeAssistant({ ...homeAssistantFixture, ota_supported: true }), /unsupported/);
  assert.throws(() => decodeHomeAssistantRuntime({ ...homeAssistantRuntimeFixture, queue_depth: 65 }), /queue/);
  assert.throws(() => decodeOsd({ ...osdFixture, burnin: { ...osdFixture.burnin, fill_color: "#ffffff" } }), /RRGGBBAA/);
  const missingFormat = structuredClone(osdFixture);
  delete (missingFormat.burnin as Record<string, unknown>).format;
  assert.equal(decodeOsd(missingFormat).burnin.format, undefined);
  assert.throws(() => decodeSendConfig({ ...sendFixture, ftp: { ...sendFixture.ftp, port: 0 } }), /port/);
  assert.throws(() => decodeSendConfig({ ...sendFixture, gotify: { ...sendFixture.gotify, priority: 11 } }), /priority/);
  assert.deepEqual(decodeSendConfig({ ...sendFixture, gotify: { ...sendFixture.gotify, extras: "{\"client::display\":{\"contentType\":\"text/markdown\"}}" } }).gotify, { ...sendFixture.gotify, extras: "{\"client::display\":{\"contentType\":\"text/markdown\"}}" });
  assert.throws(() => decodeSendConfig({ ...sendFixture, gotify: { ...sendFixture.gotify, extras: {} } }), /JSON-encoded string/);
  assert.throws(() => decodeSendConfig({ ...sendFixture, gotify: { ...sendFixture.gotify, extras: "not-json" } }), /valid JSON/);
  assert.throws(() => decodeSendConfig({ ...sendFixture, gotify: { ...sendFixture.gotify, extras: "[]" } }), /JSON.*object/);
});

test("enum contract stays explicit and excludes unsupported D-Link outputs", () => {
  assertRanges();
  assert.equal(Object.hasOwn(gpioFixture, "ir940"), false);
  assert.equal(Object.hasOwn(gpioFixture, "white"), false);
  assert.throws(() => decodeGpio({ ...gpioFixture, gpio: { ...gpioFixture.gpio, ir850: 62 } }), /assignments/);
  assert.throws(() => decodeGpio({ ...gpioFixture, gpio: { ...gpioFixture.gpio, ir940: 62 } }), /assignments/);
  assert.throws(() => decodeGpio({ ...gpioFixture, led: { startup_indicator: "blue" } }), /startup indicator/);
  assert.equal(Object.hasOwn((daynightFixture.controls as Record<string, unknown>), "ir940"), false);
  assert.equal(Object.hasOwn((daynightFixture.controls as Record<string, unknown>), "white"), false);
});

test("Raptor audio rejects missing or invented readback values", () => {
  const names = ["mic_vol", "mic_gain", "spk_vol", "spk_gain"];
  const fields = Object.fromEntries(names.map((name) => [name, { supported: true, available: true, value: 20, min: name.endsWith("gain") ? 0 : -30, max: name.endsWith("gain") ? 31 : 120 }]));
  fields.mic_alc_gain = { supported: true, available: true, value: 2, min: 0, max: 7 };
  const value = { source: "raptor", mic_enabled: true, spk_enabled: true, mic_muted: false,
    mic_format: "PCM", mic_sample_rate: 16000, input_readback: "owner", processing_readback: "owner",
    codecs_built: { PCM: true, G711A: true, G711U: true, AAC: false, OPUS: false }, effects_built: false,
    processing_available: false, mic_noise_suppression: null, mic_agc_enabled: null, mic_high_pass_filter: null,
    mic_agc_target_level_dbfs: null, mic_agc_compression_gain_db: null, mic_alc_gain: 2,
    mic_is_digital: false, mic_input_basis: "dlink-a1-profile-amic",
    force_stereo: false, channel_basis: "rad-fixed-mono",
    buffer_warn_frames: null, buffer_cap_frames: null, buffer_control: "unsupported-prudynt-queue-policy",
    tap_enabled: null, tap_path: null, tap_control: "unsupported",
    levels: fields, mic_vol: 20, mic_gain: 20, spk_vol: 20, spk_gain: 20 };
  assert.deepEqual(decodeAudio(value), value);
  const unavailable = structuredClone(value);
  unavailable.levels.mic_vol!.available = false;
  assert.throws(() => decodeAudio(unavailable), /must be null/);
  const missing = structuredClone(value);
  delete missing.levels.mic_vol;
  assert.throws(() => decodeAudio(missing), /must be an object/);
  assert.throws(() => decodeAudio({ ...value, mic_vol: 21 }), /disagrees/);
  assert.throws(() => decodeAudio({ ...value, mic_is_digital: true }), /topology/);
  assert.throws(() => decodeAudio({ ...value, buffer_cap_frames: 100 }), /queue controls/);
  assert.throws(() => decodeAudio({ ...value, tap_enabled: false }), /tap controls/);
});
