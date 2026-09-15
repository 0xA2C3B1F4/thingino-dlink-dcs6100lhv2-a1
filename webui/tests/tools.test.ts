import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeDaynightHistory,
  decodeDaynightSensors,
  decodeCrontab,
  decodeDiagnosticInfo,
  decodeFileList,
  decodeNetworkProbeMetadata,
  decodeNetworkProbeResponse,
  decodeOverlayStatus,
  decodeRuntimeSystem,
  decodeSdStatus,
  decodeSensorIdentity,
} from "../src/api/decode";
import { routes } from "../src/api/routes";
import { formatTimestamp, mediaStatusSummary } from "../src/pages/tools";

test("system status uses backend-neutral media availability for both streams", () => {
  const ready = { enabled: true, available: true, snapshot_url: null };
  const unavailable = { ...ready, available: false };
  const disabled = { ...unavailable, enabled: false };
  assert.deepEqual(mediaStatusSummary({ stream0: ready, stream1: ready }), [
    ["Media", "Ready"], ["Stream 0", "Ready"], ["Stream 1", "Ready"],
  ]);
  assert.deepEqual(mediaStatusSummary({ stream0: ready, stream1: disabled }), [
    ["Media", "Ready"], ["Stream 0", "Ready"], ["Stream 1", "Disabled"],
  ]);
  assert.deepEqual(mediaStatusSummary({ stream0: ready, stream1: unavailable }), [
    ["Media", "Partially available"], ["Stream 0", "Ready"], ["Stream 1", "Unavailable"],
  ]);
  assert.equal(mediaStatusSummary({ stream0: unavailable, stream1: unavailable })[0]?.[1], "Unavailable");
  assert.equal(mediaStatusSummary({ stream0: disabled, stream1: disabled })[0]?.[1], "Disabled");
});

const heartbeat = {
  time_now: 1787248800, uptime: 100, daynight_brightness: 42.5, total_gain: 1.5,
  daynight_mode: "day", rec_ch0: false, rec_ch1: true, timelapse_enabled: false,
  motion_enabled: true, motion_active: false, motion_ingress_ready: true, privacy_enabled: false, color_mode: 1, mic_enabled: true,
  spk_enabled: false, daynight_enabled: true, ircut_state: 1, ir850_state: 0,
  ir940_state: null, white_state: null,
};

test("timestamps use a compact local 24-hour representation", () => {
  const local = new Date(2026, 7, 23, 16, 15, 52);
  assert.equal(formatTimestamp(local.getTime() / 1000), "2026-08-23 16:15:52");
  assert.equal(formatTimestamp("0"), "Not reported");
});

test("runtime/system decoder keeps detailed memory, storage and interface state", () => {
  const decoded = decodeRuntimeSystem({
    code: 200,
    result: "success",
    data: {
      memory: { total: 65536, free: 24576, active: 24576, buffers: 4096, cached: 12288 },
      overlay: { total: 8192, used: 4608, free: 3584 },
      extras: { total: 31166976, used: 6590976, free: 24576000 },
      network: { online: true, ip: "192.0.2.54", interfaces: { eth0: { enabled: true, dhcp: false, address: "192.0.2.2", netmask: "255.255.255.0", gateway: "192.0.2.1", broadcast: "192.0.2.255", mac: "02:00:00:00:00:02", link_up: true } } },
      media: { prudynt_running: true, media_ready: true, stream0_enabled: true, stream1_enabled: false },
      timestamp: 1787248800,
    },
  });
  assert.equal(decoded.memory.used, 40960);
  assert.equal(decoded.memory.buffers, 4096);
  assert.equal(decoded.overlay.free, 3584);
  assert.equal(decoded.network.interfaces.eth0?.broadcast, "192.0.2.255");
  assert.equal(decoded.media.stream1_enabled, false);
  assert.throws(() => decodeRuntimeSystem({ code: 200, result: "success", data: {} }), /network/);
});

test("diagnostic decoder accepts the complete fixed allowlist response shape", () => {
  const value = decodeDiagnosticInfo({ commands: [{ command: "cat /etc/os-release", output_base64: "b2sK" }], extras_html_base64: "" });
  assert.equal(value.commands[0]?.command, "cat /etc/os-release");
  assert.throws(() => decodeDiagnosticInfo({ commands: [{ command: "arbitrary", output_base64: 1 }] }), /string/);
});

