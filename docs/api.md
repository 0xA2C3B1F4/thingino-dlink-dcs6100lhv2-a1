# Thingino Control API v1

The browser and local API share `/api/v1`. The machine-readable route and
schema contract is
[`contracts/thingino-control-api-v1.json`](../contracts/thingino-control-api-v1.json).

## Authentication

- Browser login creates a Secure, SameSite=Strict, HttpOnly session cookie.
- Direct API clients use `Authorization: Bearer <64 lowercase hex>`.
- The browser never reads the cookie or the service API key.
- URL query parameters are never accepted as credentials.
- Sessions expire after 12 hours idle or 24 hours total.
- Trusted exact addresses and nonzero CIDRs can authorize GET only; mutations
  still require a session, API key, or internal bearer.
- Password changes, access-credential changes, and API-key management require
  a browser session. API-key GET returns only whether a key exists; a newly
  generated key is returned once.
- Camera mode listens only on loopback behind uhttpd.

WebUI, Control API, media, and snapshot ingress require HTTPS. The plain HTTP
listener remains available only for ONVIF SOAP compatibility and does not
serve the WebUI or management routes. TLS responses include HSTS.

uhttpd accepts management requests only when `Host` is an IP literal, a
single-label local hostname, a `.local` name, or a `.home.arpa` name. When a
browser sends `Origin`, its scheme and authority must exactly match the TLS
request. This rejects public DNS names used for DNS-rebinding access.

The standard JSON error envelope contains a stable code, message, and
retryable flag. Authentication errors use HTTP 401. Invalid input, size limits,
unavailable backends, and deadlines use their documented 4xx or 5xx status.
Login attempts are bounded per source and globally before password hashing;
excess attempts return HTTP 429.

Browser POST, PUT, and DELETE requests carry
`X-Requested-With: Thingino-WebUI` or JSON media type. A non-empty JSON body
always requires `Content-Type: application/json`, including for API-key and
bearer callers. Successful password replacement requires 10 to 128 bytes and
invalidates every existing session.

The bounded file editor retains `text/plain`, the network probe retains
`application/x-www-form-urlencoded`, and WHIP retains `application/sdp`.
Other non-empty mutations use JSON.

Credential mutations additionally require a browser login no more than 10
minutes old; an older valid session must sign in again first.

## Main route groups

| Prefix | Purpose |
| --- | --- |
| `/api/v1/auth` | Login, logout, session, password, and API-key rotation |
| `/api/v1/runtime` | Health, heartbeat, system, media, Home Assistant, storage, and diagnostics |
| `/api/v1/config` | Admin, network, time, access, GPIO, WebUI, logging, and Home Assistant settings |
| `/api/v1/prudynt` | Audio, image, streams, OSD, Motion, privacy, and RTSP configuration |
| `/api/v1/actions` | Day/night, live controls, recording, restart, reset, and reboot |
| `/api/v1/actions/snapshot` | Complete JPEG from stream0 or stream1 |
| `/api/v1/media/webrtc/whip` | Same-origin WHIP POST and session DELETE |
| `/api/v1/storage` | Recorder, timelapse, files, and bounded maintenance |

## Limits

- request headers: 8 KiB;
- ordinary request body: 4 KiB;
- ordinary JSON backend response: 64 KiB;
- snapshot body: 2 MiB;
- four worker threads and a 16-connection queue;
- at most two concurrent backend operations; and
- three-second accepted-request deadline.

uhttpd relays MJPEG, snapshots, recordings, and ONVIF responses after Control
authorizes the exact request. `/onvif/image.cgi` and `/onvif/image1.cgi` use
the same session, API-key, or bearer authentication as other snapshot routes;
there is no unauthenticated ONVIF snapshot bypass. Large media bodies do not
occupy a Control worker.

## Worker and native-IPC lifecycle

Motion owns and joins its receiver and worker threads, wakes its condition
variable on shutdown, rolls back a partial spawn, and clears ingress readiness
after a fatal receiver exit. The HA `WorkerControl` owns the configuration
generation, bounded sender, shutdown flag, and `JoinHandle`; joins happen
outside its lifecycle mutex and stale-generation requests are drained before a
restart. Host race tests cover these transitions, but whole-daemon signal
shutdown and target libmosquitto teardown remain device gates.

The BusyBox syslog reader gets the true segment size with `shmctl(IPC_STAT)`,
attaches read-only, validates the header, data length, and tail before making a
slice, and uses one 100 ms semaphore deadline across interrupted waits. It
always attempts unlock and detach, including error paths.

## WebRTC signaling

`POST /api/v1/media/webrtc/whip?stream=0|1` accepts a bounded
`application/sdp` offer. Control returns HTTP 201, an SDP answer, and a
same-origin `Location`. It forwards neither the browser cookie nor the API key
to loopback rwd.

`DELETE /api/v1/media/webrtc/whip/{session_id}` returns HTTP 204 after rwd
releases the WebRTC, DTLS, and SRTP state. ICE consent expiry is the fallback
for a client that disappears without DELETE.

## Secret updates

Secret GET fields are `null` and have a sibling `*_set` boolean. Omitted,
empty, or null secret values preserve the stored value unless the route
explicitly performs a rotation. UI forms start from the complete decoded GET
model so hidden and read-only fields survive an edit.

## Hardware actions

The A1 profile accepts Auto, Day, Night, IR-cut, 850 nm IR, Motion, privacy,
microphone, speaker, recorder, and supported maintenance actions. IR940, white
light, generic GPIO output, firmware upload, and whole-card repartitioning are
not part of the contract. The storage route provides one explicitly confirmed
FAT32 format operation for the existing `/dev/mmcblk0p1` partition. Its
persistent worker revalidates the CID-backed card identity, device, and mount
immediately before `mkfs`; any change fails before formatting.
