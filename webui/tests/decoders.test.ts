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
  assert.deepEqual(decodeImagingRuntime(imagingRuntimeFixture), imagingRuntimeFixture.message);
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
