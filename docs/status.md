# Release status

Historical runtime baseline validated: 2026-09-06. Documentation updated: 2026-09-21.

This repository provides source and host tools. There is no supported firmware
download yet. The validated platform is DCS-6100LHV2 A1 with Apple Silicon macOS.
Physical test coverage includes one camera.

The release targets new users and clean first installations from stock firmware.
Compatibility with earlier development firmware and migration of its settings
are outside scope. First-installation, interrupted-installation recovery, and
same-camera verification remain release requirements.

The tables below describe named evidence, not blanket acceptance of later source
changes. The full-Raptor source path has passed host composition checks and a
source-built camera exercise. A new release candidate still needs its own image,
installation, browser, and physical checks.

The latest host-built candidate is `aad992b6`. Its two clean complete-firmware
builds produced 16 byte-identical artifact pairs. It increases the heartbeat
Day/Night and Privacy read allowances after the earlier `fb43b075` candidate
failed a bounded state-completeness check. The caller's deadline and unknown/null
failure states remain unchanged. Candidate ID
`69726e9a5b738f3db8fa8eaebd141399bc8b67f8f38a4462d35b9143d3518b11`
is now installed on the first camera. Both normal installation stages completed,
the supported universal verifier passed every application, Control, health,
media and WebUI gate, and independent comparison matched the exact kernel and
system plus the same camera's three protected originals. The installer did not
write the protected partitions.

