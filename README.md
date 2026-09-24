# Thingino for D-Link DCS-6100LHV2 A1

Local-first firmware for the D-Link DCS-6100LHV2, hardware revision A1.
The WebUI, RTSP, ONVIF, and Home Assistant integration run on your local
network without a D-Link cloud account.

This independent project is not affiliated with D-Link or the Thingino project.
Do not use its images or tools on another model or revision.

> [!IMPORTANT]
> There is no supported public firmware download yet. Tested on DCS-6100LHV2 A1:
> UART-free recovery and installation, source build, main/substream WebRTC,
> Motion, Privacy, Day/Night and Information. Validation covers one camera and
> Apple Silicon macOS. This is development evidence for one camera, not blanket
> physical acceptance or a public firmware release. See [test results and release status](docs/status.md) and
> [recovery requirements](docs/recovery.md) before installing.

## Choose your installation

This release targets new users installing full Raptor on a stock DCS-6100LHV2 A1.
It does not support upgrading earlier development firmware or migrating its
saved settings. Resuming an interrupted first installation and recovering the
same camera remain part of the installation workflow.

| Starting point | What to do |
| --- | --- |
| Stock A1 camera, no backup | Follow the UARTless procedure below. It replaces mtd1/mtd2 before capture and cannot preserve an exact original full-flash backup. |
| Need an exact original stock backup | Use the [UART-assisted read-only backup](docs/installation.md#public-checkout-complete-backup-path) before any SD updater. |
| Already have this camera's validated functional recovery | Reuse it and its `preserved/` directory. Skip capture, not the same-camera validation. |
| Already have an inspected universal install set | Skip firmware building. Keep its model public key and supply the matching per-camera provisioning and authorization. |
| Want the normal local build | `local-build build-universal` selects the full Raptor stack, including WebRTC. |

The repository includes the installer and validators. You supply stock media
files acquired from your camera, its recovery material, and local configuration.

See [installation projects and automation](docs/installer-projects.md) to keep
per-camera inputs once, link outputs between stages and review SD write plans.

### Full Raptor build

`local-build build-universal` selects the full Raptor media stack by default,
including its own sensor/encoder, audio, RTSP, recorder and receive-only WebRTC
services. It supports one WebRTC client at a time; the selected profile is
15 fps, with WebRTC audio reception enabled when camera capture is enabled.
Microphone and camera speaker are initially disabled. The installer acquires locked public sources, compiles against the
fresh support image's libraries and SDK, then composes the Raptor-owned runtime.
No separately prepared Raptor archive is required. Source `aff74396` passed two
clean complete-firmware builds and bounded first-camera installation and browser
checks. Full release acceptance remains open.
See [Raptor source build](components/raptor/README.md).

The support image uses catalog-locked stock `libimp`, TX-ISP and OS02G10
modules, and sensor IQ. Support libraries `libalog`, `libsysutils`, and
`libaudioProcess` come from pinned open source. The stock sensor configuration
includes I2C address `0x3c`, reset GPIO18, and 1920x1080 geometry. Public
upstream sensor modules are not substitutes. Source-build success does not
establish camera acceptance.

The full-Raptor build is the only public media path. It acquires and compiles
the locked Raptor sources as part of `build-universal`; no separate media
archive is accepted.

## Before you start

- Check the label: `DCS-6100LHV2`, hardware revision `A1`.
- Use an Apple Silicon Mac for the full guided build, Python 3.11 or newer,
  Git, and a running Docker Desktop. Allow at least 80 GiB free for the
  default single firmware build.
- Use a writable FAT32 SD card and a card reader. Back up existing card
  contents. The staging commands do not format an arbitrary card.
- Keep the computer and camera on the same local network for verification.
- Keep builds, signing keys, recovery, and configuration outside the checkout,
  in private directories on a mounted, writable volume.
- Use one writer per checkout, build workspace, SD card, and camera.
  Record the source commit and retain the successful build output.
- Power off before moving the camera's SD card. Never interrupt an active
  flash write or readback.

| Phase | Physical NOR writes |
| --- | --- |
| Host build, configuration, SD staging/handoff | None |
| UARTless recovery bootstrap | mtd1 + mtd2 |
| Installation stock-updater boot | mtd1 + mtd2 |
| Final Stage 1 installation | mtd1 + mtd3, including private data |
| Protected partitions | mtd0, mtd4, and mtd5 are never written |

The split kernel's logical `/dev/mtd4` is data inside physical mtd3, not the
protected stock mtd4. See the [hardware map](docs/hardware.md#nor-layout).
Never use generic Thingino full-flash images, Web Flasher, or raw flash commands.

## Install from a public checkout

These are sequential steps, not an unattended script. Read each result and
complete the stated physical step before continuing. Set your paths in the
variable assignments below; subsequent commands reuse them. These examples
use a macOS shell and do not include firmware or camera-specific files.

### 1. Install the host tools

Clone the public repository and work from its root:

```bash
git clone https://github.com/0xA2C3B1F4/thingino-dlink-dcs6100lhv2-a1.git
cd thingino-dlink-dcs6100lhv2-a1
git status --short --branch
git rev-parse HEAD
brew install llvm lld squashfs dropbear openssl@3
export PATH="$(brew --prefix openssl@3)/bin:$PATH"
openssl version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
make check
thingino-dlink --help
```

Keep the virtual environment active. This avoids changing an externally-managed
system Python. In a new terminal, return to the checkout and activate `.venv`
again. A prompt ending in `%` or `$` is ready; do not type the prompt itself.
Keep the Homebrew OpenSSL path in that terminal too. The signer invokes
`openssl` from `PATH` and requires Ed25519 support.

| Command | Purpose |
| --- | --- |
| `thingino-dlink` or `python -m installer.user_cli` | Guided `local-build`, `stock-recovery`, `universal`, and user-facing inspection |
| `python -m installer` or `dcs6100-thingino` | Low-level artifact tools; no `local-build` or `universal` |

### 2. Prepare public build and recovery inputs

Choose a new build root outside the repository. Set the data volume to the
exact mount root, not a directory inside it. Put both workspaces on that volume.
Start Docker Desktop before preflight.

#### Set paths once

Replace `/path/to/mounted-volume` with your mounted build volume. Choose workspace
names once here. Use a separate camera workspace for each camera; retain it
when resuming that camera's installation. For a new build, choose a new build
root. Do not delete an existing workspace to make a command pass.

```bash
export DCS6100_DATA_VOLUME="/path/to/mounted-volume"
export DCS6100_BUILD_ROOT="$DCS6100_DATA_VOLUME/thingino/build-a1"
export DCS6100_CAMERA_ROOT="$DCS6100_DATA_VOLUME/thingino/camera-1"
export DCS6100_RECOVERY_ROOT="$DCS6100_CAMERA_ROOT/functional-recovery"
```

Keep the quotes, including when paths contain spaces. `local-build` reads
`DCS6100_BUILD_ROOT` directly. The other paths are shell variables passed to
the explicit command arguments below, not automatic CLI defaults.

```bash
umask 077

mkdir -p "$DCS6100_CAMERA_ROOT"
thingino-dlink local-build prepare --build-root "$DCS6100_BUILD_ROOT"
thingino-dlink local-build status
thingino-dlink local-build bootstrap
thingino-dlink local-build acquire
thingino-dlink local-build recovery-assets
```

Require successful results before proceeding. Bootstrap/acquire download and
verify pinned public inputs. Recovery-assets builds collector packages without
contacting a camera. Firmware builds default to one clean run; recovery-assets
independently compares two collector builds. Use `--build-count 2` on firmware
preparation/build only for reproducibility evidence.

Keep the printed `files.uartless_package` and
`files.uartless_package_manifest` paths:

```bash
export DCS6100_CAPTURE_PACKAGE="/path/from/recovery-assets/uartless-capture-bootstrap.bin"
export DCS6100_CAPTURE_MANIFEST="/path/from/recovery-assets/uartless-capture-bootstrap.manifest.json"
```

Replace these two assignment values with the exact paths from the successful
result, enclosed in double quotes. Do not guess a run directory or select
whichever file happens to be newest.

#### Save paths for another terminal

After preflight succeeds and the camera workspace exists, use your editor to
save the four path assignments from **Set paths once** as
`$DCS6100_CAMERA_ROOT/install-env.sh`, with your actual values. Add the capture
assignments above and, when available, the install-set, model public-key, and
immutable builder-image assignments from steps 4 and 6. Only save paths and
the image ID, never passwords, API keys, or key contents.

```bash
chmod 600 "$DCS6100_CAMERA_ROOT/install-env.sh"
```

In a new terminal, return to the checkout, activate `.venv`, and restore the
OpenSSL `PATH` as in step 1. Load your saved file, adjusting this one path:

```bash
umask 077
export DCS6100_ENV_FILE="/path/to/camera-workspace/install-env.sh"
. "$DCS6100_ENV_FILE"
```

Source only a file you created or reviewed, since the shell executes it.
Resume at the next unfinished step, not at a new build or recovery capture.
Keep the SD and UART device selections out of this file. Recheck them when
reconnecting hardware. [Installation details](docs/installation.md) use these
same variables and define any additional paths next to the relevant example.

### 3. Capture this camera's recovery and stock media files

Skip only if you already have this camera's validated functional recovery and
the camera has not returned to stock firmware since that capture.
In that case, set `DCS6100_RECOVERY_ROOT` to that existing directory; do not
create an empty recovery directory or substitute another camera's files.
After a stock restore and stock boot, do not reuse the earlier camera
authorization. Stock can update writable configuration partitions whose exact
bytes are part of the camera binding. A successful host plan validates the
provided backup, not the camera's current flash. Obtain a new validated capture
and regenerate the camera-bound provisioning and authorization before staging.
This UARTless route overwrites physical mtd1/mtd2 before collecting the current
partitions twice. It preserves original mtd0/mtd3/mtd4/mtd5, but does not promise
restoration to D-Link stock. For an exact original backup, choose the
[read-only UART route](docs/installation.md#public-checkout-complete-backup-path).

#### Identify the card each time

Repeat this identification block every time the card returns to the Mac,
including before validation, staging, or handoff. Identify it with `diskutil`;
never assume the previous disk number still applies. A whole disk is `/dev/diskN`, not
`/dev/diskNs1`. macOS example:

```bash
diskutil list external physical
export DCS6100_SD_DEVICE="/dev/diskN"
export DCS6100_SD_MOUNT="/path/to/mounted-card"
diskutil info "$DCS6100_SD_DEVICE"
```

Replace both assignment values with the confirmed whole disk and mounted
FAT32 volume. These values apply only to the currently inserted card.
For a card previously used by another camera, follow the
[capture archival and card reuse procedure](docs/installation.md#reusing-an-installation-card)
first. Stale `UARTCAP.PSV` or `DCS6100F` data must be copied and verified on the
host before removing it. Keep each camera's private recovery and configuration separate;
reuse the existing model-universal install set.

```bash
python scripts/platform/macos_media_preflight.py \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output "$DCS6100_CAMERA_ROOT/capture-media-preflight.json"

thingino-dlink stock-recovery uartless-prepare \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-media-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE"

thingino-dlink stock-recovery uartless-authorize \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-media-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-write-set "WRITE-MTD1-MTD2"
```

After success, safely eject with `diskutil eject "$DCS6100_SD_DEVICE"`.
Insert into the powered-off camera and power on. Wait for the stock updater's
completion state. It stops after writing mtd1/mtd2; it has not yet run the
collector. A timer or LED color alone is not a completion check. Read
[boot phases](docs/installation.md#boot-phases-and-failure-handling) if uncertain.

After confirmed completion, power off and return the card to the Mac.
[Identify the card again](#identify-the-card-each-time) and regenerate
preflight before handoff:

```bash
python scripts/platform/macos_media_preflight.py \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output "$DCS6100_CAMERA_ROOT/capture-handoff-preflight.json"

thingino-dlink stock-recovery uartless-handoff \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-handoff-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-stock-uboot-result "MTD1-MTD2-WRITTEN"
```

Safely eject, insert into the powered-off camera, and power on again. This
second boot runs the read-only collector. After completion, power off and
return the card to the Mac. [Identify it again](#identify-the-card-each-time),
then validate its output:

```bash
thingino-dlink stock-recovery uartless-validate \
  --collector-dir "$DCS6100_SD_MOUNT/DCS6100F" \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --output-dir "$DCS6100_RECOVERY_ROOT" \
  --confirm-output-dir "$DCS6100_RECOVERY_ROOT"
```

Require `functional_recovery_accepted: true`.
`original_complete_backup_accepted: false` is expected for this route.
The output includes `vendor/` for building and `preserved/` for same-camera
checks. Keep a separate private copy of the recovery directory off the SD card.

### 4. Build or select the universal firmware

The build needs the acquired vendor bundle, not Wi-Fi credentials. The command
below builds full Raptor from locked public sources.

```bash
thingino-dlink inspect-vendor-bundle \
  --vendor-bundle-dir "$DCS6100_RECOVERY_ROOT/vendor"
thingino-dlink local-build build-universal \
  --vendor-bundle-dir "$DCS6100_RECOVERY_ROOT/vendor"
```

That command defaults to `--data-mode initialize` for the supported first
installation. This guide does not provide an upgrade path from older development
firmware. Keep the same camera's validated recovery and provisioning inputs
when resuming an interrupted first installation.

The full-Raptor build does not accept a private media archive. Keep the vendor
bundle and all other camera-specific inputs outside the checkout.

Require phase `local-build-model-universal-install-set-inspected`. Keep the
build result, logs, manifests, and model signing key pair. Copy its exact
`install_set_dir` and `model_signing.public_key` paths:

```bash
export DCS6100_INSTALL_SET="/path/from/build/install-set"
export DCS6100_MODEL_PUBLIC_KEY="/path/from/build/model-signing/release-ed25519.pub"
python -m installer inspect-install-set --install-set-dir "$DCS6100_INSTALL_SET"
```

For an existing accepted set, inspect it and use its matching public key.
Keep `thingino-universal.tgb`, stage 2, bootstrap, and manifests together.
The signed model bundle contains no camera credentials.

### 5. Create private configuration

```bash
thingino-dlink universal init-session \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --output-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --config-output-dir "$DCS6100_CAMERA_ROOT/install-config"

thingino-dlink universal configure \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --output-dir "$DCS6100_CAMERA_ROOT/install-config"
```

The prompts ask for station Wi-Fi SSID and passphrase twice. Passphrase means
your Wi-Fi password, not your Mac password or signing key. Hidden input displays
no characters. The installer derives the WPA PSK and generates credentials.

The management credential is `install-config/installer.credential`; the API
key is `install-config/webui-api.key`. Provisioning derives a separate initial
separate RTSP credential without writing another secret file. Keep them
private. Do not paste them
into chat, command arguments, logs, or Git. For automation, see
[private input through a file descriptor](docs/installation.md#private-input-through-a-file-descriptor).

### 6. Bind firmware to this camera

Set `DCS6100_BUILDER_IMAGE` to the immutable `sha256:...` image ID recorded
by bootstrap/acquire or the build manifest, not an arbitrary Docker tag.
The model key verifies firmware. The separate authorization signer created by
`universal configure` signs camera provisioning.

```bash
export DCS6100_BUILDER_IMAGE="sha256:REPLACE_WITH_RECORDED_BUILDER_IMAGE_ID"

thingino-dlink universal provision \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --universal-bundle "$DCS6100_INSTALL_SET/thingino-universal.tgb" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --private-config-dir "$DCS6100_CAMERA_ROOT/install-config" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --signing-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pem" \
  --unsquashfs "$(command -v unsquashfs)" \
  --mkfs-jffs2 "./scripts/run_container_mkfs_jffs2.sh" \
  --output "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --data-output "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2"

thingino-dlink universal authorize \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --universal-bundle "$DCS6100_INSTALL_SET/thingino-universal.tgb" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --provisioning "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --provisioning-data "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2" \
  --provisioning-public-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pub" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --signing-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pem" \
  --output-dir "$DCS6100_CAMERA_ROOT/authorization"
```

For a preserve-mode install set, add `--data-action preserve` to the
`universal authorize` command. The authorization action must match the stage-2
action reported by `inspect-install-set`; the default authorization action is
`initialize`.

Both provisioning files contain secrets. The JFFS2 image is exactly
1,507,328 bytes. An `initialize` install replaces private settings with this
configuration. A `preserve` update still validates and binds the provisioning
artifacts but does not write the provisioning image or erase data. Keep
firmware, session, sidecar, data image, action, and authorization matched.
The inspected physical write policy must report `action: preserve` and
`verification: before-and-after-complete-region-sha256` for preserve.

### 7. Stage the installation card

[Identify the card](#identify-the-card-each-time), including when you skipped capture.
If the card holds `STOCKM3.BIN` and `STOCKM3.OK` from an earlier install,
first use [the verified backup-copy command](docs/installation.md#reusing-an-installation-card).
Do not delete recovery files to make a validation error disappear.

For a non-writing preflight, run the command below with `--plan-only`. This
step runs on the host and does not require a powered-on or network-connected
camera. It validates the supplied recovery, signed artifacts and current card,
not the camera's live state. Review the plan before executing the write; keep
the separate boot, handoff and post-install verification steps below.

```bash
thingino-dlink universal stage \
  --work-dir "$DCS6100_CAMERA_ROOT/stage-state" \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --install-set-dir "$DCS6100_INSTALL_SET" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --provisioning "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --provisioning-data "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2" \
  --authorization-dir "$DCS6100_CAMERA_ROOT/authorization" \
  --authorization-public-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pub" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-target "DCS-6100LHV2-A1" \
  --confirm-write-set "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3"
```

Require phase `camera-bound-universal-card-staged`. The command validates
the full set, writes reserved SD paths, reads them back, and activates the
stock updater last. It does not write camera NOR.

### 8. Boot the stock updater, then hand off the card

Safely eject. Insert into the powered-off camera and power on.
Wait for confirmed stock-updater mtd1/mtd2 completion. This first boot does not
install the final system or prove the WebUI is ready.

Power off, return the card to the Mac, and
[identify it again](#identify-the-card-each-time).
Use exactly the same artifacts for handoff:

Handoff also runs on the host without a live camera connection. Use
`--plan-only` to inspect its current plan, but confirm stock-updater completion
from the physical run, not from a successful host plan.

```bash
thingino-dlink universal handoff \
  --work-dir "$DCS6100_CAMERA_ROOT/handoff-state" \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --install-set-dir "$DCS6100_INSTALL_SET" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --provisioning "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --provisioning-data "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2" \
  --authorization-dir "$DCS6100_CAMERA_ROOT/authorization" \
  --authorization-public-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pub" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-target "DCS-6100LHV2-A1" \
  --confirm-stock-uboot-result "MTD1-MTD2-WRITTEN"
```

Require `safe_next_action: boot-camera-with-passive-card-to-run-stage1`.
Handoff makes the stock-matching filename inert. Leaving it active would run
the stock updater again instead of continuing the installation.

### 9. Install the final system and verify

Safely eject. Insert into the powered-off camera and power on.
Leave the card inserted and do not interrupt power. Stage 1 validates the
camera and payloads, backs up physical mtd3, writes and verifies provisioning,
installs system/kernel, activates last, passivates installation files, and
reboots into Thingino.

The camera joins your configured Wi-Fi network after installation and reboot.
The verification command allows up to five minutes for management startup.
Run it again if the first check started before the installation reboot and
timed out. Verification does not repeat flash writes. Keep power connected;
a timeout alone is not a reason to restart or restage the camera.

```bash
thingino-dlink universal verify \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session"
```

Require `camera-bound-universal-management-verified`,
`uart_required: false`, and `safe_next_action: installation-complete`.
This checks session-pinned network identity, SSH host key, health, and live
mtd3 readback. Browser media and physical controls need separate checks.

Open `https://<camera-ip>/` using the address found on your router or during
verification. HTTP redirects to HTTPS. The camera uses a locally generated
certificate; verify the address and review any browser warning yourself.
Log in as `root` with the generated management credential from
`$DCS6100_CAMERA_ROOT/install-config/installer.credential`, not the Wi-Fi password.
Before adding an RTSP client, set a separate RTSP password under
**Settings / Media access** and use the username shown there. The full-Raptor
profile initially uses `root`, a separate derived RTSP password, and Digest
authentication; its default video paths are `/stream0` and `/stream1`.
Digest authentication does not encrypt the media transport, so keep port 554 on
a trusted network.

Check both Preview stream selections. With Raptor, both should say
`Live · WebRTC`; visible MJPEG fallback alone does not validate Raptor.
Preview starts with browser playback muted. Enable the camera's **Microphone**
and click **Listen** to hear it on your computer. Listen changes browser playback
only; it does not enable or unmute camera capture or request your computer's
microphone. **Speaker** controls the camera's output, not your computer's
speakers. Switching streams or stopping preview mutes playback again. MJPEG
fallback has no audio. An available audio track does not prove audible capture.
If a transition/readback error appears, inspect the actual camera state before
retrying; the requested change may already have taken effect.
Test Day/Night and observe the physical change, then your required RTSP,
audio, recording, and Home Assistant functions.
The SD card can stay inserted. The installed system does not require it;
power off before removing it and retain its recovery files.

## If something stops

A nonzero exit or `ok: false` means that step did not pass. Do not continue
to SD writes or camera boot. Read the reported log and run read-only diagnostics.
A failure does not require abandoning diagnosis.

| Symptom | Next check |
| --- | --- |
| `invalid choice: local-build` | Use `thingino-dlink` or `python -m installer.user_cli`, not `python -m installer`. |
| Vendor-inspection route confusion | Current `thingino-dlink inspect-vendor-bundle` and the low-level form both exist. Check this checkout's `--help`. |
| Rust cache identity mismatch | Compare checkout, source lock, builder image, and receipt. Do not edit hashes or delete all caches. |
| Locked download fetch failed | Read `download-fetch.log` for the exact failing source. Do not substitute unverified downloads. |
| Clean build failed | Find the first compiler/package error in `build-a.log`. Preserve the failed run. |
| System exceeds 6,619,136 bytes | Fix the image contents and rebuild. Never extend the system region into data. |
| Old SD backup/checkpoint | Use `universal evacuate-recovery` to copy and verify it privately before restaging. If it reports a same-size Stage 2 hash mismatch, follow the documented `quarantine-inconsistent-media` flow; never delete the checkpoint manually. |
| Backend timeout, unavailable stream, grey substream | Check the installed media profile and its sensor/encoder service. A visible MJPEG fallback does not prove WebRTC works. |
| Red blinking or no LED | Identify the boot phase. Neither observation alone proves completion or failure. |

A successful command may have `next_command: null` when finished. Read
`ok`, `phase`, `safe_next_action`, and physical actions together.
Some next-command strings name only the command family; supply the required
paths from this guide or `--help`. Never bypass a stopped identity, signature,
size, recovery, or media check.

## Other hosts and reference

The full guided build currently targets Apple Silicon macOS. SD staging has
native macOS, Linux, and Windows adapters. Their host tests do not prove
end-to-end builds or physical installation on every platform.

Linux uses a whole-disk node such as `/dev/sdb`, not `/dev/sdb1`, with
`lsblk`, `findmnt`, and `udevadm`. Windows PowerShell uses the whole
physical device reported by `Get-Disk` and its FAT32 drive root.
See [host media requirements](docs/installation.md#supported-host-systems).

- [Documentation index](docs/index.md), [build internals](docs/build.md),
  [installation details](docs/installation.md), and [release evidence](docs/status.md).
- [Features](docs/features.md), [hardware](docs/hardware.md),
  [architecture](docs/architecture.md), and [Control API](docs/api.md).
- [Contributing and checks](CONTRIBUTING.md).
- `installer/` implements the guided CLI, validators, and bounded Stage 1.
- `components/thingino-control/` and `webui/` implement Control and the WebUI.
- `components/raptor/` holds the full media source pins, patches, and lifecycle code.
- `profiles/dlink-dcs6100lhv2-a1/` and `patches/` define the locked A1 build.
- [source-export.json](source-export.json) records the exported source commit
  and per-file provenance. Subsequent changes are recorded in Git.

## Licensing

Project-authored source and documentation use the [MIT License](LICENSE).
This does not license third-party patch context, vendor libraries, modules,
tuning data, or complete images for redistribution. Raptor corresponding-source
review, RTL8188FU closure, reproducible builds, and physical acceptance remain
open in [third-party notices](third_party/NOTICE.md) and [release status](docs/status.md).
The host-only `scripts/release_closure.py` command can bind a completed
two-build candidate to source and notice manifests. Its output is technical
evidence only and never grants redistribution permission.
