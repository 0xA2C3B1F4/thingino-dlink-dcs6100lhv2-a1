# Thingino for D-Link DCS-6100LHV2 A1

This is a local-first Thingino port for the D-Link DCS-6100LHV2 hardware
revision A1. Firmware is built locally on your computer. After installation,
the WebUI, RTSP, ONVIF, and Home Assistant integration run on your local network
without a D-Link cloud account or cloud connection. An accepted model-universal
firmware build may be reused only across DCS-6100LHV2 A1 cameras. Each physical
camera still requires its own recovery evidence, authorization, and provisioning
sidecar. Do not use this installer or its images on another model or revision.

This is an independent project. It is not affiliated with, endorsed by, or
maintained by D-Link or the Thingino project.

> [!IMPORTANT]
> There is no supported public firmware release yet. Check
> [release status](docs/status.md), [installation](docs/installation.md), and
> [recovery](docs/recovery.md) before building or using removable media.

## Use with a coding agent

This repository includes the installable
[`dcs6100-thingino` skill](skills/dcs6100-thingino/SKILL.md). It directs an
agent to the current CLI, release gates, authorization boundaries, and evidence
levels for this exact camera revision.

Ask an agent with GitHub skill installation support:

> Install and use the `dcs6100-thingino` skill from
> `https://github.com/0xA2C3B1F4/thingino-dlink-dcs6100lhv2-a1/tree/main/skills/dcs6100-thingino`.

The skill installs agent instructions only. It does not install this Python
package, Docker, toolchains, private model inputs, or firmware, and it does not
authorize removable-media or camera actions.

## Local device-specific build

Firmware is built on your computer from pinned public sources and the matching
camera's owner-acquired libraries and recovery material.

The camera-acquisition step mounts stock mtd3 read-only and copies
`/lib/libimp.so`, `/lib/libalog.so`, `/lib/libsysutils.so`, and
`/lib/libaudioProcess.so`. The client validates these paths against the public
profile catalog. No private source repository is needed. During setup, you
supply the station network details and the installer creates installation-specific
management, API, and SSH material locally. Shared caches hold locked public inputs. Camera
libraries, credentials, build runs, and install sets stay in your private
workspace.

## Reusable model build and per-camera authorization

The preferred path builds the signed kernel, permanent bootstrap, and system
SquashFS once with `artifact_scope: model-universal`. That build accepts no
WPA configuration, recovery session, camera identifier, SSH key, API key, or
management credential. Its data member is empty and its network-facing startup
scripts remain non-executable until separate provisioning succeeds.

`thingino-dlink local-build build-universal` creates those identical model
bytes from public source and the owner-acquired stock vendor bundle. The default
source-native media profile provides RTSP and MJPEG; the separately reviewed
Raptor artifact is an optional WebRTC addition. `thingino-dlink universal
configure` creates the session-bound camera configuration and a stable local
authorization signer. `thingino-dlink universal provision` creates a private signed audit
sidecar plus a deterministic, exact-span per-camera JFFS2 overlay image.
`thingino-dlink universal authorize` binds both and the exact universal firmware
digest to that camera's independently validated mtd0/mtd4/mtd5 identity. Stage
the card with `thingino-dlink universal stage`, boot the stock updater once,
then return the card to the host and run `thingino-dlink universal handoff`.
The handoff revalidates the same tuple and makes the stock selector inert before
Stage 1 can boot. Stage 1 snapshots and verifies a camera-keyed HMAC
authorization, exact stage 2, data action, and JFFS2 image before removing the
bootstrap or entering any final NOR write phase. It writes and reads back the
complete private overlay before the final kernel activation eraseblock.

Backups, protected readbacks, factory partitions, recovery sessions, and
provisioning sidecars must never be copied between cameras. The older
`local-build build` command remains explicitly `device-personalized`; its
output embeds private values and must not be represented as reusable firmware.
Physical provisioning write/readback and interruption acceptance remain release gates,
so the universal host artifacts are not yet a supported firmware release.

## What this build changes

The D-Link port replaces parts of upstream Thingino where the camera's 64 MiB
memory limit, flash layout, media pipeline, or browser behavior needs a fixed
device-specific implementation.