Bounded checks on these exact bytes returned 15 successful concurrent API
responses and five complete heartbeat states without a checked process restart.
Safari playback was audible on both streams after connecting with the microphone
off, enabling it and pressing Listen once without Reload. Both snapshot and
MJPEG endpoints decoded at their expected sizes. Both RTSP streams enforced
Digest authentication and decoded three video frames. ONVIF Media1 and Media2
returned working stream and snapshot URLs; 18 further snapshot checks confirmed
and then restored Privacy behavior. A separate 40-second own-session check stayed
authenticated and logout revoked that session. It did not test natural expiry or
clock changes. See the [September 21 evidence](release-evidence/2026-09-18-raptor-mic-acknowledgement.md#september-21-installed-aad992b6-candidate).

These are same-image bounded observations. They do not transfer results from an
older candidate, establish every control or setting, prove persistence, or close
resource, endurance or release acceptance. Three resource samples around one
slow MJPEG connection showed stable process IDs and restored thread and descriptor
counts. The collector's error counters are inconclusive because its expected
log file was unavailable; actual kernel and component log review remains open.
Some service memory remained above baseline, so repeated-cycle resource acceptance remains open. Earlier
audio observations remain evidence only for their named older versions.

The September 12 full-Raptor check included ROD and its font in the installed
image. Main and substream WebRTC decoded at approximately 15 fps. OSD format
changes were saved, read back and restored. Motion, Day/Night and Privacy
controls completed in 138–704 ms, and twelve Motion configuration reads
returned HTTP 200. These checks do not establish that every setting on every
WebUI page is implemented or has been tested.

The newer source has host coverage for OSD size, text visibility and RGBA
fill/outline/background colors, Motion timing, event storage and its bounded
built-in speaker alert, fourteen image-quality controls, white balance,
speaker settings, fixed-time and sunrise/sunset Day/Night schedules. Streams have checked FPS,
GOP, codec, H.264 profile, bitrate-mode, target-bitrate and RTSP-path operations,
plus saved resolution changes that take effect after a full camera restart.
Browser tests cover saving and reloading these settings, preserving simultaneous
edits, and reporting incomplete application or persistence without success.

The September 13 checkpoint completed a clean full-Raptor universal build.
Independent checks verified the signed bundle, packed components and the final
kernel's 42/22 memory split. Later OSD RGBA, solar schedule, resolution, Home
Assistant interval and speaker-alert changes still require a matching build.
Those settings also need checks of their actual effects and reboot persistence.
Long-running full-Raptor stability remains open. The [feature table](features.md)
separates source results from device acceptance. A passing page-load test does
not establish that every field on that page works.

The September 16 host candidate at source commit
`bd93c29b56d095d67562c9f3b89015b08321f013` completed two independent
`complete-firmware` builds. The base image, full Raptor component, final root,
split kernels and complete install set were byte-identical. The second build
did not reuse the Raptor component cache. Its signed universal bundle has
SHA-256 `d594153ac2f11d8baa0f1b9decc8dad0950f9d422210a4e365fc71738851cf8a`.
This is host evidence, not camera acceptance or permission to distribute the
firmware.

The September 17 Preview audio changes correct the reciprocal WebRTC SDP
direction and start RWD's audio reader with `video_only=false`. They do not
enable microphone capture at boot or change the computer's microphone state.
The operator heard audio with Listen on both Mainstream and Substream after
the RWD, WebUI and profile changes were installed as overlays on the older
September 16 whole image. This is bounded overlay acceptance, not acceptance
of a new complete firmware image. The intermittent microphone transition or
independent-readback failure was still unresolved at that checkpoint. See the
September 19 candidate observations below for subsequent results.

The new September 17 full-Raptor candidate completed the two-build comparison
with the explicit host-packaging completion described in the
[dated build evidence](release-evidence/2026-09-17-raptor-audio-reproducibility.md).
All 16 compared files, including independently compiled full-Raptor payloads,
final roots, split kernels and signed install sets, were byte-identical. Its
universal bundle has SHA-256
`98c764a5a01eac261290e6cb37e1a83c75b2006da01bb200f8e325b91dbbd7fd`.
The reproducibility gate is closed for these exact candidate inputs. Subsequent
same-camera installation passed native management verification. Independent
readback of the complete system partition matched the candidate padded to the
fixed system region, and live RWD matched the built executable. This establishes
persistent system installation, not complete physical acceptance or permission
to distribute firmware. Independent kernel equality remains unverified.

Fresh-install source `c4cf3ae` also passed two clean complete-firmware builds;
see the [September 19 evidence](release-evidence/2026-09-19-fresh-install-reproducibility.md).
Subsequent installation of that candidate passed independent kernel/system and
protected-span readback. The operator heard first-attempt Preview audio on both
streams. Ten of its twenty acceptance checks are recorded as passed; full
physical acceptance remains open. Source `f99b4cf` subsequently fixes a missing
SD unmount before the installer's final reboot. Host tests and two targeted MIPS
builds passed. Complete source `cd516ef` then passed two clean full builds and
signed-package validation. Its runtime kernel and system image are byte-identical
to installed `c4cf3ae`. The corrected package has since been installed, and all
14 kernel/system/protected/runtime readback hashes matched. Its new boot still
emitted the FAT dirty warning. The kernel preserves a pre-existing dirty flag
even on normal unmount, so clean-unmount acceptance remains open pending a
clean-baseline, phase-specific check. The warning's origin is not yet proven.
After an approved card-specific correction of the existing state flag, a normal
runtime boot no longer emitted the warning and the operator reported working
audio. Both recorder channels subsequently passed start/stop, full MP4 decode
and host-side file-list checks. Four checks are directly recorded for `cd516ef`;
its candidate matrix remains incomplete. These observations do not close the
installer's phase-specific cleanup requirement. The dated evidence separates
these artifacts, prior tests of identical runtime bytes and the remaining checks.

On September 18, a single native microphone-enable command lost its response
although independent RAD readback showed input enabled. A slow-handler IPC
regression reproduced acknowledgement loss when handler work consumed the
server's receive deadline. Separate bounded response transmission passed host
tests. At that September 18 checkpoint it was not in the installed candidate.
The September 19 build and operator checks supersede that installation state;
they do not close every cold-boot, lifecycle or settings acceptance requirement.
See the dated evidence above for the boundaries of these results.

## Historical tested installation

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

### September 6 validated firmware

The system image fits the fixed 6,619,136-byte system region.

| Artifact | SHA-256 |
| --- | --- |
| System SquashFS, 6,500,352 bytes | `71311eaf4b578f1c357b645cd4d6708e5b3e72f477a2cfc2cd81daff148b8b63` |
| Final kernel | `570d9653f5b541592fc94545b814ae123833c5ebb5d086987e199bbfb8e8ae4a` |

## Historical tested runtime features

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
- `local-build build-universal` builds the full Raptor stack from locked
  sources. It needs no separately prepared media archive.
- The universal installer has host-tested `initialize` and `preserve` build,
  authorization, Stage-1 contract and manifest paths. Those results do not
  establish physical acceptance of the latest complete source candidate; universal
  `factory-reset` install sets remain rejected.
- Candidate `38607f3` has historical operator-audible Mainstream and Substream
  acceptance after a cold boot: connect while microphone Off, enable it and
  press Listen once, without Reload or retry. Running RWD and the full system
  partition independently match the new build. This required approved removal
  of a preserved old RWD binary override; existing configuration was retained.
  See the [candidate evidence](release-evidence/2026-09-18-raptor-mic-acknowledgement.md).
  This result did not transfer to `fb43b075` or `aad992b6`; the current
  `aad992b6` audio result is separately recorded above. Other controls, settings
  and the full acceptance matrix remain open.
- Linux and Windows staging adapters have host coverage, not equivalent physical
  installation acceptance. The full guided build targets Apple Silicon macOS.

Physical stock mtd0, mtd4 and mtd5 remain protected. The split kernel's logical
mtd4 is data inside physical mtd3, not protected stock mtd4. The complete write
set and card handoffs are in the [installation guide](../README.md).

## Source and host checks

`make check` validates the allowlist/privacy policy, source lock, documentation,
release ledger, API contract and Python tests. The CI workflow defines checks
for Control, WebUI, Chromium fixtures and Linux/Windows host contracts.
Hosted execution is not currently accepted: the latest inspected public run,
[`35357294489`](https://github.com/0xA2C3B1F4/thingino-dlink-dcs6100lhv2-a1/actions/runs/35357294489),
for commit `28764a5a00df964ff3961928420f203356e00168`, ended before any of its
eight jobs executed steps. GitHub reports an account payment/spending-limit
restriction. Local passes do not replace these unexecuted remote checks.
The repository owner confirmed that the hosted allowance is exhausted and
cannot currently be extended. Leave hosted platform acceptance open; do not
treat retries or local runs as a substitute for the missing hosted evidence.

[`source-export.json`](../source-export.json) records the exported source commit,
file inventory and per-file hashes. Subsequent changes are recorded in Git.

For a completed two-build run, `scripts/release_closure.py` validates the
install set, final root, Raptor component, source locks and reproducibility
evidence before writing `candidate-closure.json` and `notice-manifest.json`:

```bash
python3 scripts/release_closure.py \
  --run-dir /path/to/completed/run \
  --output-dir /path/to/new/release-evidence \
  --universal-public-key /path/to/release-ed25519.pub
```

These sidecars bind technical evidence to exact candidate bytes. They always
report legal review as `not-assessed` and redistribution as
`not-authorized-by-this-repository`; generating them does not close a legal
or physical release gate.

`scripts/source_delivery_inventory.py` records all 13 full-Raptor source
entries, their public origins, Git identities, reconstruction patches and
license/notice digests. With `--verified-cache-root`, it also checks the
reconstructed trees using the normal source validator. The output contains
no private cache paths or vendor blobs. It is an inventory, not the source
delivery archive or a legal review, and it does not close a firmware gate.

## Open release gates

[`policy/release-gates.json`](../policy/release-gates.json) tracks source
publication separately from firmware distribution. Remaining firmware
requirements include:

- Raptor corresponding-source review and RTL8188FU license-file provenance;
- the complete candidate acceptance checklist;
- physical interrupted-write, corrupt-data, reset, reinstall and final-layout
  recovery tests, including original-partition capture requirements;
- wider provisioning acceptance, including power interruption and slow cards;
- installation on another A1 camera with the same universal firmware digest
  and distinct camera artifacts; and
- physical removable-media acceptance on each claimed host platform.

The default full-Raptor profile also requires its corresponding-source and
notice review before distribution. See the [third-party notices](../third_party/NOTICE.md)
and [license review](../third_party/LICENSE_REVIEW.md).

`make release-status` reports the ledger. `make release-ready-source` checks the
source-publication scope. `make release-ready-public-firmware` remains nonzero
while firmware gates are blocked.
