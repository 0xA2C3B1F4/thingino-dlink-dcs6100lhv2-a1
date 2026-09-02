# Installation

There is no supported public installation package yet. Do not use generated
development artifacts on a camera unless the exact candidate and operation
have separate authorization.

The source can build and stage a fixed read-only RAM collector that captures
all original mtd0-mtd5 partitions twice before any stock updater runs. Host
validation and both 16 MiB reconstructions must pass before any dependent
persistent-write phase opens. One private UART-assisted RAM-collector run has
completed this capture and host validation, but source availability alone is
not device proof and the unopened-camera transport remains a release gate.

There is still no proven automatic stock-U-Boot SD-to-RAM collector boot that
leaves all six original partitions unchanged. The complete-backup source is
implemented, but a new unopened camera needs the separately authorized UART
RAM transport for a byte-exact original six-partition backup. An explicit
UARTless functional-capture alternative uses the stock SD updater and therefore
erases and writes mtd1/mtd2 before the collector runs. It preserves original
mtd0, mtd3, mtd4, and mtd5 and must never report an original complete backup.
This alternative remains a development candidate until its physical write,
handoff, duplicate capture, retry, and functional restore have been accepted.

## Intended flow

The finished installation workflow is:

1. verify the exact model, A1 revision, NOR geometry, and stock source state;
2. capture and validate that camera's complete duplicate private recovery set;
3. verify the current read-only mtd0/mtd4/mtd5 same-device binding;
4. build from pinned public source and owner-acquired private inputs;
5. validate every artifact, partition limit, dependency, credential role, and
   manifest;
6. identify the exact external SD card and preserve unrelated files when the
   existing filesystem is usable;
7. stage the two-file stock-U-Boot install set through temporary names and
   verify it by reading the card back;
8. boot the card and let the bounded bootstrap install the permanent verifier;
9. remove or deactivate only the exact stock-matching bootstrap name;
10. boot stage 1, which validates the final install set before any final write;
11. write and read back only the authorized kernel, bootstrap, and stock
    physical mtd3 system/data regions, with activation last; and
12. boot Thingino, discover it on the station network, and run management and
    media acceptance separately.

The SD card is not required after a successful persistent installation.

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

With the camera powered off, insert one FAT32 SD card into the host and create
the native removable-media preflight. On macOS:

```bash
python3 scripts/platform/macos_media_preflight.py \
  --whole-device /dev/diskN \
  --mount-root /path/to/mounted-card \
  --output /path/to/private/media-preflight.json
```

Use only the three paths printed by `recovery-assets`, repeat the exact physical
device, and choose new private output directories:

```bash
thingino-dlink stock-recovery backup-prepare \
  --kernel /path/from/result/collector-kernel.uimage \
  --linux-config /path/from/result/collector-linux.config \
  --mmc-module /path/from/result/jzmmc_v12.ko \
  --output-dir /path/to/private/live-set \
  --mount-root /path/to/mounted-card \
  --media-preflight /path/to/private/media-preflight.json \
  --confirm-physical-device /dev/diskN
```

Require the JSON result to report `write_set: []` and
`read-only-collector-staged-unarmed`. Power the camera off before moving the SD
card. Insert it into the matching A1 camera, connect its confirmed UART, start
the capture command while the camera is still off, and only then power it on:

```bash
thingino-dlink stock-recovery backup-capture \
  --input-dir /path/to/private/live-set \
  --linux-config /path/from/result/collector-linux.config \
  --serial-device /dev/cu.usbserial-N \
  --confirm-serial-device /dev/cu.usbserial-N
```

The editable package installs the pinned `pyserial` dependency required by this
command. The RAM collector marks all six physical partitions read-only, reads
each partition twice, performs SD storage readback, retains the allowlisted
stock-mtd3 vendor libraries, and halts. It never authorizes a NOR write. After a
reported completion, power the camera off, return the card to the host, and
finalize into a new private recovery directory:

```bash
thingino-dlink stock-recovery backup-validate \
  --collector-dir /path/to/mounted-card/DCS6100B \
  --output-dir /path/to/private/complete-backup \
  --confirm-output-dir /path/to/private/complete-backup
```

Stop unless duplicate partition sets, both reconstructed 16 MiB images, and all
storage readbacks pass. Keep the output private and never substitute another
camera's backup. The remaining vendor-bundle, protected-readback, media-closure,
and Raptor inputs must still pass their own documented producers and validators
before `local-build configure`.

