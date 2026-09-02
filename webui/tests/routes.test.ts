import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { routes } from "../src/api/routes";
import { previewStreamUsable } from "../src/pages/preview";

test("canonical paths are centralized and encode untrusted parameters", () => {
  assert.equal(routes.actions.snapshot(1), "/api/v1/actions/snapshot?stream_id=1");
  assert.equal(routes.actions.homeAssistant, "/api/v1/actions/ha");
  assert.equal(routes.actions.reboot, "/api/v1/actions/reboot");
  assert.equal(routes.actions.syncTime, "/api/v1/actions/time/sync");
  assert.equal(routes.config.crontab, "/api/v1/config/crontab");
  assert.equal(routes.runtime.homeAssistant, "/api/v1/runtime/ha");
  assert.equal(routes.runtime.mediaMetrics, "/api/v1/runtime/media/metrics");
  assert.equal(routes.runtime.sensor, "/api/v1/runtime/sensor");
  assert.equal(routes.files.list("/mnt/media/a b"), "/api/v1/files?cd=%2Fmnt%2Fmedia%2Fa%20b");
  assert.equal(routes.media.mjpeg(0), "/media/v1/mjpeg?stream=0");
  assert.equal(routes.media.preview(0), "/media/v1/mjpeg?stream=0");
  assert.equal(routes.media.preview(1), "/media/v1/mjpeg?stream=1");
  assert.equal(routes.media.whip(0), "/api/v1/media/webrtc/whip?stream=0");
  assert.equal(routes.media.whip(1), "/api/v1/media/webrtc/whip?stream=1");
  assert.equal(routes.runtime.motion, "/api/v1/runtime/motion");
});

test("sensor compatibility alias stays out of frontend routes", async () => {
  const source = await readFile("src/api/routes.ts", "utf8");
  assert.doesNotMatch(source, /\/api\/v1\/sensor\/iq/);
});

test("MJPEG preview uses the fixed device profile and forcibly disconnects the old response", async () => {
  const source = await readFile("src/api/media.ts", "utf8");
  assert.match(source, /routes\.media\.preview\(stream\)/);
  assert.match(source, /&q=/);
  assert.match(source, /uhttpd validates q but deliberately discards it/);
  assert.match(source, /this\.image\.src = "data:,"/);
  assert.doesNotMatch(source, /request=/);
});

