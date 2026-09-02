# Authorization boundaries

Repository inspection, source review, and host-only checks do not authorize an
external or physical action. Confirm the current request immediately before
each of these boundaries:

- downloading or publishing material when the user limited the work to local
  inspection;
- preparing, changing, passivating, or ejecting removable media;
- powering, resetting, rebooting, or connecting to a camera;
- passive UART capture or any interactive U-Boot command;
- creating a volatile runtime candidate on a camera; and
- erasing, writing, or activating NOR content.

Use the repository CLI for every permitted physical workflow. Never issue raw
`flashcp`, `dd`, `mtd`, `nandwrite`, `sf`, or U-Boot write commands as a
shortcut. Never write stock physical mtd0, mtd4, or mtd5. Reject an unknown
camera revision, flash layout, physical media identity, artifact digest, size,
recovery binding, or write declaration.

Universal host artifacts are not installation authorization. Physical
provisioning, interruption, and second-camera release gates may remain open;
report them without presenting host success as device proof. When the user has
explicitly authorized changing the exact SD card, use only `universal stage`
and `universal handoff` for the universal path. Require all recovery, camera,
media, full write-set, and stock-result confirmations. Handoff is allowed only
after the stock updater's mtd1+mtd2 write and readback have been confirmed. If
either guided command is absent in the checked-out revision, stop instead of
inventing a staging path.

Require readback before activation. Treat a terminal stop decision as final
until the user authorizes a separately bounded diagnostic step or new evidence
changes the preflight result. Do not repeat a destructive restore merely to
produce another log.

Keep credentials, private keys, Wi-Fi settings, camera-derived bindings,
provisioning archives, and data images private. Do not place them in Git,
fixtures, chat, command arguments that will be logged, or public build output.
Provisioning inputs must retain the modes and hardlink constraints enforced by
the CLI.
