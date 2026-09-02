# Recovery

Recovery is device-specific. A backup from one camera must never be used on
another camera.

## Required private recovery material

Before its first persistent write, each camera needs two independently read
and byte-identical mtd0-mtd5 partition sets, storage readback for every file,
and two byte-identical reconstructed 16 MiB full-flash images. A private
manifest binds the exact A1 layout, sizes, identities, reconstructions, and
checks. You must also verify the current physical mtd0, mtd4, and mtd5 against
that set before installation.

Keep stock physical mtd5 and every per-device hash private. Do not commit
backups, partition files, manifests, MAC addresses, serial numbers,
credentials, or UART logs.

The optional UARTless functional-capture path is a different recovery class.
It deliberately lets stock U-Boot replace mtd1/mtd2 before capture, preserves
original mtd0/mtd3/mtd4/mtd5, and validates the captured mtd1/mtd2 against the
exact replacement package. Its schema-3 manifest must state
`original_complete_backup_accepted: false`. The initial
`recovery-functional` source can restore the reviewed collector/recovery state;
it is not a D-Link stock restoration source. Never pass this schema to the
exact same-device stock-restorer. A D-Link-functional restore requires a
separately cataloged kernel/rootfs pair and physical acceptance.

## Current split-layout behavior

The permanent mtd2 is a verifier and direct Thingino handoff. It does not run
the older network recovery AP. Holding reset does not open a setup AP on this
layout. The development camera confirmed that a long reset has no recovery-AP
effect.

Recovery currently relies on the exact stock-U-Boot SD install set bound to
the candidate. This is a recovery path for the Thingino kernel, bootstrap, and
mtd3 installation.

The source also contains a same-device D-Link 1.02.02 restorer whose default
host state is prepare-only and unarmed.
Its mtd2 validator accepts only a bounded SquashFS with an erased tail, a
zero-filled tail, or zero padding to the next 4 KiB boundary followed by an
erased tail; other trailing bytes fail closed.
The saved mtd3 must retain its exact same-device identity and begin with an
exact JFFS2 cleanmarker using the MTD CRC convention.
Its UART-free transport validates a dedicated six-partition kernel, builds an
inert strict mtd1/mtd2 stock-U-Boot package around the bounded restorer, and
activates the sole matching filename only after an exact media and write-set
confirmation. On macOS FAT media, staging removes only newly created
AppleDouble sidecars for the reserved restore paths; authorization and selector
activation use the same transaction rule, as does the active-to-inert handoff,
and preserve unrelated sidecars. The restorer compares mtd0, mtd4, and mtd5 with
the selected
camera's private backup, opens only mtd1, mtd2, and mtd3 for mutation, restores
mtd3 then mtd2 then the mtd1 tail, and writes the first mtd1 erase block last.
Every final partition is read back before `RESTORE.OK` is written. Before it
can erase its own mtd2 backing partition, the freestanding MIPS PID 1 must
successfully lock all current and future mappings into RAM; failure is a hard
stop.
`RESTORE.RUN` is atomically activated and read back before the one-use
`RESTORE.GO` file is removed, so an interrupted status write cannot open the
NOR write phase without a durable run marker.

Stock U-Boot stops in its completion LED loop. The card must return to the
host for `sd-handoff`, which verifies the complete set and makes the update
filename inert before the next boot. Only that next boot enters the restorer.
An interrupted run requires `sd-retry`, another updater pass, and another
handoff; leaving the selector active would only repeat the updater forever.

After completion, `thingino-dlink stock-recovery sd-passivate` requires the
exact private prepared set, card preflight, `RESTORE.RUN`, and `RESTORE.OK`.
It moves the active package and restore files into `RESTORE.PSV`, verifies the
card readback, removes only transaction-created AppleDouble sidecars, writes
and reads back `PASSIVE.OK` last, and preserves unrelated card files.
Stock U-Boot's update message is not readback evidence. This UART-free path has
one same-device physical acceptance run: the bounded restorer completed
mtd3/mtd2/mtd1 physical readbacks with activation last, the card was passivated,
and D-Link 1.02.02 reached stable setup mode. Physical interruption, retry, and
second-camera evidence remain open.

## Failure policy

Stop before writing if:

- camera identity or flash geometry is unknown;
- the SD card is internal, ambiguous, or not the confirmed physical device;
- backup or checkpoint size, digest, or source does not match;
- stock physical mtd0, mtd4, or mtd5 differs from the bound private recovery
  set;
- the final kernel, system, data action, or manifest differs from the
  candidate; or
- a previous recovery attempt returned a terminal stop decision.

Host fake-NOR interruption tests do not prove physical power-loss recovery.
The final public release requires recorded physical interruption, corruption,
reinstall, and second-camera results using only the published installation and
recovery workflow.
