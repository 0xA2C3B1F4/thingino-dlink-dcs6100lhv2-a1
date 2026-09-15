# Hardware contract

The only supported target is D-Link `DCS-6100LHV2`, hardware revision `A1`.

| Item | Fixed value |
| --- | --- |
| SoC | Ingenic T31N, MIPS32, 64 MiB physical RAM |
| Sensor | OS02G10, 1920x1080 |
| Wi-Fi | RTL8188FU, GPIO57 power |
| Linux memory | `mem=42M@0x0` |
| ISP reserved memory | `rmem=22M@0x2a00000` |
| IR-cut | GPIO50 then GPIO49, 100 ms latching pulse |
| Status LEDs | GPIO52 green and GPIO54 red, active low |
| Reset input | GPIO60 |
| 850 nm IR LED | GPIO61 |
| Speaker enable | GPIO63 |
| Sensor reset | GPIO18 |

The exact memory split was selected after camera testing with both encoders,
MJPEG, snapshots, RTSP, OSD, Motion, day/night, audio, recording, and WebRTC.
Changing it creates a new hardware candidate.

## NOR layout

The physical NOR is 16 MiB with six stock partitions. The repository profile
is the machine-readable authority for offsets and limits.

| Stock physical partition | Role | Normal policy |
| --- | --- | --- |
| mtd0 | Stock bootloader and environment | Never write |
| mtd1 | Kernel | Write only through the reviewed installer |
| mtd2 | Bootstrap and verifier | Write only through the reviewed installer |
| mtd3 | Thingino system and persistent data | Fixed bounded regions with readback |
| mtd4 | Stock vendor data | Preserve, never write |
| mtd5 | Per-device factory configuration | Preserve and keep private, never write |

The current split reserves 6,619,136 bytes for the read-only SquashFS system
and 1,507,328 bytes for JFFS2 data. The boundary is fixed at a 64 KiB boundary.
The installer must reject an oversized system image instead of moving the data
region.

The installer and final kernels split stock physical mtd3 into two logical
devices. This shifts the two preserved stock partitions to higher logical
numbers:

| Kernel device | On-flash region | Policy |
| --- | --- | --- |
| `/dev/mtd0` | stock physical mtd0 | preserve |
| `/dev/mtd1` | stock physical mtd1 | reviewed kernel write only |
| `/dev/mtd2` | stock physical mtd2 | reviewed bootstrap write only |
| `/dev/mtd3` | 6,619,136-byte `system` inside stock physical mtd3 | bounded write and readback |
| `/dev/mtd4` | 1,507,328-byte `data` inside stock physical mtd3 | explicit preserve, initialize, or factory-reset action |
| `/dev/mtd5` | stock physical mtd4 vendor data | preserve, never write |
| `/dev/mtd6` | stock physical mtd5 factory data | preserve and keep private, never write |

Installer logical `/dev/mtd4` is therefore the data region inside stock
physical mtd3. It is not stock physical mtd4.

Generic Thingino full-flash images, Web Flasher images, and any package for a
different revision are incompatible. Unknown identity or geometry must fail
before a write.