## UARTless functional-capture alternative

`local-build recovery-assets` also produces an all-MTD-read-only mtd2-boot
kernel, a minimal collector root, and an exact stock-U-Boot package. This path
is UARTless after SD staging, but it is not write-free: the first camera boot
erases and writes physical mtd1 and mtd2. The accepted originals are therefore
mtd0, mtd3, mtd4, and mtd5. The captured mtd1/mtd2 must instead equal the exact
replacement images reconstructed from the authorized package.

Stage the package under a non-matching inert name:

```bash
thingino-dlink stock-recovery uartless-prepare \
  --package /path/from/result/uartless-capture-bootstrap.bin \
  --package-manifest /path/from/result/uartless-capture-bootstrap.manifest.json \
  --mount-root /path/to/mounted-card \
  --media-preflight /path/to/private/media-preflight.json \
  --confirm-physical-device /dev/diskN
```

The result must report `armed: false`, `original_complete_backup_accepted:
false`, `original_preserved_mtd: [0,3,4,5]`, and the future write set `[1,2]`.
Activation is a separate exact confirmation:

```bash
thingino-dlink stock-recovery uartless-authorize \
  --package /path/from/result/uartless-capture-bootstrap.bin \
  --package-manifest /path/from/result/uartless-capture-bootstrap.manifest.json \
  --mount-root /path/to/mounted-card \
  --media-preflight /path/to/private/media-preflight.json \
  --confirm-physical-device /dev/diskN \
  --confirm-write-set WRITE-MTD1-MTD2
```

Only `uartless-authorize` renames the reviewed package to the single filename
matched by stock U-Boot. With the camera off, move the card to the camera and
power it on once. This updater pass writes mtd1/mtd2 and enters its completion
state; it does not yet run the collector. Power off, return the card to the
host, regenerate the removable-media preflight, and make the selector inert:

```bash
thingino-dlink stock-recovery uartless-handoff \
  --package /path/from/result/uartless-capture-bootstrap.bin \
  --package-manifest /path/from/result/uartless-capture-bootstrap.manifest.json \
  --mount-root /path/to/mounted-card \
  --media-preflight /path/to/private/new-media-preflight.json \
  --confirm-physical-device /dev/diskN \
  --confirm-stock-uboot-result MTD1-MTD2-WRITTEN
```

Move the passive card back to the powered-off camera and boot normally. The new
mtd1 kernel boots the minimal mtd2 collector without UART, enforces every mtd0
through mtd5 partition read-only, captures each current partition twice to
`DCS6100F`, performs SD readback, and halts. Return the card to the host and
validate into a new private schema-3 functional recovery directory:

```bash
thingino-dlink stock-recovery uartless-validate \
  --collector-dir /path/to/mounted-card/DCS6100F \
  --package /path/from/result/uartless-capture-bootstrap.bin \
  --output-dir /path/to/private/functional-recovery \
  --confirm-output-dir /path/to/private/functional-recovery
```

This result may report `functional_recovery_accepted: true` only after duplicate
reads, both 16 MiB functional reconstructions, mtd1/mtd2 replacement identity,
the read-only preserved-partition readback, the camera vendor bundle, and all
storage readbacks pass. The private result contains `vendor/` for
`local-build configure` and `preserved/` for the later same-device gate.
It always reports `original_complete_backup_accepted: false`.

Functional recovery is accepted only through explicitly functional-aware CLI
arguments. For example, later media staging uses:

```bash
thingino-dlink prepare-card \
  --whole-device /dev/diskN \
  --mount-root /path/to/mounted-card \
  --functional-recovery-dir /path/to/private/functional-recovery \
  --preserved-readback-dir /path/to/private/functional-recovery/preserved \
  --confirm-physical-device /dev/diskN \
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
read-only and copies `/lib/libimp.so`, `/lib/libalog.so`, and
`/lib/libsysutils.so`. It also retains `/lib/libaudioProcess.so` when the
optional catalog identity matches. The installer validates the file names,
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
that declaration. Physical interruption, successful provisioned boot, and
second-camera acceptance remain open release gates, so no guided universal
staging command is exposed yet.

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
`python3 -m installer --help` and `dcs6100-thingino --help`. Their presence does
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
