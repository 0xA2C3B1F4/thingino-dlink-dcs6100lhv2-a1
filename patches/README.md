# Public patch set

These patches are the reviewed source delta used by the D-Link
DCS-6100LHV2 A1 full-Raptor build. They apply to immutable upstream revisions
recorded in `sources.lock.json`; `scripts/source_prepare.py` verifies every
input digest before applying them.

## Scope

The `thingino/` series provides the board baseline, deterministic build inputs,
the camera-local IMP 1.1.4 support libraries, recovery networking, the Rust
Thingino Control package, the static WebUI, persistent ONVIF, and hardened
storage and recovery behavior. The selected configuration uses
`THINGINO_STREAMER_NONE`: no legacy media package is built or installed.

The day/night patch keeps photosensing and hysteresis in `daynightd`, publishes
the requested state to Thingino Control, and preserves the device-verified
100 ms IR-cut GPIO pulses. Media actuation belongs to Raptor.

The `uhttpd/` series removes CGI and request-process execution, implements the
bounded loopback Control and ONVIF ingress, accounts for TLS backpressure, and
keeps static and media responses within fixed limits. Media requests are
relayed to the selected full-Raptor backend without adding a second media
owner.

The `onvif/` patch turns the pinned handler into a bounded loopback daemon and
replaces command hooks with in-process or Thingino Control operations. Removed
lines in its patch context document deletion of the superseded bridge from the
pinned upstream tree; they are not shipped source or a build input.

Raptor itself is built from the corresponding source component in this tree
and composed onto the support base by the universal build. There is no
external media artifact, selectable legacy backend, or private media closure.

## Provenance and validation

The exact patch order and hashes are recorded in
`profiles/dlink-dcs6100lhv2-a1/source-profile.json`. Patch application and host
tests prove source compatibility only. A successful source export or build
does not by itself prove installation, visible video, audio transitions, or
physical recovery on a camera; those are separate release gates.

The camera-local vendor-library inputs and the RTL8188FU driver retain their
own provenance and redistribution obligations. Current gate state is recorded
in `policy/release-gates.json` and checked by `scripts/check_release_gates.py`.