| Area | Upstream or earlier design | D-Link A1 design | Reason |
| --- | --- | --- | --- |
| WebUI | Server-rendered pages, CGI programs, and page-specific JavaScript | Four-file static TypeScript application | Removes executable web content and gives the browser one typed API |
| Control plane | CGI, shell helpers, `curl`, `jct`, and a compatibility Agent | One Rust service, `thingino-controld` | Centralizes authentication, configuration, actions, and errors |
| HTTP ingress | CGI and request plugins | Static files plus bounded nonblocking proxies | Keeps slow media clients out of control workers |
| Media ownership | Request handlers mixed control and transport duties | Prudynt owns ISP, frame sources, encoders, RTSP, JPEG, and recording | Prevents a second ISP or encoder owner |
| Browser preview | About 5 fps MJPEG | MJPEG in the base profile; optional H.264 WebRTC through Raptor `rwd` | The tested Raptor camera candidate delivered about 15 fps on both streams |
| Home Assistant | Shell and helper processes | Native MQTT and Motion handling in Control | Removes request-time processes and bounds reconnect state |
| Hardware settings | Generic GPIO and light choices | Fixed A1 capabilities and ownership | Prevents unsupported or unsafe output controls |
| Installation | Generic full-flash assumptions | Stock-U-Boot bootstrap plus fixed mtd3 system/data regions | Preserves the stock bootloader and device data partitions |

## Supported hardware

- model `DCS-6100LHV2`, hardware revision `A1` only;
- Ingenic T31N with 64 MiB physical RAM;
- OS02G10 at 1920x1080;
- RTL8188FU Wi-Fi;
- 42 MiB Linux memory and 22 MiB ISP reserved memory; and
- the exact six-partition 16 MiB NOR layout in [hardware.md](docs/hardware.md).

Unknown model, revision, flash geometry, source identity, or artifact size is
a hard failure. Generic Thingino full-flash images and the Web Flasher are not
compatible with this camera.

## Main features

- main and substream H.264 at the selected 15 fps profile;
- MJPEG browser preview, with native two-stream WebRTC in the optional Raptor profile;
- RTSP, snapshots, ONVIF, OSD, Motion, privacy, audio, recording, and timelapse;
- Day, Night, and Auto with fixed IR-cut and 850 nm illumination controls;
- static responsive WebUI with a same-origin authenticated Control API;
- native Home Assistant MQTT discovery and commands; and
- persistent settings in the fixed mtd3 JFFS2 data region.

See [features.md](docs/features.md) for the validation level of each group.

## Installation quickstart

There is no supported public firmware download yet. A public checkout can build
the reusable base image after a read-only acquisition of the four allowlisted
stock libraries from the owner's matching camera. It does not need a private
repository, a private media closure, or Raptor. Each installation still needs
that camera's own recovery set, authorization, and private provisioning
sidecar; those camera inputs do not change the universal firmware bytes.

The commands below show the current installation workflow for a reviewed private
candidate. Stop if the [release status](docs/status.md) lists an open gate for
the operation you intend to perform.

### 1. Install and check the host tools

Use Python 3.11 or newer from a clean, reviewed checkout:

```bash
make check
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
thingino-dlink --help
python -m installer.user_cli --help
```

Run these commands from the repository root and keep the virtual environment
active for the remaining host commands. This avoids modifying an
externally-managed system Python. The `.venv` directory and editable-install
metadata are ignored by the public-tree checker, so `make check` remains
repeatable after installation. The default below creates the build workspace
beside the checkout. Set `DCS6100_DATA_VOLUME` to the exact mounted volume root
that contains both paths; a containing directory is not a mount root. Override
the build path if that volume does not have enough free space:

```bash
export DCS6100_BUILD_ROOT="$(cd .. && pwd)/dcs6100-build"
export DCS6100_DATA_VOLUME=/path/to/exact-mounted-volume

thingino-dlink workflow-preflight --json \
  --mode production-build \
  --data-volume "$DCS6100_DATA_VOLUME"
```

Install and start Docker Desktop, then let the installer create a two-run local
build workspace. `prepare` records its non-secret identity in the installer
state, so later commands also work without `--build-root` after the environment
variable is unset:

```bash
thingino-dlink local-build prepare
thingino-dlink local-build bootstrap
thingino-dlink local-build acquire
thingino-dlink local-build recovery-assets
# Complete stock-recovery backup-prepare, backup-capture, and backup-validate.
thingino-dlink local-build build-universal \
  --vendor-bundle-dir /path/from/recovery/vendor
thingino-dlink universal configure \
  --session-dir /path/to/private/recovery-session \
  --output-dir /path/to/private/install-config
```

`recovery-assets` is the public bridge between `acquire` and the camera backup.
It builds the source-locked MIPS toolchain, the locked Buildroot download
cache, both collector kernels, effective Linux configurations, the matching
MMC module, and the inert UARTless capture package. It does not contact a
camera, stage removable media, or write NOR. Its JSON output exposes two
separate transports:

- `uart` preserves and duplicates all six original partitions with no NOR
  write;
