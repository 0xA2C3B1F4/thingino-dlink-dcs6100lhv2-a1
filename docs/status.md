# Release status

Validation date: 2026-09-06. Documentation updated: 2026-09-07.

This repository provides source and host tools. There is no supported firmware
download yet. The validated platform is DCS-6100LHV2 A1 with Apple Silicon macOS.
Physical test coverage includes one camera.

## Tested installation

| Component | Result |
| --- | --- |
| Source preparation and universal build | Passed from a clean checkout with locked dependencies and camera-acquired inputs |
| UART-free functional recovery | Passed from D-Link firmware; duplicate capture, storage verification and same-camera binding accepted |
| Provisioning and authorization | Passed |
| SD staging and handoff | Passed on macOS |
| UART-free installation | Passed |
| Installed kernel, media and WebUI | Matched the built candidate |
| Management verification | Passed, including with main WebRTC active |
| Delayed-start and timeout handling | Host regression tests passed |

Recovery capture and the latest build/installation have separate acceptance
records. The current five-minute management timeout has host coverage and
running-camera acceptance, but has not been revalidated through a cold install.

### Validated firmware

The system image fits the fixed 6,619,136-byte system region.

| Artifact | SHA-256 |
| --- | --- |
| System SquashFS, 6,500,352 bytes | `71311eaf4b578f1c357b645cd4d6708e5b3e72f477a2cfc2cd81daff148b8b63` |
| Final kernel | `570d9653f5b541592fc94545b814ae123833c5ebb5d086987e199bbfb8e8ae4a` |

## Tested runtime features

| Feature | Result |
| --- | --- |
| Main WebRTC | 10 minutes at approximately 15 fps; no reported dropped frames or lost packets |
| Substream WebRTC | 10 minutes at approximately 15 fps; no reported dropped frames or lost packets |
| Stream selection | Main-to-substream switch passed |
| Motion | On/off controls passed; confirmation within 866 ms in the measured sequence |
| Privacy | Main/substream controls passed; sampled main frames were fully black |
| Day/Night | Controls passed |
| Information | Load and three refreshes passed; complete status data, HTTP 200, confirmations within 128 ms |
| Process stability | No reboot, service restart or checked kernel failure marker during the stream test |
| Resource use | Thread and file-descriptor counts unchanged; Raptor RSS ranged from 2,112 to 3,888 KiB, with virtual size fixed at 9,432 KiB |

The runtime checks cover bounded sessions, not multi-day stability. Other
features and their validation state are listed in [features](features.md).
The full acceptance scope is in [testing](testing.md#candidate-acceptance).

## Known issues and installation limits

- Intermittent reboot and card-boot failures from development testing remain
  unresolved. They did not recur in the validated installation and runtime tests.
- The UARTless capture route replaces original physical mtd1/mtd2 before
  collection. It provides functional recovery, not an exact original full-flash
  backup or a promise of stock restoration. See [recovery](recovery.md).
- The base profile provides RTSP/MJPEG. WebRTC requires a separately reviewed
  source-built Raptor archive; the guided CLI does not build it automatically.
- The universal installation path initializes camera settings; it is not a
  preserve-settings firmware update.
- Linux and Windows staging adapters have host coverage, not equivalent physical
  installation acceptance. The full guided build targets Apple Silicon macOS.

Physical stock mtd0, mtd4 and mtd5 remain protected. The split kernel's logical
mtd4 is data inside physical mtd3, not protected stock mtd4. The complete write
set and card handoffs are in the [installation guide](../README.md).

## Source and host checks

`make check` validates the allowlist/privacy policy, source lock, documentation,
release ledger, API contract and Python tests. CI also checks Control, WebUI,
Chromium fixtures and Linux/Windows host contracts.

[`source-export.json`](../source-export.json) records the exported source commit,
file inventory and per-file hashes. Subsequent changes are recorded in Git.

## Open release gates

[`policy/release-gates.json`](../policy/release-gates.json) tracks source
publication separately from firmware distribution. Remaining firmware
requirements include:

- Prudynt distribution terms and RTL8188FU license-file provenance;
- two byte-identical clean builds from documented source and camera-acquired inputs;
- the complete candidate acceptance checklist;
- physical interrupted-write, corrupt-data, reset, reinstall and final-layout
  recovery tests, including original-partition capture requirements;
- wider provisioning acceptance, including power interruption and slow cards;
- installation on another A1 camera with the same universal firmware digest
  and distinct camera artifacts; and
- physical removable-media acceptance on each claimed host platform.

The optional WebRTC profile also requires its Raptor corresponding-source and
notice review before distribution. See the [third-party notices](../third_party/NOTICE.md)
and [license review](../third_party/LICENSE_REVIEW.md).

`make release-status` reports the ledger. `make release-ready-source` checks the
source-publication scope. `make release-ready-public-firmware` remains nonzero
while firmware gates are blocked.
