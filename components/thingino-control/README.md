# Thingino Control

`thingino-controld` is the DCS-6100LHV2 A1 WebUI and API control service. It is
a dependency-free Rust daemon built against the pinned MIPS glibc 2.16 ABI
floor.

Control owns browser sessions, API keys, configuration validation, hardware
actions, diagnostics, recorder control, Home Assistant MQTT state, and bounded
communication with the Raptor media services. It does not run a shell, `curl`,
`jct`, or another helper for each request.

## Boundaries

- `web_auth.rs` owns login, session lifetime, API keys, and trusted-LAN policy.
- `request_parse.rs` owns pure HTTP parsing, `request.rs` owns bounded socket
  reads, and `decode.rs` owns the shared decoders.
- `camera/api.rs` owns route dispatch and `camera/config/` owns validated
  configuration reads and writes by domain.
- `camera/actions.rs` owns day/night, live controls, reset, and reboot.
- `camera/runtime.rs`, `camera/diagnostics.rs`, and `camera/files.rs` own
  their bounded runtime domains.
- `camera/storage.rs` exposes storage operations; `camera/storage/` separates
  path/mount checks, SD identity, formatting, recording and retention.
- `camera/platform.rs` exposes OS operations; `camera/platform/` separates
  ABI declarations, file/socket I/O, logs and SysV IPC ownership.
- `camera/motion_datagram.rs` parses ingress datagrams. `camera/motion.rs`
  owns service state, while `camera/motion/` separates thread lifecycle,
  event dispatch and persisted clips.
- `camera/ha/service.rs` owns HA request/status state. Its `service/` modules
  separate worker ownership, MQTT sessions and publication. Other `camera/ha/`
  modules own configuration, MQTT framing, discovery and Motion handoff.
- `camera/raptor.rs` owns the bounded media-service boundary to Raptor.
- `protocol.rs` defines the shared routes, responses, and error model.

`camera.rs` is the composition root for paths, shared camera types, the backend,
and common bounded helpers. `camera/api.rs` owns the route table.

Camera mode listens only on loopback behind uhttpd. Four worker threads serve
a 16-connection queue, with at most two backend operations and fixed deadlines.
Headers, bodies, JSON, and JPEG responses have separate size limits.

The uhttpd proxy supplies the actual client address and forwards only the exact
same-origin mutation marker. Trusted addresses can authorize GET only.
Credential-management routes require a browser session, and the API-key status
route never returns the stored key. Login admission is bounded before password
hashing; credential mutations require a login no more than 10 minutes old, and
password replacement atomically updates credentials and invalidates all
sessions.

Snapshots and large media bodies do not pass through a Rust worker. uhttpd
asks Control to authorize the request, then relays bytes directly from the
Raptor media service or the owning file service.

The complete v1 contract is in [`docs/api.md`](../../docs/api.md).

## Host checks

Run from the repository root with Rust 1.95.0, Python 3, `mosquitto`,
`mosquitto_passwd` and `openssl` available. Keep Cargo output outside the
repository. Set `TMPDIR` to an existing writable temporary directory with a
short path, because the tests create Unix sockets beneath it.

```bash
export CARGO_TARGET_DIR=/path/to/external/cargo-target
export TMPDIR=/path/to/short/temp
make control
```

This runs formatting, clippy, the Rust tests against disposable local MQTT
brokers, a release host build and the host soak. MQTT fixtures include plain,
authenticated and TLS connections; their generated credentials are temporary.
The soak sends 1,000 requests and checks stalled-backend behavior, response
deadlines, child-process count, file descriptors, RSS, request-file absence,
and secret absence from logs and process arguments.

Host success is not camera acceptance. The final candidate must use the exact
cross-built binary extracted from the validated image and pass the external
browser and device matrix in [`docs/testing.md`](../../docs/testing.md).
