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
RAM transport until a non-writing bootstrap is found and physically accepted.
The stock SD updater cannot close this gap because selecting its package erases
and writes mtd1/mtd2 before the collector could run.

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

## Files acquired from the camera

For a stock `DCS-6100LHV2` revision `A1`, the acquisition step mounts mtd3
read-only and copies `/lib/libimp.so`, `/lib/libalog.so`, and
`/lib/libsysutils.so`. It also retains `/lib/libaudioProcess.so` when the
optional catalog identity matches. The installer validates the file names,
sizes, SHA-256 values and MIPS ABI against
`profiles/dlink-dcs6100lhv2-a1/vendor-closure.json`, then writes the accepted
metadata to `vendor-bundle.private.json`.

During setup, you provide the station-network details. The installer generates
management and API credentials locally for that installation. You select the
SSH public key.

## Data actions

The final install set binds exactly one mtd3 data action:

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

The firmware build and its device-specific inputs stay on your computer. After
`python3 -m installer inspect-install-set` accepts the exact
local output, stage it on macOS, Linux, or Windows with
`thingino-dlink stage-install-set`. The command validates the install set,
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
