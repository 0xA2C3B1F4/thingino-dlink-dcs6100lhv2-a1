# Raptor rwd WebRTC component

This component keeps Prudynt as the only IMP, ISP, frame-source, and encoder
owner. Prudynt publishes stream0 and stream1 Annex-B H.264 into bounded RSS
rings. The pinned Raptor `rwd` consumes those rings and provides video-only
WHIP and WebRTC.

It does not install or start Raptor `rvd`, `rsd`, `rad`, or HAL.

## Fixed scope

- D-Link DCS-6100LHV2 A1 only;
- stream0 at 1920x1080 and the configured stream1 substream;
- selected 15 fps profile;
- one WebRTC client;
- no WebRTC audio or talkback;
- 2 MiB main and 512 KiB sub RSS data areas; and
- unchanged Prudynt RTSP, snapshots, recording, and MJPEG fallback.

`rwd` reads video synchronously from RSS. If the reader falls behind or RSS
reports overflow, it jumps forward, requests an IDR, and resumes at a
keyframe. There is no separate 32-frame client video queue. `max_clients = 1`
is the client-memory bound.

## Source boundary

The Prudynt publisher is
`patches/prudynt/0047-media-publish-stream0-to-raptor-ring.patch`. Publication
is opt-in through `PRUDYNT_RAPTOR_RING=1`. A missing ring library or failed rwd
start does not fail Prudynt.

`raptor-lock.json` records the exact Raptor, RSS, Compy, and mbedTLS source
identities. Apply the two local rwd patches in order. The resulting `rwd` must:

- have 4 KiB load-segment alignment;
- have no `DT_RPATH` or `DT_RUNPATH`;
- have no libimp or Raptor HAL dependency;
- use the reviewed RSS acquire and release balance; and
- bind WHIP signaling only to `127.0.0.1`.

Control proxies only SDP and the opaque session identifier. It does not send
the WebUI cookie or API key to rwd. DTLS-SRTP media uses the camera certificate
and key. WHIP DELETE releases media, DTLS, and SRTP state.

## Lifecycle

Persistent service order starts Prudynt before `S96rwd`. Shutdown stops rwd
and every client before `S31prudynt` tears down media and ISP. A failed rwd
start leaves Prudynt, RTSP, snapshots, recording, and MJPEG running.

The RAM-only supervisor and runtime tool exist for bounded candidate testing.
They restart media and bind-mount candidate UI files but do not write MTD.
Their presence is not authorization to contact a camera.

## Build inputs

Use `thingino-dlink local-build build-universal --webrtc` after local preparation
and vendor acquisition. The installer builds the component from public locked
sources and links it automatically. For a component-only build, use
`thingino-dlink local-build build-raptor`.

`source-build-lock.json` defines public bases and reconstruction patches that
produce the exact accepted trees in `raptor-lock.json`, followed by the existing
two RWD patches. Transitive source and license identities are included. The
persistent component archive contains only rwd, RSS libraries, configuration
and provenance, with static LTO TLS. Existing reviewed external RAM archives
remain accepted through `--raptor-rwd-artifact`.

See [build instructions](../../docs/build.md#build-webrtc-from-public-sources).
Build success does not close licensing, redistribution or camera acceptance gates.
