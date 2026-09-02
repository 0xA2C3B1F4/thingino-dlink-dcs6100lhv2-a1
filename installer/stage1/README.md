# Fixed offline stage 1

Stage 1 is a 32-bit MIPS freestanding PID 1 and one reviewed MMC module in a
small read-only SquashFS. It has no libc, shell, network stack, Web UI, SSH,
Telnet, or arbitrary command/MTD interface.

The host binds one exact `THINGINO2.BIN` size and SHA-256, the final kernel and
system hashes, the MMC module hash, and the fixed partition ABI into the
PID 1 binary. The stock-U-Boot-compatible bootstrap writes only physical mtd1
and mtd2. Its installer kernel exposes this logical map:

- boot, bootstrap, vendor, and factory are read-only;
- kernel and system are writable only during the installer boot;
- data is writable;
- an unexpected eighth MTD or any wrong size/type/erase geometry fails closed.

The physical 8,126,464-byte mtd3 region is permanently divided at a 64 KiB
boundary into a 6,619,136-byte read-only SquashFS `system` region and a
1,507,328-byte JFFS2 `data` region. The split is not derived from the current
SquashFS length. A system image that exceeds its fixed region fails the host
build instead of moving the persistent data boundary.

On the permanent boot path, the verifier mounts the hash-checked system region
itself and passes `THINGINO_DLINK_VERIFIED_MTD_ROOT=1` only to Thingino
`/init`. The init script consumes that mark before mounting the fixed JFFS2
data region and OverlayFS. Generic NFS, MMC, and RAM boots still stop at the
normal non-MTD guard instead of falling through to the persistent overlay.

The XM25QH128C MTD driver reports a 32 KiB physical erase size and a 256-byte
write size. The installer erases one physical sector at a time, writes complete
pages, pads only the final partial page with erased `0xff`, and retains a
conservative 64 KiB final-kernel activation span. The MIPS O32 `MEMERASE`
request uses the architecture's `0x80084d02` ioctl value.

PID 1 mounts the FAT card and verifies the exact stage-2 file. Before any final
write, it requires the compiled stock-matching bootstrap pathname to be absent
or removes only that exact path and syncs FAT. The normal stock flow stops after
flashing the bootstrap, so the host deactivates the verified matching name
before rebooting into stage 1. An already absent path is therefore an expected,
explicitly handled state rather than an installer failure.
For an `initialize` install, PID 1 exports the original physical stock mtd3 as
private `STOCKM3.BIN` and verifies that backup against NOR before the first
final write. On a pre-write retry, it may reuse an existing file only after
comparing its complete system and data regions against the still-current NOR
and proving exact end-of-file.
A partial or oversized backup is terminal. A complete backup that differs from
current NOR is terminal unless the exact recovery checkpoint already exists;
this does not recover an installation interrupted before that checkpoint.

After proving that `STOCKM3.BIN` exactly matches current NOR, PID 1 writes and
reads back fixed 80-byte `STOCKM3.OK`. The record binds its magic/version, the
backup size and SHA-256, and the compiled stage-2 size and SHA-256. On a later
boot where NOR no longer matches the backup, only an exact backup/checkpoint
pair permits re-running that same bounded final install. The record contains no
command, path, partition, offset, or replacement digest supplied by the card.
It is a consistency/corruption gate, not authentication against malicious
removable media, and it does not restore stock firmware.

The stage-2 header binds exactly one data action. `initialize` creates the
private backup/checkpoint and erases the new data region. `preserve` hashes the
complete data region before writing system and requires an identical complete
readback afterward. `factory-reset` is an explicit data-only erase combined
with the bound system/kernel installation. No mode silently reformats a corrupt
JFFS2 region. PID 1 writes and verifies system, writes and verifies the kernel
tail, and writes the first 64 KiB activation block last.

The host fake-NOR model uses the same reported 32 KiB physical erase sectors
and 256-byte write pages as PID 1. Its interruption tests stop after selected
first, middle, and final sector/page operations, prove `preserve` leaves data
unchanged, and prove `initialize` can resume with the exact SD backup and
checkpoint. This is stronger host modeling, not execution of the MIPS PID 1 or
physical power-loss evidence.

After mounting sysfs, PID 1 requires the board's active-low green GPIO52 and red
GPIO54 status LEDs through the kernel LED class. It writes and reads back
`/sys/class/leds/led_g/brightness` and `led_r/brightness`; generic GPIO export
is deliberately not used because the `gpio-leds` driver owns both pins. Green
identifies validation and backup, green+red identifies the final flash-write
interval, terminal failure leaves red on, and successful activation/readback
returns to green before reboot. Failure of the LED gate itself is terminal
before installer-mode writes.

The LED-class interface completed one live v12 final write and later passed
again from the permanent mtd2 verifier. Earlier v10 and v11 roots failed closed
before final writes at GPIO direction and GPIO value respectively. Formal
human-observed correlation of every color and failure phase is still required
before calling the protocol a closed UART-independent feedback gate.

The final kernel uses the same map with kernel, bootstrap, and system marked
read-only. The permanent mtd2 PID 1 verifies the exact final kernel and system,
mounts logical mtd3, and replaces itself with Thingino `/init`. Thingino `/init`
mounts logical `data` as JFFS2 at `/overlay`, mounts it as the OverlayFS upper
over the SquashFS root, pivots into the merged root, and keeps `/overlay`
available for diagnostics and explicit reset. Time, HA/MQTT, Prudynt, password,
and other normal `/etc` writes therefore copy up to persistent JFFS2. The SD
card is not needed after successful installation.

`build.py` constructs the two-file install set atomically in a private output
directory. The predecessor two-stage path completed one authorized private
persistent install on an A1, including final readbacks and a clean reboot. The
fixed split, data modes, persistent overlay, and fail-closed corruption policy
described here are newer and remain host-tested until their separate device
matrix passes. Neither result makes a public release: the final Thingino root
must remain reproducible from reviewed inputs with unique private credentials,
licensing must be closed, and interrupted-write plus recovery behavior must be
recorded before public media staging is supported.