- `uartless` uses stock U-Boot to write mtd1/mtd2, then duplicates the current
  read-only layout while preserving original mtd0/mtd3/mtd4/mtd5.

The UARTless package remains inert until the separate
`stock-recovery uartless-authorize` command receives the exact
`WRITE-MTD1-MTD2` confirmation. It never reports an original complete backup.
Follow [installation](docs/installation.md) for the SD handoff and the exact
transport-specific safety boundary.

`universal configure` validates the recovery session before asking for the
station Wi-Fi SSID and passphrase twice through hidden terminal input. It
derives the 64-hex WPA PSK, generates the per-install credentials, binds them
to that session, and creates or reuses a local authorization key pair. You do
not create a WPA file or calculate a PSK yourself.

The preferred universal build needs only the owner-acquired vendor bundle.
Its model signer is generated in private build storage when one is not supplied.
The media closure and reviewed source-built Raptor RWD artifact remain optional
advanced inputs for the legacy tested WebRTC profile. Camera Wi-Fi and
credentials are created later with `universal configure` and never enter the
universal firmware build. [Build inputs](docs/build.md) explains the boundaries.

A successful `build-universal` reports its private `install_set_dir` after one
clean build by default, source-native final-root preparation, split-kernel
packaging, and a passing schema-2 inspection. `--build-count 2` adds a second
clean build and requires byte-identical outputs for release evidence. If the
optional Raptor artifact is supplied, its overlay is validated and added. SD-card staging and camera
installation are separate, explicitly confirmed steps. Keep secret values in
the mode-restricted input files.

### 2. Inspect the finished install set

Validate the exact directory before discovering or changing an SD card:

```bash
python3 -m installer inspect-install-set \
  --install-set-dir /path/to/reviewed/install-set
```

Require a passing schema-2 fixed-split result, the intended data action, and
the expected kernel, system, data, bootstrap, and stage-2 bindings. Do not use
the legacy whole-mtd3 installer for a fixed-split install set.

### 3. Stage the validated build result on macOS, Linux, or Windows

Insert one FAT32 SD card, identify its exact whole-device ID and mount root,
then stage the already inspected local install set. The command asks the
operating system for the physical-media identity, requires the exact expected
data action, writes only the reserved installer paths through temporary names,
reads them back, and activates the verified files last.

Each value below has one exact meaning:

- `--install-set-dir` is the `install_set_dir` printed by the successful build;
  do not select a parent run directory.
- `--recovery-dir` is this same camera's already validated complete duplicate
  mtd0-mtd5 backup. It is not a directory to create empty for this command.
- `--preserved-readback-dir` is the current read-only protected-partition
  evidence for the same camera. It is likewise an earlier workflow output.
- `--expected-data-mode` applies only to the legacy personalized path and must
  equal its configured `initialize`, `preserve`, or `factory-reset` action.
- `--whole-device` is the physical SD-card disk reported by the operating
  system, while `--mount-root` is that card's mounted FAT32 volume.
- `--confirm-physical-device` repeats the exact `--whole-device` value and
  `--confirm-target` is literally `DCS-6100LHV2-A1`. They stay explicit command
  arguments so a reusable settings file cannot silently authorize another
  physical disk.

Legacy personalized macOS example:

```bash
thingino-dlink stage-install-set \
  --install-set-dir /path/to/reviewed/install-set \
  --recovery-dir /path/to/private-complete-backup \
  --preserved-readback-dir /path/to/current-read-only-protected \
  --expected-data-mode initialize \
  --whole-device /dev/diskN \
  --mount-root /path/to/mounted-card \
  --confirm-physical-device /dev/diskN \
  --confirm-target DCS-6100LHV2-A1
```

