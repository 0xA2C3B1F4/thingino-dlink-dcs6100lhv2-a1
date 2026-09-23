# September 23 Raptor candidate on the first A1 camera

This record applies only to firmware source
`22989b85e327b090116cf7884a2e616055e24454c` and its 7,958,609-byte
signed universal bundle, SHA-256
`416e94a8125aa4758d8cdea2ee91ab5543fc955ea54f41c35c7121edf125a23c`.
The target was the first DCS-6100LHV2 A1 camera. It is a bounded installation
and browser result, not public firmware-release acceptance.

## Build and installation

Two clean `initialize` builds without Raptor component-cache reuse produced
16 byte-identical complete-firmware artifact pairs. An independent host pass
rehashed all 32 files and inspected both install sets. These build results were
recorded in [release status](../status.md) before the camera work.

The supported universal path staged the camera-specific SD files. On the first
card boot, the operator observed familiar blinking, but no direct UART success
line was captured. A subsequent boot without the card
reached `STAGE1 booted` and `device_verified`, then stopped at the expected
`FAIL sd_partition_missing`. This sequence alone does not prove that the stock
updater completed. The still-active card was booted again; the operator observed
regular red blinking, but the passive UART listener started too late to capture
the stock updater's success line. The active selector was then passivated
through the supported universal handoff. Its camera-bound host plan and full
receipt are retained in private evidence.

The next card boot produced UART stage records for `sd_mounted`,
`stage2_snapshotted_and_verified`,
`camera_authorization_and_provisioning_snapshotted`,
`recovery_checkpoint_committed`, `stock_userdata_backup_committed`,
`final_write_phase`, `provisioning_data_written_and_verified`,
`final_system_written`, `final_kernel_tail_written`,
`preactivation_kernel_verified`, `activation_written_and_verified`,
`camera_install_files_passivated`, `sd_unmounted`, and `rebooting_final`.
The following boot reached `thingino_verified_switch_root`.

The supported universal verifier passed application, Control, health, media,
and WebUI checks on the pinned station. It reported system partition
SHA-256 `3c73cd753eab1be4e9df7a57154833fb601513c76eee8740749c51ec8426a0b4`.
The supported selected-partition verify-readback passed on that boot:

| Readback span | SHA-256 |
| --- | --- |
| Logical mtd1 | `093a7ba72b13545406e59f35fb38bd312e485b3f2e20f55e6c300dafad9c56af` |
| Logical mtd3, system | `3c73cd753eab1be4e9df7a57154833fb601513c76eee8740749c51ec8426a0b4` |

The camera-specific protected physical mtd0, mtd4 and mtd5 also matched the
preserved same-camera readbacks. Their identifiers and digests are retained only
in private evidence.

This is not a full-flash readback. Mutable data and an exact comparison of
physical mtd2 were excluded.

## Browser observation and remaining gates

After the operator handled the new local HTTPS certificate warning and logged
in, Chromium showed live Main and Substream WebRTC. One Listen click reported
`Audio playing` on each stream, and the operator heard audio immediately on
both. On Main stream, with Listen still on, switching the microphone from Off
to On restored audio immediately without Reload. No Safari result is claimed
for these bytes.

The public firmware-release ledger remains 2 of 9 gates closed. The full
content-addressed candidate matrix, interrupted-installation and recovery
tests, wider provisioning acceptance, a second camera, host-platform and hosted
CI acceptance, RTL8188FU rights, and Raptor corresponding-source and legal
review remain open. This record does not authorize firmware distribution.
