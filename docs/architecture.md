# Architecture

The camera has one media owner and one control service.

```text
Browser or API client
        |
        v
      uhttpd
        |-- /          static WebUI
        |-- /api/v1    Thingino Control ---- Prudynt Unix socket
        |                    `-- WHIP ---- optional loopback Raptor rwd
        |-- /media/v1  authorized Prudynt or file relay
        `-- ONVIF      persistent loopback service

Prudynt H.264 encoders -- optional Annex-B RSS rings -- Raptor rwd -- DTLS-SRTP
```

## Universal firmware and camera-private state

The immutable model layer contains the signed A1 kernel, permanent bootstrap,
closed unprovisioned system SquashFS, source-built Prudynt and media init, and
the catalog-locked owner-acquired stock userspace libraries. Its ISP and sensor
modules and IQ file come from the same source build as its kernel. The bundle
also binds the acquired stock module and IQ identities as recovery evidence.
The legacy private media closure and Raptor runtime are optional profile additions.
It contains no camera recovery identity, WPA configuration, hostname, SSH key,
management credential, API key, or writable data image. One exact model build
is content-addressed and reusable.

The camera layer contains four separate private objects: recovery evidence, a
signed audit sidecar, an exact-span JFFS2 overlay image, and a signed
authorization. The authorization binds the universal firmware and stage-2
hashes to one camera identity, recovery session, data action, sidecar,
provisioning ID, and JFFS2 digest. A second non-emitted key derived from complete
preserved mtd0/mtd4/mtd5 bytes HMACs the fixed binary consumed by Stage 1.
Per-camera objects never feed a compiler or change universal firmware bytes.

The initial universal root is deliberately closed: root is locked, private
configuration paths are absent, and network-facing init scripts are installed
non-executable with reviewed archived copies. On the host, provisioning applies
the same validated configuration transformations to an OverlayFS upper tree
and runs locked `mkfs.jffs2` twice. Both 1,507,328-byte results must be identical
and pass `jffs2dump -c`.

Stage 1 loads exact stage 2 and provisioning bytes into bounded RAM snapshots,
then verifies their hashes, the HMAC, live camera identity, and data action
before passivating the active bootstrap. For `initialize`, it commits the stock
mtd3 backup and checkpoint through readback-plus-rename transactions. The
universal checkpoint additionally binds the exact authorization binary and
provisioning digest, so a retry cannot substitute different same-camera
credentials. Stage 1 writes and reads back the complete JFFS2 data region,
writes and verifies the immutable
system and kernel tail, and activates the first kernel eraseblock last. The
active selector is therefore one-use under the removable-media transaction;
this is not represented as clone-resistant cryptographic anti-replay. After
successful activation, Stage 1 passivates the remaining camera files best
effort. Physical power-loss behavior remains a release gate.

## Prudynt owns media

Prudynt is the only owner of IMP, ISP, frame sources, encoders, JPEG, OSD,
RTSP, Motion observations, and recording. Its D-Link patch set adds bounded
queues, deterministic JPEG demand, framed Control IPC, process-replacement
recovery, and the optional RSS publisher used by WebRTC.

The main RSS ring has a 2 MiB data area. The substream ring has 512 KiB.
Prudynt publishes Annex-B access units, capture timestamps, dimensions, frame
rate, and keyframe state only while a reader exists. An RSS IDR request maps to
the corresponding Prudynt encoder.

## Raptor rwd only transports WebRTC

The build uses Raptor's `rwd`, RSS, IPC, Compy, and DTLS-SRTP closure. It does
not start Raptor `rvd`, `rsd`, `rad`, or HAL. It does not initialize IMP or
ISP. One configured client is the memory bound.

Control proxies loopback WHIP signaling through the authenticated same-origin
API. Browser media uses DTLS-SRTP over the camera's UDP socket. A failed rwd
start disables only WebRTC. RTSP, snapshots, and MJPEG continue through
Prudynt. Service shutdown stops rwd and its clients before Prudynt tears down
the rings and ISP.

## Thingino Control owns the API

`thingino-controld` is a dependency-free Rust service built against the pinned
MIPS glibc 2.16 ABI floor. It owns sessions, API keys, configuration, hardware
actions, diagnostics, Home Assistant MQTT state, and bounded calls to Prudynt.
It does not launch request-time shell commands or helper processes.

Four fixed worker threads serve a bounded connection queue. Separate backend
limits leave capacity for login and health requests if media or storage is
slow. Secrets are write-only. GET responses report whether a value is set but
never return it.

## uhttpd owns ingress

uhttpd terminates TLS for the WebUI, Control, media, and snapshot routes,
serves four static WebUI files, and proxies fixed routes through Host and
Origin checks. Plain HTTP is limited to ONVIF SOAP compatibility. Its pumps
are nonblocking and bounded.
CGI, dynamic request plugins, script queues, and request-time child processes
are absent from the target build.

## The WebUI is static

The frontend is a framework-free TypeScript application. It uses same-origin
HttpOnly sessions and the versioned Control API. WebRTC is preferred for both
streams. MJPEG remains the fallback. The production bundle contains only
`index.html`, `manifest.webmanifest`, `assets/app.css`, and `assets/app.js`.