test("initial preview data does not burst the bounded Control worker pool", async () => {
  const control = await readFile("src/api/control.ts", "utf8");
  const preview = await readFile("src/pages/preview.ts", "utf8");
  assert.doesNotMatch(control, /media\(\): Promise<RuntimeMedia> \{\s*return Promise\.all/);
  assert.doesNotMatch(preview, /Promise\.all\(\[api\.heartbeat\(\), api\.system\(\), api\.media\(\)\]\)/);
});

test("enabled main stream can probe WHIP while unavailable substream remains gated", () => {
  const media = {
    stream0: { available: false, enabled: true, snapshot_url: null },
    stream1: { available: false, enabled: true, snapshot_url: null },
  } as Parameters<typeof previewStreamUsable>[1];
  assert.equal(previewStreamUsable(0, media), true);
  assert.equal(previewStreamUsable(1, media), false);
  assert.equal(previewStreamUsable(0, { ...media, stream0: { available: true, enabled: false, snapshot_url: null } }), false);
});

test("Preview and Streamer prefer WHIP for both video streams with MJPEG fallback", async () => {
  const preview = await readFile("src/pages/preview.ts", "utf8");
  const streamer = await readFile("src/app/streamer-preview.ts", "utf8");
  assert.match(preview, /whipPreview\.start\(selected,/);
  assert.doesNotMatch(preview, /selected !== 0/);
  assert.match(streamer, /new WhipPreview\(this\.video\)/);
  assert.match(streamer, /whipPreview\.start\(this\.stream,/);
  assert.match(streamer, /state === "error"\) startMjpeg\(\)/);
});

test("Preview does not present the internal WHIP endpoint as a player URL", async () => {
  const preview = await readFile("src/pages/preview.ts", "utf8");
  assert.match(preview, /Player endpoints/);
  assert.match(preview, /WHIP signaling endpoint is not a player URL/);
  assert.doesNotMatch(preview, /label: "WebRTC", value: rwdWhipEndpoint/);
});

test("preview exposes the canonical day, auto, and night Control action", async () => {
  const preview = await readFile("src/pages/preview.ts", "utf8");
  assert.match(preview, /\[\["auto", "Auto"\], \["day", "Day"\], \["night", "Night"\]\]/);
  assert.match(preview, /setLiveControl\(\{ kind: "daynight", mode \}\)/);
  assert.match(preview, /label: "Color mode"[\s\S]*kind: "color"/);
  assert.match(preview, /value === 0 \? true : value === 1 \? false/);
});

test("Preview keeps a compact header and aligns live controls beside its content", async () => {
  const preview = await readFile("src/pages/preview.ts", "utf8");
  const shell = await readFile("src/app/shell.ts", "utf8");
  const styles = await readFile("src/styles.css", "utf8");
  assert.match(preview, /page-heading page-heading-with-actions/);
  assert.match(preview, /element\("h1", \{ text: "Preview" \}\)/);
  assert.doesNotMatch(preview, /A clear view, with quiet controls|element\("span", \{ className: "eyebrow", text: "Camera" \}\)/);
  assert.match(preview, /preview-state-line/);
  assert.match(preview, /content\.append\(message, hero, previewStateLine, statusGrid, liveCard\)/);
  assert.match(preview, /section\.append\(controlCard, content\)/);
  assert.match(preview, /page-heading-actions/);
  assert.match(preview, /className: "control-row daynight-row"/);
  assert.match(preview, /"aria-describedby": dayNightDetailId/);
  assert.match(preview, /toggle\.setAttribute\("aria-describedby", detailId\)/);
  assert.match(styles, /--section-rail-width:\s*18rem/);
  assert.match(styles, /\.preview-page \.control-row:hover small,[\s\S]*visibility:\s*visible/);
  assert.match(styles, /\.preview-page \.daynight-row\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(styles, /@media \(max-width: 600px\)[\s\S]*\.preview-page \.control-row small\s*\{[\s\S]*position:\s*static/);
  assert.doesNotMatch(shell, /subnav|subnav-link/);
  assert.match(shell, /sectionLayout\.classList\.toggle\("has-section-navigation", Boolean\(groups\)\)/);
});

test("closed mobile navigation cannot be displayed by author styles", async () => {
  const styles = await readFile("src/styles.css", "utf8");
  assert.match(styles, /\.mobile-drawer:not\(\[open\]\)\s*\{\s*display:\s*none/);
});

test("masthead omits the single D-Link administrator name without changing session state", async () => {
  const shell = await readFile("src/app/shell.ts", "utf8");
  assert.doesNotMatch(shell, /session-user|user\.textContent|session\.username \?\?/);
  assert.match(shell, /online\.textContent = session\.authenticated \? "Online" : "Signed out"/);
  assert.match(shell, /online\.title = `Client IP: \$\{session\.client_ip\}`/);
  assert.match(shell, /passwordWarning\.hidden = !session\.is_default_password/);
});

test("semantic hidden state wins over navigation layout display rules", async () => {
  const styles = await readFile("src/styles.css", "utf8");
  assert.match(styles, /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
  assert.match(styles, /\.page-heading > \.eyebrow\s*\{\s*display:\s*none/);
  assert.match(styles, /--text-page-title:\s*1\.5rem/);
  assert.match(styles, /\.page-heading h1[\s\S]*font-size:\s*var\(--text-page-title\)/);
});

test("D-Link network surfaces expose only the physical Wi-Fi interface", async () => {
  const config = await readFile("src/pages/config.ts", "utf8");
  const tools = await readFile("src/pages/tools.ts", "utf8");
  assert.match(config, /groupSections:\s*true/);
  assert.match(config, /Scan Wi-Fi networks/);
  assert.match(config, /Available Wi-Fi networks/);
  assert.match(config, /Use selected network/);
  assert.doesNotMatch(config, /interfaces\.eth0\./);
  assert.doesNotMatch(config, /interfaces\.usb0\./);
  assert.match(tools, /name === "wlan0"/);
});

test("Information pages expose each real source without a duplicate diagnostics menu", async () => {
  const tools = await readFile("src/pages/tools.ts", "utf8");
  assert.doesNotMatch(tools, /Process snapshot|Status snapshot|System snapshot/);
  assert.match(tools, /"network-sockets": \{ query: "netstat"/);
  assert.match(tools, /"kernel-log": \{ query: "dmesg"/);
  assert.match(tools, /"system-log": \{ query: "logread"/);
  assert.match(tools, /processes: \{ query: "top"/);
  assert.match(tools, /renderCrontab/);
  assert.doesNotMatch(tools, /renderDiagnostics/);
});

test("multi-page sections use one route-aware desktop and mobile navigation", async () => {
  const tools = await readFile("src/pages/tools.ts", "utf8");
  const shell = await readFile("src/app/shell.ts", "utf8");
  const styles = await readFile("src/styles.css", "utf8");
  const navigation = await readFile("src/app/navigation.ts", "utf8");
  for (const section of ["Information", "Settings", "Streamer", "Services", "Tools"]) assert.match(navigation, new RegExp(`${section}: \\[`));
  for (const group of ["Overview", "Configuration", "Logs", "Runtime", "Storage", "Connectivity", "Camera", "Administration", "Video", "Detection", "Integrations", "Capture", "Diagnostics", "Maintenance"]) assert.match(navigation, new RegExp(`label: "${group}"`));
  assert.match(shell, /section-nav-link/);
  assert.match(shell, /section-page-select/);
  assert.match(shell, /"aria-label": `\$\{definition\.group\} page`/);
  assert.match(shell, /location\.hash = `#\/\$\{select\.value\}`/);
  assert.doesNotMatch(shell, /subnav|subnav-link/);
  assert.match(styles, /section-layout\.has-section-navigation/);
  assert.match(styles, /\.section-nav,\s*\.preview-page \.control-card \{[\s\S]*position: sticky/);
  assert.match(styles, /\.section-select\s*,\s*\.section-select-label \{ display: none/);
  assert.doesNotMatch(tools, /renderInformationLayout|information-nav|information-layout/);
  assert.match(styles, /--main-top-space:\s*2\.25rem/);
  assert.match(styles, /main\.frame\s*\{[^}]*padding-block:\s*var\(--main-top-space\) 4rem/);
  assert.match(styles, /--main-top-space:\s*1\.375rem/);
  const order = ["status", "usage", "crontab", "onvif-info", "prudynt-info", "thingino-info", "kernel-log", "streamer-log", "system-log", "processes", "network-sockets", "kernel-modules", "os-release", "overlay"];
  let previous = -1;
  for (const page of order) {
    const index = navigation.indexOf(`id: "${page}"`);
    assert.ok(index > previous, `${page} is out of order`);
    previous = index;
  }
  for (const label of ["ONVIF", "Prudynt", "Thingino", "Network connections"]) assert.match(navigation, new RegExp(`label: "${label}"`));
});

test("fullscreen controls reuse the existing preview media and never own media lifecycle", async () => {
  const fullscreen = await readFile("src/app/fullscreen-preview.ts", "utf8");
  const preview = await readFile("src/pages/preview.ts", "utf8");
  const streamer = await readFile("src/app/streamer-preview.ts", "utf8");
  const styles = await readFile("src/styles.css", "utf8");
  assert.match(fullscreen, /this\.frame\.requestFullscreen\(\)/);
  assert.match(fullscreen, /document\.exitFullscreen\(\)/);
  assert.match(fullscreen, /event\.key !== "Enter" && event\.key !== " "/);
  assert.match(fullscreen, /event\.key === "Escape"/);
  assert.doesNotMatch(fullscreen, /MjpegPreview|\.src\s*=|routes\.media/);
  assert.match(preview, /new PreviewFullscreen\(frame, \[video, image\], "live camera preview"\)/);
  assert.match(streamer, /new PreviewFullscreen\(this\.frame, \[this\.video, this\.image\], "Streamer live preview"\)/);
  assert.match(styles, /\.fullscreen-preview\.fullscreen-fallback[\s\S]*position:\s*fixed/);
  assert.match(styles, /\.fullscreen-preview:fullscreen[\s\S]*object-fit:\s*contain/);
});

test("transient session probe errors do not render the login form", async () => {
  const source = await readFile("src/main.ts", "utf8");
  assert.match(source, /if \(!renderingLogin\) showConnectionError\(error\)/);
  assert.match(source, /Camera connection interrupted/);
});

test("production sources do not contain prohibited compatibility routes", async () => {
  const files = [
    "src/api/routes.ts", "src/api/control.ts", "src/api/media.ts", "src/main.ts", "index.html",
  ];
  const source = (await Promise.all(files.map((file) => readFile(file, "utf8")))).join("\n");
  assert.doesNotMatch(source, /\/x\/[^\s"']*\.cgi/i);
  assert.doesNotMatch(source, /agent\.cgi/i);
  assert.doesNotMatch(source, /thingino[-_ ]agent/i);
});

test("r5 does not expose the legacy send-destination feature", async () => {
  const navigation = await readFile("src/app/navigation.ts", "utf8");
  const config = await readFile("src/pages/config.ts", "utf8");
  const routeSource = await readFile("src/api/routes.ts", "utf8");
  assert.doesNotMatch(navigation, /Send destinations|id: "send"|\| "send"/);
  assert.doesNotMatch(config, /Send destinations|Destination tests|decodeSendConfig|services\.send/);
  assert.doesNotMatch(routeSource, /services\/send\/config/);
});
