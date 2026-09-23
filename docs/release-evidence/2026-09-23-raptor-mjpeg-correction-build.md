# September 23 MJPEG correction: host build only

The installed `22989b85` Raptor candidate was rejected after a real Chromium
MJPEG fallback reached Live on Main stream but Preview Reload sent
`/media/v1/mjpeg?stream=0&q=51` and received HTTP 401. The WebUI generates a
bounded retry revision (`q=50` through `q=99`); the uhttpd media route accepts
and strips it before forwarding to Raptor. Control's authorization allowlist
did not accept it. This is a browser fallback failure, not an audio failure.
The rejected candidate remains installed on the first camera.

Source `aff74396532c133805e7f08549fc7bf6ba80d60b` adds only the canonical
two-digit retry revision to Control's MJPEG target allowlist. It rejects
out-of-range, noncanonical, duplicate, and mixed query parameters. Targeted
Rust tests, 40 WebUI media/route tests, `cargo fmt --check`, documentation
checks, and the full `make check` suite passed (1,076 tests, one skipped).
These are source and host checks, not a fresh camera observation.

Two clean full-Raptor builds without a Raptor component cache completed in
run `build-20260923T145048233909Z-aff74396532c`. The complete-firmware report
has `scope: complete-firmware`, `builds: 2`, `component_cache_used: false`,
`inspections_accepted: true`, `differences: []`, and `byte_identical: true`.
An independent pass rehashed all 32 files in its 16 artifact pairs, including
both final root files, both signed install sets, and their manifests; every
size and SHA-256 matched the report. The report SHA-256 is
`5559b63596fadb86c93df95c63cb079263f0a9c00edcfe835f405d81b3f37020`.
The signed install set was inspected under schema 2 for the
`dcs6100lhv2-a1-mtd3-split-v1` layout and `initialize` data mode.

| Install-set file | SHA-256 |
| --- | --- |
| `thingino-universal.tgb` | `d97e3b8857cbfe167411fefb90fb3015476bbbd425824ec0871ecb5855ff3cc1` |
| `THINGINO2.BIN` | `16477317f022ee2841cd35ad330e1a51fab171e4b4211a283af8e3eb3861590d` |
| `DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin` | `ff72558102595ec5d0f7eada806092fec5b9d384768d3018c17078a4829b85d7` |
| `stage1-bootstrap.squashfs` | `cd1a0ce36cb070d3e643e8fdca5b2843bab499bb7111834814198628d7e70b41` |

The install-set manifest SHA-256 is
`51262df649b2bc93a1709831ee36e791a8f16e22016d5cde312acfb3f6c44901`.
The technical-only release-closure check produced a candidate-closure sidecar
with SHA-256 `6ce842b96b736e303673d799c16dc6ffb74d6710f3750cc44642a8025286626e`
and a notice manifest with SHA-256
`220b8eecb2bc8cf6a34566c1bb1ab5acdd1ac17a568a1de9554b2c1d4c43904f`.
Neither sidecar approves redistribution.

The first build attempt stopped before firmware compilation when its offline builder
could not resolve `sources.buildroot.net` for the pinned tar archive. The exact
archive from the preceding verified build cache was independently rechecked
against its SHA-256 and 4,150-entry inventory, copied to this source profile's
new cache key, and revalidated by the normal builder. It was dependency input,
not a reused firmware component. The successful run made no SD or camera write.

Next, install this exact signed candidate through the supported SD workflow
and independently verify the camera identity, layout and selected readback.
In a real browser with WebRTC unavailable, test MJPEG Live, Reload, and both
stream selections; Listen must remain unavailable in MJPEG mode. Record HTTP
and UI state before any retry if the fallback goes Offline. The full 20-check
candidate matrix, recovery, wider provisioning, another A1 camera, host
platforms, source/licensing closure and binary-distribution gates remain open.
