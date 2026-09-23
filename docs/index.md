# Documentation

Start with the [README installation guide](../README.md#install-from-a-public-checkout).
It contains the complete UARTless universal command sequence, including host
setup, functional recovery, firmware selection, private configuration, SD
handoffs, verification, and troubleshooting. The pages below explain the
contracts and the supported workflow.

- [Status](status.md) lists what is proven and what blocks a public release.
- [Features](features.md) separates implemented features from their validation
  level.
- [Hardware](hardware.md) defines the only supported board and flash layout.
- [Architecture](architecture.md) explains Control, the full Raptor media stack,
  uhttpd, and the WebUI.
- [API](api.md) summarizes the stable Thingino Control v1 boundary.
- [Build](build.md) covers source preparation, private inputs, and build gates.
- [Installation](installation.md) defines the installation boundary and flow.
- [Recovery](recovery.md) covers required backups, the restore flow, and its
  remaining evidence gates.
- [Testing](testing.md) lists host, build, browser, and device evidence levels.

Third-party source and redistribution notes are in
[`third_party/NOTICE.md`](../third_party/NOTICE.md). The exact unresolved
license evidence is in
[`third_party/LICENSE_REVIEW.md`](../third_party/LICENSE_REVIEW.md).
