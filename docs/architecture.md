# Architecture

The A1 image has one media owner and one control service. The public build path
always composes the full Raptor media stack.

```text
Browser or API client
        |
        v
      uhttpd
        |-- /          static WebUI
        |-- /api/v1    Thingino Control
        |-- /media/v1  authorized Raptor media or file relay
        `-- ONVIF      persistent loopback service

Raptor media services -- Thingino Control -- uhttpd -- browser
       |-- RTSP, MJPEG, snapshots and recording
       `-- WebRTC over authenticated WHIP and DTLS-SRTP
```

## Universal firmware and camera-private state

The immutable model layer contains the signed A1 kernel, permanent bootstrap,
closed unprovisioned system SquashFS, source-built Control and Raptor services,
and the catalog-locked stock libraries, sensor modules and IQ data acquired from
the matching camera. The bundle contains no camera recovery identity, WPA
configuration, hostname, SSH key, management credential, API key, or writable
data image. One exact model build is content-addressed and reusable.

The camera layer contains four separate private objects: recovery evidence, a
signed audit sidecar, an exact-span JFFS2 overlay image, and a signed
authorization. The authorization binds the universal firmware and stage-2
hashes to one camera identity, recovery session, data action, sidecar,
provisioning ID, and JFFS2 digest. A second non-emitted key derived from the
complete preserved mtd0/mtd4/mtd5 bytes HMACs the fixed binary consumed by Stage
1. Per-camera objects never feed a compiler or change universal firmware bytes.

The initial universal root is deliberately closed. Root is locked, private
configuration paths are absent, and network-facing init scripts are installed
non-executable with reviewed archived copies. On the host, provisioning applies
the validated configuration transformations to an OverlayFS upper tree and runs
locked `mkfs.jffs2` twice. Both 1,507,328-byte results must be identical and pass
`jffs2dump -c`.

Stage 1 loads exact stage 2 and provisioning bytes into bounded RAM snapshots,
then verifies their hashes, the HMAC, live camera identity, and data action
before passivating the active bootstrap. For `initialize`, it commits the stock
mtd3 backup and checkpoint through readback-plus-rename transactions. The
universal checkpoint also binds the exact authorization binary and provisioning
digest, so a retry cannot substitute different same-camera credentials. Stage 1
writes and reads back the complete JFFS2 data region, writes and verifies the
immutable system and kernel tail, and activates the first kernel eraseblock
last. Physical power-loss behavior remains a release gate.

## Full Raptor owns media

The full Raptor source build provides the media services used by the image:
`rvd` for video and motion, `rhd` for image and hardware controls, `rsd` for
RTSP, `ric` for camera policy, `rad` for audio, `rod` for OSD, `rmr` for
recording, `raptorctl` for service coordination, and `rwd` for WebRTC. The
Raptor HAL owns the target-specific media interfaces. The source lock binds the
public revisions, reconstruction patches, final trees and license-file hashes.

Raptor publishes bounded stream metadata and encoded frames to its consumers.
RMR owns recording and timelapse writers. RSD provides authenticated RTSP, and
the HTTP media relay serves snapshots and MJPEG after Control authorizes the
request. The default stream paths are `/stream0` and `/stream1`.

Control proxies loopback WHIP signaling through the authenticated same-origin
API. Browser media uses DTLS-SRTP over the camera's UDP socket. A failed Raptor
service or unavailable capability is reported as unavailable or partial; the
API does not claim success from a stale or missing readback. Service shutdown
stops dependent clients before the owning Raptor service is torn down.

## Thingino Control owns the API

`thingino-controld` is a dependency-free Rust service built against the pinned
MIPS glibc 2.16 ABI floor. It owns sessions, API keys, configuration, hardware
actions, diagnostics, Home Assistant MQTT state, and bounded calls to the Raptor
services. It does not launch request-time shell commands or helper processes.

Four fixed worker threads serve a bounded connection queue. Separate backend
limits leave capacity for login and health requests if media or storage is slow.
Secrets are write-only. GET responses report whether a value is set but never
return it.

## uhttpd owns ingress

uhttpd terminates TLS for the WebUI, Control, media, and snapshot routes, serves
the static WebUI, and proxies fixed routes through Host and Origin checks. Plain
HTTP is limited to ONVIF SOAP compatibility. uhttpd buffers each bounded Control
or ONVIF request body before opening a loopback backend, and its backend pumps
are nonblocking and bounded.

CGI, dynamic request plugins, script queues, and request-time child processes
are absent from the target build.

## The WebUI is static

The frontend is a framework-free TypeScript application. It uses same-origin
HttpOnly sessions and the versioned Control API. WebRTC is preferred for both
streams, with MJPEG available as the browser fallback. The production bundle
contains only `index.html`, `manifest.webmanifest`, `assets/app.css`, and
`assets/app.js`.
