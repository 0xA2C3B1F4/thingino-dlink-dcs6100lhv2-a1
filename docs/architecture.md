# Architecture

The camera has one media owner and one control service.

```text
Browser or API client
        |
        v
      uhttpd
        |-- /          static WebUI
        |-- /api/v1    Thingino Control ---- Prudynt Unix socket
        |                    `-- WHIP ---- loopback Raptor rwd
        |-- /media/v1  authorized Prudynt or file relay
        `-- ONVIF      persistent loopback service

Prudynt H.264 encoders -- Annex-B RSS rings -- Raptor rwd -- DTLS-SRTP
```

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

uhttpd terminates HTTP or TLS, serves four static WebUI files, and proxies the
fixed Control, media, and ONVIF routes. Its pumps are nonblocking and bounded.
CGI, dynamic request plugins, script queues, and request-time child processes
are absent from the target build.

## The WebUI is static

The frontend is a framework-free TypeScript application. It uses same-origin
HttpOnly sessions and the versioned Control API. WebRTC is preferred for both
streams. MJPEG remains the fallback. The production bundle contains only
`index.html`, `manifest.webmanifest`, `assets/app.css`, and `assets/app.js`.