test("crontab decoder keeps the bounded editable document contract", () => {
  assert.deepEqual(decodeCrontab({ content: "*/5 * * * * /bin/true\n", max_bytes: 16384 }), { content: "*/5 * * * * /bin/true\n", max_bytes: 16384 });
  assert.throws(() => decodeCrontab({ content: "ok", max_bytes: 20000 }), /max_bytes/);
  assert.equal(routes.config.crontab, "/api/v1/config/crontab");
});

test("runtime day/night history is decoded as a direct heartbeat array", () => {
  assert.equal(decodeDaynightHistory([heartbeat])[0]?.daynight_mode, "day");
  assert.deepEqual(decodeDaynightSensors({ night_threshold_pct: 28, day_threshold_pct: 42, current: { brightness: 42.5 } }).current, { brightness: 42.5 });
  assert.throws(() => decodeDaynightHistory({ entries: [heartbeat] }), /array/);
  assert.deepEqual(
    decodeDaynightSensors({ source: "raptor", night_threshold_pct: null, day_threshold_pct: null, thresholds: { trigger: "gain", supported: true, available: true, values: { day_threshold: 25000, night_threshold: 40000 } }, current: null }),
    { source: "raptor", night_threshold_pct: null, day_threshold_pct: null, thresholds: { trigger: "gain", supported: true, available: true, values: { day_threshold: 25000, night_threshold: 40000 } }, current: null },
  );
});

test("file, overlay and sensor decoders preserve bounded tool metadata", () => {
  const files = decodeFileList({ directory: "/mnt/media", parent: "/", breadcrumbs: [{ label: "Home", path: "/" }, { label: "media", path: "/mnt/media" }], entries: [{ name: "clip.mp4", path: "/mnt/media/clip.mp4", size: "1024", perm: "0644", time: "1787248800", is_dir: false, is_link: false, link_target: "", deletable: true }] });
  assert.equal(files.entries[0]?.deletable, true);
  assert.equal(decodeOverlayStatus({ usage: { label: "56%", percent: 56, state: "primary" }, listing_base64: "", path: "/overlay" }).path, "/overlay");
  assert.equal(decodeSensorIdentity({ sensor_model: "os02g10", soc_model: "t31n", soc_family: "t", file_path: "/usr/share/sensor/os02g10-t31n.bin", md5: "not computed in request path" }).soc_family, "t");
});

test("SD decoder keeps presence and formatter capability coherent", () => {
  const base = { ok: true, data: { has_sdcard: false, device: null, reports: { partitions_b64: "", mounts_b64: "" }, format: { supported: false, options: [], status: "idle", last_output_b64: "" }, filesystems: [], messages: { format_warning: "disabled", not_present: "none" }, debug: { detection: "none" } } };
  assert.equal(decodeSdStatus(base).data.format.supported, false);
  assert.throws(() => decodeSdStatus({ ...base, data: { ...base.data, format: { ...base.data.format, supported: true } } }), /disagree/);
  assert.throws(() => decodeSdStatus({ ...base, data: { ...base.data, has_sdcard: true } }), /disagree/);
});

test("tool routes encode media modes and deletion paths", () => {
  assert.equal(routes.files.remove("/mnt/media/clip.mp4"), "/api/v1/files?rm=%2Fmnt%2Fmedia%2Fclip.mp4");
  assert.equal(routes.media.file("/mnt/media/clip.mp4", "download"), "/media/v1/file?path=%2Fmnt%2Fmedia%2Fclip.mp4&download=1");
  assert.equal(routes.media.file("/mnt/media/clip.mp4", "play"), "/media/v1/file?path=%2Fmnt%2Fmedia%2Fclip.mp4&play=1");
});

test("network probe decoder exposes only DNS resolve and TCP connect", () => {
  const metadata = decodeNetworkProbeMetadata({
    actions: [
      { id: "resolve", label: "DNS resolve", description: "Resolve a host." },
      { id: "connect", label: "TCP connect", description: "Connect to a port." },
    ],
    interfaces: ["wlan0"],
    defaults: { action: "resolve", interface: "auto", packet_size: 56, count: 1 },
    limits: { packet_size: { min: 1, max: 65507 }, count: { min: 1, max: 1 } },
  });
  assert.deepEqual(metadata.actions.map((entry) => entry.id), ["resolve", "connect"]);
  assert.equal(decodeNetworkProbeResponse({ command: "connect camera.local:443", success: false, output_b64: "b2s=" }).success, false);
  assert.throws(() => decodeNetworkProbeResponse({ command: "resolve localhost", output_b64: "b2s=" }), /success/);
  assert.throws(() => decodeNetworkProbeMetadata({ ...metadata, actions: [{ id: "ping", label: "Ping", description: "No" }] }), /unsupported/);
});
