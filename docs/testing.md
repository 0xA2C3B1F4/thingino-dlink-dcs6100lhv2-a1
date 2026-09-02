# Testing and release evidence

Each result proves only its own level.

| Evidence | Proves | Does not prove |
| --- | --- | --- |
| Static contract | Source, schema, route, profile, and policy consistency | Running code or hardware behavior |
| Host unit or fixture | Deterministic logic and simulated failure handling | Firmware integration or real Prudynt behavior |
| Built artifact | Exact binary and image bytes, size, and provenance | Boot, media, browser, or physical controls |
| Real browser | Rendered UI and WebRTC or MJPEG lifecycle | Camera-side physical effect unless observed separately |
| Live device | The named action on the exact candidate and camera | Another build, board, or unfinished matrix row |

## Host commands

The canonical source gate is:

```bash
make check
```

Focused gates are:

```bash
make policy
make release-status
make docs
make contract
make test
make control
make webui-build
make webui
make webui-size
```

`make check` runs the public allowlist and secret scan, immutable source-lock
validation, release-gate ledger, Markdown links, Control API contract, and
Python host tests.
`make control` runs formatting, Clippy, Rust tests, release build, contract
tests, and the 1,000-request soak. `make webui` runs TypeScript, unit, bundle,
and prohibited-route checks.

## Optional parser stress and mutation checks

`components/thingino-control/fuzz/` is a separate `cargo-fuzz` package, not a
production dependency. Copy its corpus to task-owned scratch and use the
documented 60-second target runs with a 1024 MiB RSS limit for JSON, HTTP,
shared decoders, and Motion datagrams. Mutation testing should stay bounded to
regression-derived selections for JSON, requests, Motion state, storage policy,
and configuration parsing. Neither tool is a permanent release gate.

GitHub CI also runs a focused host-platform matrix on Ubuntu and Windows. It
checks the Linux and Windows removable-media metadata validators, platform
dispatch, and the common temporary-write/readback/activation sequence. These
are native host contracts, not firmware builds or real SD-card and camera
acceptance.

`make release-status` reads the evidence ledger and reports open gates.
`make release-ready-source` validates the disclosed source snapshot.
`make release-ready-public-firmware` fails until every public firmware-release
gate is closed. Private local-build acceptance is tracked separately.

## Candidate acceptance

A content-addressed firmware candidate must record all of these groups before
acceptance:

- login, logout, and expiry;
- MJPEG and WebRTC on both streams, including disconnect and reconnect;
- both snapshots;
- every Preview control, with the physical effect observed where applicable;
- audio, Motion, privacy, LED, IR, configuration, and recorder actions;
- RTSP and ONVIF;
- Prudynt restart;
- slow clients, disconnects, and concurrent API requests;
- CPU, RSS, PSS, threads, file descriptors, sockets, shared memory, and
  available-memory proxy;
- kernel, ISP, encoder, and media errors; and
- one real Safari or Chromium session.

The release also needs two byte-identical clean builds, removable-media
readback, physical interruption and recovery tests, and the licensing closure
listed in [status.md](status.md).
