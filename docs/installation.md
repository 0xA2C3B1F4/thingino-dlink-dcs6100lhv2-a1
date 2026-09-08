# Installation

There is no supported public installation package yet. Do not use generated
development artifacts on a camera unless the exact candidate and operation
have separate authorization.

The source can build and stage a fixed read-only RAM collector that captures
all original mtd0-mtd5 partitions twice before any stock updater runs. Host
validation and both 16 MiB reconstructions must pass before any dependent
persistent-write phase opens. Source availability alone is not device proof;
the unopened-camera transport remains a release gate.

There is still no proven automatic stock-U-Boot SD-to-RAM collector boot that
leaves all six original partitions unchanged. The complete-backup source is
implemented, but a new unopened camera needs the separately authorized UART
RAM transport for a byte-exact original six-partition backup. An explicit
UARTless functional-capture alternative uses the stock SD updater and therefore
erases and writes mtd1/mtd2 before the collector runs. It preserves original
mtd0, mtd3, mtd4, and mtd5 and must never report an original complete backup.
This alternative remains a development candidate until its physical write,
handoff, duplicate capture, retry, and functional restore have been accepted.

## Installation sequence

Use the [README](../README.md#install-from-a-public-checkout) for the complete
UARTless universal command sequence. This page explains the alternative backup
transport, phase evidence, and write boundaries.

## Paths used in these examples

First [set the shared paths](../README.md#set-paths-once), or
[load your saved settings](../README.md#save-paths-for-another-terminal).
Run commands from the checkout with its Python environment active. The examples
below reuse the README variables, so you only edit path assignments, not each
command. Keep double quotes around variable references, even without spaces.

Use the README's exact capture-package and build-result assignments when a
command needs those artifacts. Additional assignments below apply only to the
selected alternative. A new output directory must not contain a previous
capture or installation. Keep that earlier evidence and choose a new name.

Whenever the card returns to the host,
[identify it again](../README.md#identify-the-card-each-time) before using
`DCS6100_SD_DEVICE` and `DCS6100_SD_MOUNT`. Do not save either value in
`install-env.sh` or reuse a preflight from a previous insertion.

## Sequence overview

The implemented sequence is:

1. verify the exact model, A1 revision, NOR geometry, and stock source state;
2. capture and validate that camera's recovery set, keeping exact original
   and UARTless functional recovery classes distinct;
3. verify the current read-only mtd0/mtd4/mtd5 same-device binding;
4. build from pinned public source and the owner-acquired stock vendor bundle;
5. validate every artifact, partition limit, dependency, credential role, and
   manifest;
6. identify the exact external SD card and preserve unrelated files when the
   existing filesystem is usable;
7. stage the full universal install set, provisioning, and authorization
   through temporary names and verify the files by SD readback;
8. boot the card and let the bounded bootstrap install the permanent verifier;
9. run `universal handoff` to validate the set again and make the exact
   stock-matching bootstrap name inert;
10. boot stage 1, which validates the final install set before any final write;
11. write and read back only the authorized kernel, bootstrap, and stock
    physical mtd3 system/data regions, with activation last; and
12. boot Thingino, discover it on the station network, and run management and
    media acceptance separately.

The SD card is not required after a successful persistent installation.
It can remain inserted after Stage 1 passivates the install files. Retain its
recovery files and always power off before removing it.

## Boot phases and failure handling

There are two separate two-boot sequences when starting without recovery:
one for UARTless capture and another for installation. Returning the card to
the host for handoff is required between the boots in each sequence.

| Boot | What happens | What establishes completion |
| --- | --- | --- |
| Capture, first boot | Stock U-Boot replaces mtd1/mtd2 and stops in its updater completion loop | Confirm the stock-updater completion state before host `uartless-handoff`; this is not a full NOR readback claim. |
| Capture, second boot | The read-only collector writes duplicate capture files to `DCS6100F` and halts | Host `uartless-validate` must accept all duplicates, replacement identities, and storage readbacks. |
| Installation, first boot | Stock U-Boot installs the reviewed bootstrap/kernel and stops | Confirm updater completion before `universal handoff`. |
| Installation, second boot | Stage 1 validates, backs up, writes, verifies, activates, and reboots | Run `universal verify` on the station network, then browser/media acceptance. |

The stock updater's completion loop and Stage 1 failure indications are
different states. Red blinking can indicate stock-updater completion, but is
not a universal success indicator. Stage 1 requests
green during validation/backup, green plus red during final writes, red on
terminal failure, and green before reboot. Normal firmware has its own LED
behavior. Do not infer success from elapsed time, a relay click, or darkness.

The current host handoff asks the operator to confirm
`MTD1-MTD2-WRITTEN`; it does not independently read camera NOR while the
card is in the host. If the updater completion state cannot be identified,
do not guess that confirmation or power-cycle an active write. Diagnose the
phase first. UART can provide development diagnostics but is not a required
command transport for the SD installation path.

After a host error, retain the command, exit code, phase, and private log path.
Read-only diagnosis is allowed. Do not repeat physical writes until the cause
and the exact safe retry path are established. A successful terminal command
may have `next_command: null`; that alone is not a failure.

## Private input through a file descriptor

Prefer `universal configure` in an interactive terminal. Its SSID and Wi-Fi
passphrase prompts are hidden and repeated for confirmation. The management
credential and API key are generated separately.

When interactive entry is unavailable, the command accepts `--secrets-fd`
with an inherited descriptor of at least 3. The JSON object must contain
exactly `ssid`, `passphrase`, `confirmation_ssid`, and
`confirmation_passphrase`, with matching pairs. Keep it in an owner-only
file outside the repository, populate it privately, and pass the open file
descriptor, not its contents, on the command line:

```bash
thingino-dlink universal configure \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --output-dir "$DCS6100_CAMERA_ROOT/install-config" \
  --secrets-fd 3 3<"$DCS6100_CAMERA_ROOT/confirmed-wifi.json"
```

The file must already exist and contain the owner's actual values. Do not use
secrets in argv, environment variables, process substitution, shell history,
chat, or public logs. Preserve generated provisioning/configuration securely;
remove the input file securely when no longer needed under the owner's policy.

## Reusing an installation card

The commands in this section are for the README's non-project workflow, where
`DCS6100_PROJECT` is unset. If you use installation projects, follow the separate
[camera A to camera B project procedure](installer-projects.md#reuse-one-card-for-camera-a-and-camera-b)
instead. Keep the previous camera's private host recovery and configuration.

Before another UARTless capture, identify the current card again. On macOS or
Linux, copy old `UARTCAP.PSV`, `DCS6100F` and their known partial/sidecar files to
an unused private host directory. Choose a different suffix for each new archive.
The destination must be outside the SD tree on a different filesystem, without
symlink components. An active updater must first complete its explicit
handoff/passivation workflow; this command will not remove it.

```bash
export DCS6100_SAVED_CAPTURE="$DCS6100_CAMERA_ROOT/saved-capture-before-reuse-1"
thingino-dlink stock-recovery uartless-reuse \
  --work-dir "$DCS6100_CAMERA_ROOT/capture-reuse-state" \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output-dir "$DCS6100_SAVED_CAPTURE" \
  --plan-only
```

Review the plan, including the archive destination and transaction markers.
Then run the following command and enter the exact plan digest, device, target
and `COPY-VERIFY-THEN-REMOVE-CAPTURE` confirmation when prompted:

```bash
thingino-dlink stock-recovery uartless-reuse \
  --work-dir "$DCS6100_CAMERA_ROOT/capture-reuse-state" \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output-dir "$DCS6100_SAVED_CAPTURE" \
  --confirm-output-dir "$DCS6100_SAVED_CAPTURE" \
  --confirm-physical-device "$DCS6100_SD_DEVICE"
```

All copies are checked before any capture source is removed. Retain the private
archive; it preserves capture bytes without certifying incomplete data as valid
recovery. Unknown collector files stop the operation, and unrelated root files
remain untouched. If removal is interrupted after a verified receipt exists,
inspect the archive, add `--resume` to the same plan command, then confirm that
new plan with `--resume` on the second command too. If copying stopped before
the receipt existed, keep the partial archive and choose a new destination.
Windows capture archival is currently unsupported and stops before file writes.

Capture archival does **not** evacuate `STOCKM3.BIN` or `STOCKM3.OK`. Handle those
separate recovery files with the existing command below before new staging.

A completed installation may leave `STOCKM3.BIN` and `STOCKM3.OK` bound to
the previous firmware. Before staging a different set, copy them to a new
private host directory with the repository command. Identify the card again,
then choose an unused destination name. Change the suffix for each later update:

```bash
export DCS6100_SAVED_RECOVERY="$DCS6100_CAMERA_ROOT/saved-recovery-before-update-1"

thingino-dlink universal evacuate-recovery \
  --work-dir "$DCS6100_CAMERA_ROOT/evacuation-state" \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output-dir "$DCS6100_SAVED_RECOVERY" \
  --confirm-physical-device "$DCS6100_SD_DEVICE"
```

This verifies the backup, checkpoint, and any archived backup before removing
those reserved card paths. It preserves unrelated files and writes no NOR.
Retain the private output. Do not manually delete a mismatching checkpoint.

## Public-checkout complete-backup path

After `local-build prepare`, `bootstrap`, and `acquire`, build the public
read-only recovery inputs before `local-build configure`:

```bash
thingino-dlink local-build recovery-assets --json
```

The result names `kernel`, `linux_config`, and `mmc_module` files beneath one
mode-restricted build run. It also reports an empty `write_set`. This command
builds and validates the source-locked MIPS toolchain, download cache, collector
kernel, effective configuration, and matching MMC module. It does not contact a
camera or stage removable media.

Set the three artifact paths to that successful result's exact values. Choose
a new backup workspace once; its `live-set` and `complete-backup` directories
must not exist yet. Keep it separate from `DCS6100_RECOVERY_ROOT`, which is
for functional recovery, not an exact original backup.

```bash
export DCS6100_COLLECTOR_KERNEL="/path/from/result/collector-kernel.uimage"
export DCS6100_COLLECTOR_CONFIG="/path/from/result/collector-linux.config"
export DCS6100_COLLECTOR_MMC_MODULE="/path/from/result/jzmmc_v12.ko"
export DCS6100_BACKUP_ROOT="$DCS6100_CAMERA_ROOT/original-backup-1"
```

With the camera powered off, insert one FAT32 SD card into the host,
[identify it](../README.md#identify-the-card-each-time), and create the native
removable-media preflight. On macOS:

```bash
python3 scripts/platform/macos_media_preflight.py \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output "$DCS6100_CAMERA_ROOT/media-preflight.json"
```

Stage the read-only collector using those paths and the confirmed card:

```bash
thingino-dlink stock-recovery backup-prepare \
  --kernel "$DCS6100_COLLECTOR_KERNEL" \
  --linux-config "$DCS6100_COLLECTOR_CONFIG" \
  --mmc-module "$DCS6100_COLLECTOR_MMC_MODULE" \
  --output-dir "$DCS6100_BACKUP_ROOT/live-set" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/media-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE"
```

Require the JSON result to report `write_set: []` and
`read-only-collector-staged-unarmed`. Power the camera off before moving the SD
card. Insert it into the matching A1 camera, connect its confirmed UART, start
the capture command while the camera is still off, and only then power it on.
Set the current confirmed serial device once. Do not save it in `install-env.sh`:

```bash
export DCS6100_SERIAL_DEVICE="/dev/cu.usbserial-N"

thingino-dlink stock-recovery backup-capture \
  --input-dir "$DCS6100_BACKUP_ROOT/live-set" \
  --linux-config "$DCS6100_COLLECTOR_CONFIG" \
  --serial-device "$DCS6100_SERIAL_DEVICE" \
  --confirm-serial-device "$DCS6100_SERIAL_DEVICE"
```

The editable package installs the pinned `pyserial` dependency required by this
command. The RAM collector marks all six physical partitions read-only, reads
each partition twice, performs SD storage readback, retains the allowlisted
stock-mtd3 vendor libraries, and halts. It never authorizes a NOR write. After a
reported completion, power the camera off, return the card to the host,
identify it again, and finalize into a new private recovery directory:

```bash
thingino-dlink stock-recovery backup-validate \
  --collector-dir "$DCS6100_SD_MOUNT/DCS6100B" \
  --output-dir "$DCS6100_BACKUP_ROOT/complete-backup" \
  --confirm-output-dir "$DCS6100_BACKUP_ROOT/complete-backup"
```

Stop unless duplicate partition sets, both reconstructed 16 MiB images, and all
storage readbacks pass. Keep the output private and never substitute another
camera's backup. The vendor bundle and protected readback must pass their own
documented producers and validators. A private media closure and reviewed
Raptor artifact are optional advanced inputs, not prerequisites for the
matched-media universal build.

## UARTless functional-capture alternative

`local-build recovery-assets` also produces an all-MTD-read-only mtd2-boot
kernel, a minimal collector root, and an exact stock-U-Boot package. This path
is UARTless after SD staging, but it is not write-free: the first camera boot
erases and writes physical mtd1 and mtd2. The accepted originals are therefore
mtd0, mtd3, mtd4, and mtd5. The captured mtd1/mtd2 must instead equal the exact
replacement images reconstructed from the authorized package.

Set `DCS6100_CAPTURE_PACKAGE` and `DCS6100_CAPTURE_MANIFEST` from the
successful `recovery-assets` result as in the README. With the camera off,
insert and identify the card, then generate a fresh preflight:

```bash
python3 scripts/platform/macos_media_preflight.py \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output "$DCS6100_CAMERA_ROOT/capture-media-preflight.json"
```

Stage the package under a non-matching inert name:

```bash
thingino-dlink stock-recovery uartless-prepare \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-media-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE"
```

The result must report `armed: false`, `original_complete_backup_accepted:
false`, `original_preserved_mtd: [0,3,4,5]`, and the future write set `[1,2]`.
Activation is a separate exact confirmation:

```bash
thingino-dlink stock-recovery uartless-authorize \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-media-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-write-set WRITE-MTD1-MTD2
```

Only `uartless-authorize` renames the reviewed package to the single filename
matched by stock U-Boot. With the camera off, move the card to the camera and
power it on once. This updater pass writes mtd1/mtd2 and enters its completion
state; it does not yet run the collector. Power off, return the card to the
host, identify it again, regenerate the removable-media preflight, and make
the selector inert:

```bash
python3 scripts/platform/macos_media_preflight.py \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --output "$DCS6100_CAMERA_ROOT/capture-handoff-preflight.json"

thingino-dlink stock-recovery uartless-handoff \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_CAPTURE_MANIFEST" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --media-preflight "$DCS6100_CAMERA_ROOT/capture-handoff-preflight.json" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-stock-uboot-result MTD1-MTD2-WRITTEN
```

Move the passive card back to the powered-off camera and boot normally. The new
mtd1 kernel boots the minimal mtd2 collector without UART, enforces every mtd0
through mtd5 partition read-only, captures each current partition twice to
`DCS6100F`, performs SD readback, and halts. Power off, return the card to the
host, identify it again, and validate into a new private schema-3 functional
recovery directory:

```bash
thingino-dlink stock-recovery uartless-validate \
  --collector-dir "$DCS6100_SD_MOUNT/DCS6100F" \
  --package "$DCS6100_CAPTURE_PACKAGE" \
  --output-dir "$DCS6100_RECOVERY_ROOT" \
  --confirm-output-dir "$DCS6100_RECOVERY_ROOT"
```

This result may report `functional_recovery_accepted: true` only after duplicate
reads, both 16 MiB functional reconstructions, mtd1/mtd2 replacement identity,
the read-only preserved-partition readback, the camera vendor bundle, and all
storage readbacks pass. The private result contains `vendor/` for
`local-build build-universal` and `preserved/` for the later same-device gate.
It always reports `original_complete_backup_accepted: false`.

After the model-universal build, bridge this functional recovery to the
camera-local provisioning flow without inventing a recovery-AP session:

```bash
thingino-dlink universal init-session \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --output-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --config-output-dir "$DCS6100_CAMERA_ROOT/install-config"
```

The generated session is bound to the functional recovery identity and has no
camera transport. Continue with the exact `universal configure` command printed
by the installer; enter Wi-Fi values only through its hidden prompts.

Functional recovery is accepted only through explicitly functional-aware CLI
arguments. For example, later media staging uses:

```bash
thingino-dlink prepare-card \
  --whole-device "$DCS6100_SD_DEVICE" \
  --mount-root "$DCS6100_SD_MOUNT" \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-target DCS-6100LHV2-A1
```

The exact `--recovery-dir` and functional `--functional-recovery-dir` arguments
are mutually exclusive. Functional-aware results keep
`full_flash_reconstruction_accepted: false` while reporting their separate
functional acceptance; schema 3 never enters the exact same-device stock
restore port. The initial restoration class is `recovery-functional`; it
restores the reviewed recovery state, not D-Link stock behavior. A future
`stock-functional` claim requires a separately cataloged and physically
accepted D-Link kernel/rootfs restoration pair.

## Files acquired from the camera

For a stock `DCS-6100LHV2` revision `A1`, the acquisition step mounts mtd3
read-only and copies `/lib/libimp.so`, `/lib/libalog.so`,
`/lib/libsysutils.so`, `/lib/libaudioProcess.so`,
`/lib/modules/tx-isp-t31.ko`, `/lib/modules/sensor_os02g10_t31.ko`, and
`/etc/sensor/os02g10-t31.bin`. The installer validates the file names,
sizes, SHA-256 values and MIPS ABI against
`profiles/dlink-dcs6100lhv2-a1/vendor-closure.json`, then writes the accepted
metadata to `vendor-bundle.private.json`.

For the model-universal build, these catalog-locked bytes are model inputs and
must normalize identically across compatible A1 acquisitions. Camera-specific
station details, management/API credentials, and SSH material belong only to
the separate provisioning sidecar. The legacy personalized build still embeds
those values and must not be reused on another camera.

## Reusing one firmware on multiple cameras

Build `thingino-universal.tgb` once. For camera A and camera B, independently:

1. capture and validate that camera's own recovery and preserved readback;
2. create its private audit sidecar and exact-span JFFS2 overlay image;
3. create its signed camera authorization; and
4. verify that both authorizations name the same universal firmware SHA-256
   while their camera, session, sidecar, data, and authorization identities
   differ.

The authorization also contains a fixed 256-byte `INSTALL.AUTH.BIN`. Its HMAC
key is derived independently inside the host recovery gate and Stage 1 from the
same camera's complete preserved mtd0/mtd4/mtd5 bytes; the key is not printed or
written into the authorization. Before any final write, Stage 1 loads exact
stage 2 and the full 1,507,328-byte `THINGINO.PROVISION` JFFS2 into bounded RAM,
then verifies camera identity, HMAC, compiled stage-2 SHA-256, `initialize`, and
the data image SHA-256. A sidecar, data image, or authorization swapped between
cameras therefore fails before bootstrap removal and before NOR erase.

The immutable universal root initially has no usable private credential or
network startup. Private values are not members of the signed universal bundle.
Create the per-camera configuration with `universal configure`; the command
accepts Wi-Fi values only through hidden prompts or an inherited secrets file
descriptor and binds the generated credentials to that recovery session.
After authorization, Stage 1 commits a read-back stock-mtd3 backup and
checkpoint, writes and verifies the complete private data overlay, writes the
system and kernel tail, and writes the final kernel activation eraseblock last.
It removes the active bootstrap before writes and passivates the per-camera card
files after verified activation. This provides crash/retry and one-active-card
transaction semantics; it is not clone-resistant anti-replay.

The complete physical write declaration is stock-updater mtd1+mtd2, followed by
Stage-1 final mtd1 and physical mtd3. Physical mtd0/mtd4/mtd5 remain preserved.
The SD transaction also creates `STOCKM3.BIN` and `STOCKM3.OK` and consumes its
active selector and camera files. No staging operation may hide any part of
that declaration. The host lifecycle has two explicit SD mutations:
`universal stage` validates the full tuple and activates the stock updater; after
that updater reports verified mtd1+mtd2 completion and the powered-off card is
returned to the host, `universal handoff` revalidates the full tuple and live
media identity before making the updater inert. Stage 1 requires that handoff
and will not run while the stock-matching bootstrap remains active.

Installation, installed-artifact verification, main/substream WebRTC and
selected controls have passed on one A1 camera. Management verification allows
five minutes for startup. See [test coverage](status.md#tested-installation)
for the validated scope. Physical interruption, wider provisioning acceptance
and second-camera acceptance remain open release gates.

## Data actions

The legacy personalized install set binds exactly one mtd3 data action:

- `initialize` creates the first private stock-mtd3 backup and checkpoint,
  installs the fixed system, and erases the new data region;
- `preserve` installs the fixed system while requiring the complete data region
  to remain byte-identical; or
- `factory-reset` installs the fixed system and explicitly erases only the data
  region.

No mode silently repairs or reformats corrupt JFFS2 data.

## Write boundaries

- Never write stock physical mtd0, mtd4, or mtd5.
- In the installer kernel, logical `/dev/mtd4` is the data region inside stock
  physical mtd3; the preserved stock physical mtd4 and mtd5 appear as
  `/dev/mtd5` and `/dev/mtd6`. See [hardware.md](hardware.md#nor-layout).
- Never use a generic full-flash or Web Flasher image.
- Reject an unknown camera, partition map, SD card, artifact identity, size,
  or stale manifest.
- Require readback before activation.
- Keep the previous installer set where the format permits it.
- Stop if the source checkout, private inputs, or recovery binding differs from
  the candidate.

The CLI exposes the implemented development commands through
`python3 -m installer.user_cli --help` and `thingino-dlink --help`.
The separate `dcs6100-thingino` entry point and `python3 -m installer` module
are the lower-level artifact CLI and do not contain `local-build` or `universal`. Command presence does
not make an artifact public or authorize a write.

## Local install set and supported host systems

The legacy personalized firmware build and its device-specific inputs stay on
your computer. After `python3 -m installer inspect-install-set` accepts the
exact local output, stage that legacy development set on macOS, Linux, or
Windows with `thingino-dlink stage-install-set`. This command is not the
model-universal staging path. It validates the install set,
requires the expected `initialize`, `preserve`, or `factory-reset` data action,
requires `--recovery-dir` and `--preserved-readback-dir` to pass before media
staging,
performs the native removable-media preflight, and uses the common
temporary-write, readback, and activation-last stager.

The native adapters use:

- macOS `diskutil` for an exact external physical whole disk and FAT32 volume;
- Linux `lsblk`, `findmnt`, and `udevadm` for an exact removable whole disk and
  directly mounted FAT32 partition; and
- Windows PowerShell Storage cmdlets for an exact physical disk number and
  FAT32 drive root.

You must still provide the exact whole-device ID twice and confirm
`DCS-6100LHV2-A1`. A host-contract test does not prove a card reader, SD card,
camera, interrupted-write recovery, or post-install operation. One private
macOS card workflow completed with storage readback, but other readers,
card formats, Linux, Windows, and interruption cases remain open until
separately recorded.
