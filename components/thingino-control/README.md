# Thingino Control

`thingino-controld` is the DCS-6100LHV2 A1 WebUI and API control service. It is
a dependency-free Rust daemon built against the pinned MIPS glibc 2.16 ABI
floor.

Control owns browser sessions, API keys, configuration validation, hardware
actions, diagnostics, recorder control, Home Assistant MQTT state, and bounded
communication with Prudynt. It does not run a shell, `curl`, `jct`, or another
helper for each request.

## Boundaries

- `web_auth.rs` owns login, session lifetime, API keys, and trusted-LAN policy.
- `request_parse.rs` owns pure HTTP parsing, `request.rs` owns bounded socket
  reads, and `decode.rs` owns the shared decoders.
- `camera/api.rs` owns route dispatch and `camera/config/` owns validated
  configuration reads and writes by domain.
- `camera/actions.rs` owns day/night, live controls, reset, and reboot.
- `camera/runtime.rs`, `camera/diagnostics.rs`, `camera/storage.rs`, and
  `camera/files.rs` own their bounded runtime domains; `camera/platform.rs`
  owns reviewed OS and SysV IPC boundaries.
- `camera/motion_datagram.rs`, `camera/motion.rs`, and `camera/ha/` separate
  datagram parsing from Motion and MQTT worker lifecycles.
- `camera/prudynt.rs` owns the framed Unix-socket boundary to Prudynt.
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
asks Control to authorize the request, then relays bytes directly from Prudynt
or the owning file service.

The complete v1 contract is in [`docs/api.md`](../../docs/api.md).

## Host checks

Keep Cargo output outside the repository:

```bash
export CARGO_TARGET_DIR=/path/to/external/cargo-target

cargo fmt --manifest-path components/thingino-control/Cargo.toml --check
cargo clippy --manifest-path components/thingino-control/Cargo.toml \
  --all-targets -- -D warnings
cargo test --manifest-path components/thingino-control/Cargo.toml --locked
cargo build --manifest-path components/thingino-control/Cargo.toml \
  --release --locked
python3 components/thingino-control/scripts/host_soak.py \
  --binary "$CARGO_TARGET_DIR/release/thingino-controld"
```

The soak sends 1,000 requests and checks stalled-backend behavior, response
deadlines, child-process count, file descriptors, RSS, request-file absence,
and secret absence from logs and process arguments.

Host success is not camera acceptance. The final candidate must use the exact
cross-built binary extracted from the validated image and pass the external
browser and device matrix in [`docs/testing.md`](../../docs/testing.md).