That `stage-install-set` example is the legacy personalized path. For the
preferred universal path, first run `universal provision` and `universal
authorize`, then use the fully bound `universal stage` command shown in
[build.md](docs/build.md#build-one-reusable-a1-firmware). It additionally
requires the provisioning data, authorization, universal public key, recovery
session, and the exact
`STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3` write-set confirmation.
After the verified stock mtd1+mtd2 boot, return the card to the host and run the
documented `universal handoff` before the Stage-1 boot.

Linux uses a whole-disk node such as `/dev/sdb`, not `/dev/sdb1`:

```bash
thingino-dlink stage-install-set \
  --install-set-dir /path/to/reviewed/install-set \
  --recovery-dir /path/to/private-complete-backup \
  --preserved-readback-dir /path/to/current-read-only-protected \
  --expected-data-mode initialize \
  --whole-device /dev/sdb \
  --mount-root /run/media/$USER/CARD \
  --confirm-physical-device /dev/sdb \
  --confirm-target DCS-6100LHV2-A1
```

Windows PowerShell uses the disk number shown by `Get-Disk` and the exact drive
root:

```powershell
thingino-dlink stage-install-set `
  --install-set-dir 'C:\Private\install-set' `
  --recovery-dir 'C:\Private\complete-backup' `
  --preserved-readback-dir 'C:\Private\current-read-only-protected' `
  --expected-data-mode initialize `
  --whole-device '\\.\PHYSICALDRIVE3' `
  --mount-root 'E:\' `
  --confirm-physical-device '\\.\PHYSICALDRIVE3' `
  --confirm-target DCS-6100LHV2-A1
```

The adapters reject system, internal, virtual, read-only, ambiguous, non-FAT32,
or mismatched media. Linux requires `lsblk`, `findmnt`, and `udevadm`; Windows
requires the standard Storage cmdlets. Native CI verifies the Linux and Windows
host contracts. One macOS card completed the private install-and-restore
workflow with storage readback; the wider reader, card-format, Linux, and
Windows physical matrix remains an open release gate.

### 4. Install and verify the camera

Confirm the bottom label says `DCS-6100LHV2`, hardware revision `A1`. Power the
camera off before inserting or removing the card. Follow the exact physical
sequence supplied with the reviewed candidate, and never interrupt power while
a write is active. After completion, power the camera off, remove the card,
boot Thingino, and run management and media acceptance separately.

Read [installation.md](docs/installation.md) for the complete write boundary
and [recovery.md](docs/recovery.md) before the first persistent write. The
installer never authorizes stock physical mtd0, mtd4, mtd5, a generic
full-flash image, or the Thingino Web Flasher. The split kernel's logical
`/dev/mtd4` is the data region inside stock physical mtd3; see the exact
[hardware map](docs/hardware.md#nor-layout).

## Source check and preparation

Python 3.11 or newer is required for host tools. Run the source gate first:

```bash
make check
```

Choose an empty build directory outside this repository, then verify and
prepare the immutable upstream source. This low-level development example uses
the same single workspace variable as the quickstart:

```bash
export DCS6100_BUILD_ROOT="$(cd .. && pwd)/dcs6100-build"

python3 scripts/source_checkout.py validate-lock
python3 scripts/source_checkout.py fetch \
  --destination "$DCS6100_BUILD_ROOT/thingino-sources"
python3 scripts/source_checkout.py verify \
  --checkout "$DCS6100_BUILD_ROOT/thingino-sources"
python3 scripts/source_prepare.py \
  --checkout "$DCS6100_BUILD_ROOT/thingino-sources" \
  --destination "$DCS6100_BUILD_ROOT/thingino-prepared"
```

`fetch` is the networked step. Verification and preparation are offline.
Firmware compilation also needs the pinned builder inputs and the validated
four-path vendor bundle acquired from the owner's matching camera. The bundle
stays in the local private workspace while redistribution terms remain
unresolved. The resulting source-native base includes RTSP and MJPEG. WebRTC is
present only when the optional Raptor `rwd` overlay and its provenance gates
pass.
Continue with [build.md](docs/build.md).

## Repository map

- [`components/thingino-control/`](components/thingino-control/) contains the
  Rust WebUI and API control service.
- [`components/raptor-rwd/`](components/raptor-rwd/) contains the video-only
  WebRTC consumer, source pins, patches, and lifecycle scripts.
- [`webui/`](webui/) contains the static TypeScript frontend.
- [`profiles/dlink-dcs6100lhv2-a1/`](profiles/dlink-dcs6100lhv2-a1/) contains
  hardware, source, runtime, and artifact contracts.
- [`patches/`](patches/) contains the reviewed Thingino, Prudynt, uhttpd, and
  ONVIF changes.
- [`installer/`](installer/) contains host validators, builders, recovery
  state, and bounded media preparation.
- [`tests/`](tests/) contains host, contract, negative-path, and fake-NOR tests.
- [`docs/index.md`](docs/index.md) is the complete documentation index.
- [`source-export.json`](source-export.json) records the generated development
  export base and its policy. Later public-only changes are recorded by the Git
  commits layered on that base.

## Licensing

Project-authored source and documentation use the [MIT License](LICENSE).
That license does not cover third-party patch context, toolchains, libraries,
kernel modules, firmware, tuning data, or a complete firmware image. The open
Prudynt license question is disclosed, and the source snapshot intentionally
retains all 48 required patches. Their inclusion does not place Prudynt-derived
context under MIT. The Prudynt grant and the remaining redistribution questions
still block a public binary release. See [release status](docs/status.md) and
[third_party/NOTICE.md](third_party/NOTICE.md).
