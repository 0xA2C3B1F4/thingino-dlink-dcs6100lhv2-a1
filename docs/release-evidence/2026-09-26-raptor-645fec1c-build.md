# September 26 verification of the fresh-default Raptor build

Source `645fec1ce5f0c262541eaff00d50e946265e99bd` was built in run
`build-20260925T170308006681Z-645fec1ce5f0`. The supported model-universal
builder used `--data-mode initialize --build-count 2`. Both clean base
containers exited with status 0 and without OOM. The complete-firmware
comparison reports two builds, no component cache, accepted inspections,
no differences and byte-identical results. The SHA-256 of
`reproducibility.json` is
`8e9a1b16e8cd8a9d34d8edd3fed205eeff00eabc7f2a1df024e5ccd119e86caa`.

The signed universal bundle is 7,958,609 bytes with SHA-256
`9744c5b3ce02ec53963492766cacc5351f350b60e132f53747593f5fcb60fc0b`.
Its final Raptor system SquashFS is 6,373,376 bytes with SHA-256
`bf2c27f5825e3650423f7368b5b5b1fbf61f6c83fdeee44b793d88b99a8aa836`.
The separate `inspect-install-set` command passed schema 2, the fixed
`dcs6100lhv2-a1-mtd3-split-v1` layout and initialize mode. System and data
spans are 6,619,136 and 1,507,328 bytes respectively.

`scripts/release_closure.py` independently validated the completed run and
signed bundle using the retained model public key. Its technical closure
binds the embedded system digest to the final Raptor system above. This does
not authorize redistribution or close legal review.

The run's pinned Buildroot `unsquashfs` was used through its generated
workspace wrapper to read `etc/raptor-media.conf` directly from the final
Raptor SquashFS. It contains `[sensor] antiflicker = 2` and
`[stream0] enabled = true`. These are the two fresh-install saved-state fixes.
A file-list check found `usr/bin/rad`, `usr/bin/rvd`, `usr/bin/rwd` and
`etc/init.d/S96raptor`; the checked `usr/bin/prudynt`, `usr/bin/prudyntctl`
and `etc/init.d/S31prudynt` paths were absent.

The first clean base SquashFS, Linux configuration, base manifest and source
preparation report are also byte-identical by `cmp` to the corresponding
first-build outputs of the installed `867d1182` version. The Raptor component
archive digest remains
`2695c60dae4d974be8da8c9147db03b885ff133b91cb8a9df85cfc4e1206b1e3`.
Neither comparison transfers the earlier camera acceptance to this new final
system image.

The build checks above did not write SD or camera storage. They do not by
themselves establish device acceptance or firmware-release readiness.

## First-camera installation and fresh-state reads

The same bundle was subsequently staged through the supported installer on
the first camera's identified SD card. The previous stock backup was copied,
verified and retained on the host before its card copy was removed. No card
formatting was performed. UART captured the stock updater's explicit success
line before the host handoff made its matching filename passive.

The second boot recorded provisioning data written and verified, final system
and kernel-tail writes, preactivation kernel verification, activation written
and verified, installation-file passivation, SD unmount and final reboot.
The following boot reached `thingino_verified_switch_root`.

`universal verify` passed authenticated management, application, media and
WebUI checks. The separate `universal verify-readback` passed against the new
signed bundle, covering logical partitions 0, 1, 3, 5 and 6. This is not a
whole-flash comparison: mutable data and exact physical mtd2 contents are
outside that readback. Stock bootstrap wrote physical mtd1/mtd2; final
installation wrote physical mtd1/mtd3. Protected physical mtd0/mtd4/mtd5
were verified by the selected readback.

Before any settings save, a separate authenticated HTTPS test session used
the camera certificate obtained through pinned SSH, with certificate and
hostname validation enabled. Image quality reported anti_flicker live,
configured and saved values all equal to 2, with `matches_saved: true` and
`verification: sdk-readback`. Main stream enable_control reported active,
configured and saved enabled values all true, `saved_available: true`,
`configured_matches_saved: true` and `pending_restart: false`. These reads
confirm the two fresh-default corrections on the installed bytes. The test
session logged out with HTTP 204; it did not save settings.

The same main-stream response also reported unavailable saved rate-control
state and a null saved encoder profile. These are follow-up observations,
not accepted settings behavior; their cause and UI impact remain to be
classified. New-candidate browser/audio acceptance, the rest of the full
candidate matrix and release/legal gates remain open.

The source follow-up identified missing `rc_mode` and `profile` keys in both
fresh stream sections. The exact build input's `rvd/rvd_pipeline.c`
`load_stream_config` uses defaults `cbr` and `2`, whereas Control's
`stream_disk_encoding`, `stream_disk_rc_config` and `stream_disk_profile`
require explicit saved values for their observations. Both stream sections
now declare those existing defaults. A regression test failed for both
streams before the change and checks their saved encoding defaults; the
composition test also checks the keys in the packed-root fixture.
`init_qp` remains absent: CBR permits no explicit QP and RVD uses its unset
sentinel. The GOP-mode reader already handles its documented `default` value
when the key is absent. The stream audio field has a separate explicit-user-
selection contract; this correction does not change it.

These additional source corrections are not included in the installed
`645fec1c` bundle. Its installation and readback evidence remains valid for
that bundle only.

## Follow-up build with explicit stream encoding defaults

Source `46fe7be5fa061630de620738e96965da4d8663f1` was subsequently built in
`build-20260926T061621701161Z-46fe7be5fa06` through the same supported
initialize-mode, two-clean-build workflow. Both base containers exited 0
without OOM. The complete-firmware comparison reports two byte-identical
builds, no component cache, no differences and accepted inspections. Its
`reproducibility.json` SHA-256 is
`b5e317c4e8d8863d5392c26f2f82d853364b5592b9e92d7bcb6e95f7b47a93b0`.

The new signed bundle is 7,958,609 bytes with SHA-256
`f891e0b010de1007bf432b69976226b7d2c6d01872e35cf0fafa85577ef83d06`.
The final system SquashFS is 6,373,376 bytes with SHA-256
`a5c0bf7ea4b19adda5c478f5d65f746de55c8489a25a8aa27379a3da6223ce54`.
The separate `inspect-install-set` check and `scripts/release_closure.py`
both passed. The latter used the retained model public key and establishes
technical provenance, not redistribution permission.

Reading `etc/raptor-media.conf` from the final system with the run's pinned
`unsquashfs` confirmed both streams have `profile = 2` and `rc_mode = cbr`.
The earlier `antiflicker = 2` and main-stream `enabled = true` fixes remain.
The Raptor component archive digest is unchanged from the build above.

The new schema-2 Raptor candidate ID is
`82860c27f42e2a65314504cc242f52a187ee73761997e0687290ec82afbf0a44`.
It is not accepted: installation, fresh settings reads and the device matrix
for these exact artifacts remain pending. No camera or SD writes were made
by these build and package checks. Earlier firmware's browser/audio results
are not claimed as acceptance of this new bundle.
