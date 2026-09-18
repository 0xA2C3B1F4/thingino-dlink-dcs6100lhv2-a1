# Raptor microphone acknowledgement candidate, September 18

The normal `thingino-dlink local-build build-universal` workflow completed
successfully from firmware-input commit
`72e3025359bcc14065a78d7f5d60e104d18b7a6e`, with `--build-count 2` and
`--data-mode preserve`. The immutable builder image was
`sha256:8abae43028bd1155572c76e268b9b8c5dce4686efe70ff68c0b7c5f9b93b361e`.
Unlike the September 17 run, this invocation completed both packages without
a separate manual packaging completion. Normal complete-firmware comparison
accepted all 16 files as byte-identical; neither Raptor component compile used
the component artifact cache. Both schema-2 install-set inspections passed.

This candidate includes the bounded IPC acknowledgement fix and stage-tagged
Control diagnostics described in the
[September 17 evidence](2026-09-17-raptor-audio-reproducibility.md).
It remains Raptor-only, with no Prudynt fallback. The reconstructed IPC tree is
`67274ab44406926d61ae37b353218dac630ef72d`.

| Artifact | SHA-256 |
| --- | --- |
| Signed universal bundle | `2810c84ac90ee323225e910cdc77bbf77b3e123a178f0c9954d0bfb7226642c3` |
| System SquashFS, 6,356,992 bytes | `f7b058a753bf3b537d5cacbeee5902afdb160dd0d2d3097aa7bb362704b86e7a` |
| Full Raptor component | `303fb135c8b89dee421196e588191f95e73f7810bf62428c91a960cf683bca7f` |
| Final kernel | `faf28040e59639fb9818b3cc12ead8c1c5a0f8109d01ab693fb579797dd3e383` |

## SD preparation, not camera acceptance

The existing same-camera private configuration generated a new firmware-bound
provisioning sidecar and preserve authorization through the normal CLI.
The read-only stage plan bound the exact new bundle to the reidentified external
FAT32 card. Existing installation files were independently backed up on the host.
Normal staging activated the stock-updater selector last. Independent readback
after unmount/remount matched all six staged file hashes and the volume identity.
The card was not formatted; recording contents were not modified.

Following the operator's explicit first-phase completion report and return of
the card, normal host handoff validated the same card and exact candidate,
passivated the stock-updater selector and retained the Stage 1 package. A host
backup preserved the six staged files. Independent unmount/remount readback
matched all six retained hashes and confirmed absence of the active stock
selector. First-boot completion remains operator-observed, not independent
camera NOR readback.

After the operator reported the final boot ready, normal `universal verify`
passed pinned health, authenticated Control, WebUI, media and application
readiness. An independent pinned SSH read of the full system partition produced
`ed7282b4d800074444a16738ce2c134b983876205396fb32e975358b79c5497f`,
equal to this candidate's system image padded with `0xff` to 6,619,136 bytes.
Live IPC library and RWD executable hashes also matched the full-Raptor
component. This establishes persistent system installation and live use of the
new IPC library. Independent kernel/protected-partition equality is not claimed.
RAD owner observations reported available input, disabled and unmuted, with
configured codec `l16`. No microphone mutation or service restart occurred in
these verification probes. Browser microphone/audio acceptance remains pending.

The results establish host reproducibility, SD preparation and persistent system
installation, not complete camera NOR equality or firmware-release acceptance.
Microphone transitions, Preview audio and subsequent cold-boot acceptance remain
open. The next test is one explicit Preview microphone enable followed by Listen,
with no automatic retry. Do not interrupt a
write or infer success solely from elapsed time or an unspecified LED state.
Legal redistribution and the other firmware-release gates remain open.

## Follow-up playback and session-clock findings

The operator subsequently heard audio, but reported that an initial Listen
attempt was silent and a second attempt produced sound immediately. This is
not evidence of a long delayed start. After another reboot, a read-only snapshot
confirmed enabled, unmuted L16 input and an established, sending WebRTC client.
The RWD log recorded L16-to-PCMU processing; that does not prove audible browser
output or per-session audio packet delivery. The operator later reloaded Preview,
observed Listen off, and heard sound after enabling it again. Reload deliberately
resets browser playback to muted without changing the camera microphone.
First-attempt playback acceptance is still open.

The operator also reported first-login expiry within approximately 15 seconds,
followed by successful second login, and logout when Time settings changed the
camera clock. Session creation, idle expiry, maximum age and recent-auth checks
used `SystemTime` UNIX seconds. A sufficiently large forward wall-clock step
could expire a fresh session; a backward step could prolong its lifetime. This
is a source-level defect consistent with the reports, not proof that every
silent Listen attempt was caused by logout.

The follow-up source fix stores process-local session timestamps as monotonic
`Instant` values. Recent-authentication remains 10 minutes, idle expiry 12 hours
and maximum age 24 hours. Tests keep simulated wall time separate from elapsed
time, apply forward/backward wall steps and enforce recent/idle/max boundaries
through both authorization and session-status paths. This session fix is not
included in the installed bundle identified above; it requires a matching new
firmware build and physical first-login/time-change/Preview acceptance.

## Session-clock candidate build

The normal universal workflow subsequently completed from firmware-input commit
`ee822b1cd572a72f3eb4f3d887ec0124c045e19c`, with two independent complete
builds and preserve data mode. All 16 compared artifacts were byte-identical,
with no component artifact-cache reuse. Both schema-2 install-set inspections
passed. The builder image and owner-acquired vendor inputs were unchanged.

