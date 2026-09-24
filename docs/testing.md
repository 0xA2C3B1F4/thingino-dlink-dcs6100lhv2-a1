# Testing and release evidence

Each result proves only its own level.

| Evidence | Proves | Does not prove |
| --- | --- | --- |
| Static contract | Source, schema, route, profile, and policy consistency | Running code or hardware behavior |
| Host unit or fixture | Deterministic logic and simulated failure handling | Firmware integration or real media-daemon behavior |
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
The documentation tests also parse the README's guided and low-level CLI
examples using the current command parsers without invoking their handlers.
They check that the universal sequence is complete and that stage/handoff
use the same firmware and camera inputs. This catches command-route and
required-argument drift; it does not run a build or touch SD media or a camera.
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

For full Raptor, `candidate create` takes a specification with
`"schema_version": 2` and `"media_backend": "raptor"`. Its content-addressed
identity binds the Raptor checklist, including `runtime.raptor_restart`, all
three WebRTC checks and `config.field_matrix`. Historical schema-1 records keep
their original checklist and do not establish full-Raptor acceptance. Run
documents supplied to `candidate record` use schema 1 for either profile.
Use an actual UTC `observed_at` timestamp ending in `Z` or `+00:00`; future
times and conflicting results for the same check at the same time are rejected.
The latest observation determines status, regardless of import order.

A content-addressed firmware candidate must record all of these groups before
acceptance:

- login, logout, and expiry;
- MJPEG and WebRTC on both streams, including disconnect and reconnect;
- both snapshots;
- every Preview control, with the physical effect observed where applicable;
- audio, Motion, privacy, LED, IR, configuration, and recorder actions;
- RTSP and ONVIF;
- restart of the selected media backend and recovery of its dependent services;
- slow clients, disconnects, and concurrent API requests;
- CPU, RSS, PSS, threads, file descriptors, sockets, shared memory, and
  available-memory proxy;
- kernel, ISP, encoder, and media errors; and
- one real Safari or Chromium session.

For every settings page, record each field and action separately. An enabled
control or a successful GET is not evidence that saving has an effect. Test a
changed value, the POST result, a fresh backend read, the running service's
effect and persistence after restart where applicable. Restore the previous
value and verify the restoration. Cover Image quality, Streams, OSD, Motion,
Day/night, Audio, RTSP/ONVIF, Home Assistant, Recorder, Timelapse, network,
time, credentials, system settings and Tools. A Preview toggle does not cover
the corresponding settings page.

Keep configured values distinct from hardware observations. For example, the
T31 noise-reduction setter can acknowledge a value but its SDK cannot read it
back; recording the configuration alone does not prove its visual effect.
Mark each remaining field as read-only, fixed by the device profile,
temporarily unavailable, unimplemented, or unsupported with its reason.
Disabled or hidden fields are not automatically complete.

Attach that field-level evidence to `config.field_matrix`. The ledger requires
`device_effect_observed: true` for a passed or manually observed field matrix,
Raptor restart, WebRTC or control check. This records an observation claim;
reviewing its evidence is still required before accepting the candidate.

Credential changes, settings initialization and SD formatting require explicit
authorization for that test. If permission or a physical step is missing,
leave the affected acceptance row open. Do not infer it from a host fixture.

### Local clock and session acceptance

On the exact installed candidate, record its digest, camera identity, boot
condition, LAN NTP address, and an independently synchronized UTC reference.
Keep the default local-only network profile without a default route or DNS.
Set a numeric NTP address on the camera's subnet and save it. With an
authenticated browser session already open, use Sync time now to correct the
clock. Record camera and reference UTC immediately after the correction, and
verify that the existing session can still read an authenticated endpoint
without a spurious expiry or repeated login. Confirm that logout and ordinary
session expiry still work after the correction.

Cold boot with the saved NTP setting. Record the camera clock before sync and
verify that it reaches within 10 seconds of the UTC reference within five
minutes of LAN readiness, without manual Sync time now, Internet access, or a
new NTP setting. Read back the saved NTP address and timezone after reboot.
If the clock remains at build time, only a manual sync works, or the session
fails across a valid correction, leave the firmware clock gate blocked. Record
the time readings, elapsed wait, browser behavior, and any failure separately;
a running NTP process or a correct timezone is not a pass.

The release also needs two byte-identical clean builds, removable-media
readback, physical interruption and recovery tests, and the licensing closure
listed in [status.md](status.md).

### First-attempt Preview audio

For the exact installed candidate, test main and substream separately, including
after a cold boot. Record the candidate digest, browser/version, selected stream,
boot condition, initial microphone state and time to audible output. Keep one
Preview client connected during each test.

1. Set the camera microphone off, then open Preview and wait for live WebRTC.
   The connection must be established while capture is disabled. MJPEG cannot
   satisfy this test because it has no audio.
2. Enable the microphone once and confirm that the UI reports On without a
   transition/readback error. Press Listen once. Make an identifiable sound near
   the camera and confirm that it is heard at the browser's output device.
3. Do not use Reload, reconnect or a second Listen click to turn a failed first
   attempt into a pass. Record silence, unexpected button changes, browser
   playback errors and session expiry as separate observations.
4. If silent, preserve the connection and microphone state. Open Audio
   diagnostics and press Inspect received audio. Retain its two samples,
   packetDelta, byteDelta, muted, paused and restartReason, along with the button
   label and visible errors. Collect a read-only camera input-state and RWD
   client snapshot before changing anything. A null delta means unavailable
   statistics or a changed connection, not zero received packets.
5. Only after recording the failed attempt, record any operator-approved retry
   separately. Success after Reload or a second click does not close this row.

Increasing packet counters or audio energy cannot establish physical audibility.
A later microphone-Off observation cannot explain an earlier silent attempt if
the operator disabled capture afterward. Record the order of those actions.
Reload deliberately resets browser playback to Listen; that reset alone is not
evidence of the original failure's cause. Also test ordinary playback with the
microphone already on and recovery after a background/foreground transition.