| Artifact | SHA-256 |
| --- | --- |
| New signed universal bundle | `91b3904b62f7b3f676df8e83434a42d2b2c610c2d3c5fd2eaded67c30fd0373a` |
| New system SquashFS, 6,356,992 bytes | `a2f6e501634f91a711ce908546f42e5cda23dc79ca50a7f0e7c3082e5991fb79` |
| New full Raptor component | `3cdc6bb280e2dfa7b136065db698b29a207e07c7a1022c6ffd1f7582eb22c244` |

This establishes host build reproducibility for the session fix, not its
installation or physical acceptance. No camera or SD write is established by
the build receipt. First-login lifetime, wall-clock changes and first-attempt
Listen after cold boot still require acceptance on this exact new candidate.

Public source commit `65991516419bdee23d1f7cd96908d707d317f8a5` was
published, but GitHub Actions run `35318448945` did not execute its jobs.
Rust-Control and WebUI check-run annotations independently report an account
payment or spending-limit restriction. The remote tests are unexecuted, not
passed and not evidence of a source regression. Local project checks do not
replace the missing platform and browser CI acceptance. No billing settings
were changed. Legal and other physical firmware-release gates remain open.

## Installed session candidate and Preview lifecycle correction

After the operator completed both installation phases and reported normal WebUI
availability, pinned read-only SSH and the normal universal verification passed.
Independent readback matched the candidate's system partition, FF-padded to
6,619,136 bytes, SHA-256
`b82a781315e402dc102ceb3bffa41faf9a244f9453d89225adae1d657be47c0f`.
The live IPC library and RWD matched their candidate component payloads.
This establishes installation of the session-clock system candidate above;
kernel and protected-partition equality are not claimed.

The operator's first Listen attempt was silent despite live WebRTC, Microphone
On and the Mute playback button. Later Listen returned to off without an
operator playback retry. A read-only browser observation then showed the active
video muted, playing, readyState 4, and live enabled audio/video tracks.
RAD was enabled and unmuted; RWD reported ICE/DTLS established and sending.
These observations do not establish audio RTP delivery or audibility during
the initial silent attempt. First-attempt audio acceptance remains failed/open.

Independent source consultation reproduced a separate client teardown race:
a delayed old WHIP POST response could overwrite the shared session URL and
release a newer playing peer. Its occurrence in the camera incident is not
proven. The source correction now deletes only the stale response's own session,
guards response-body completion, and attaches one remote stream per connection.
A browser-only playback controller separates listening intent, audio-track
availability and accepted playback. Temporary visibility/focus suspension
preserves intent; explicit Mute, Reload, stream change, fallback and disposal
clear it. Rejected audible resume stays muted with a click-to-retry message and
one bounded muted-video retry. Stale playback completions cannot update newer
state. Focus return cancels a pending stop deadline and resumes an elapsed stop.

Preview's Audio diagnostics action reads two bounded inbound-audio counter
samples, one second apart. It reports packet/byte deltas and supported
energy/sample counters, rejects samples from replaced connections, and exposes
neither SDP, addresses, credentials nor raw audio. Counters do not prove speaker
audibility. The action does not enable playback or mutate camera controls.

Local verification used the bundled Node runtime, the existing pinned esbuild
dependencies, GNU coreutils for the Linux-style installation timestamp test,
and an existing Chromium executable. Commands:

```sh
cd webui
npm run check
npm run test:browser -- preview-audio.spec.ts
```

The WebUI check passed typechecking, 238 tests, production bundle generation and
the bundle/route scan. All eight focused Chromium browser tests passed. They
cover both track orders, transient reconnect, pending/elapsed focus deadlines,
explicit resets, rejected playback, diagnostics and MJPEG fallback. Unit tests
also cover delayed old POST/response-body completions, stale play callbacks,
muted retry and bounded/stale stats. Synthetic-track/play mocks are state-machine
evidence, not actual Safari autoplay or camera audio acceptance. Astra High's
final read-only review found no blocking correctness issue.

The complete local project check, `./scripts/check.sh`, subsequently passed all
901 tests, public-tree/source locks, documentation and route-contract checks.
The exact source-profile contract now requires 164 installed files, including
the new playback controller; no hash or file-scope verification was weakened.

A wider, filtered run of existing WebUI/Raptor browser tests had six passes,
one skip and one failure. The failure expected `1920 × 1080 · H.264 · 20 fps`
on the Streams page. The same single test failed at the identical assertion
using an unmodified HEAD WebUI snapshot with the same Chromium/runtime fixture
environment. This is baseline evidence, not a passed broad browser suite;
the existing Streams summary-test discrepancy remains unresolved. It does not
replace the focused Preview lifecycle test results above.

The reviewed WebUI bundle is 369,024 bytes; its JavaScript SHA-256 is
`34ea9374fe23e05c76444ee7957e87e9e8d27a0904273c8e26f97c7d086029a3`.
The firmware source profile includes the new playback-controller source and
updated input hashes. This correction has not been built into a complete
firmware, installed on the camera, or physically audio-accepted. No firmware/SD
write, camera-control mutation, warm service restart or new binary publication
occurred in this source-correction round. Physical, remote CI and legal release
gates remain open.
