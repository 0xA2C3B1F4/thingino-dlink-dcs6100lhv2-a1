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
updated input hashes. At the end of this source-correction round, the correction
had not yet been built into a complete firmware, installed on the camera, or
physically audio-accepted. No firmware/SD
write, camera-control mutation, warm service restart or new binary publication
occurred in this source-correction round. Physical, remote CI and legal release
gates remain open.

## Preview lifecycle candidate build

The normal `local-build build-universal` workflow subsequently completed with
exit 0 from firmware-input commit
`c1e6d93b442abcacfb784f6c146ca301eee48051`, using two clean builds and
preserve data mode. The immutable builder image was unchanged. The complete
firmware comparison found all 16 artifacts byte-identical, with no differences
and no component artifact-cache reuse. Both schema-2 install-set inspections
were accepted.

| Artifact | SHA-256 |
| --- | --- |
| Signed universal bundle, 7,938,129 bytes | `fd676c516bcbbbc02fb7e5c99854837e6866c3baf52b0677ad518e6199876013` |
| System SquashFS, 6,356,992 bytes | `d53ce7485c2632cee81eb20c87ad48424d04cb21931020a64466cc7550bee568` |
| Full Raptor component | `e69a4bc6c96608d9e7fbe920cf631e7684bb8c3c14d35deb7180a7be51b40a37` |
| Final kernel | `faf28040e59639fb9818b3cc12ead8c1c5a0f8109d01ab693fb579797dd3e383` |

The base manifest binds the reviewed 369,024-byte WebUI bundle and JavaScript
hash above. The normal workflow reports Raptor-only media, full source build,
no physical actions and no NOR writes. The bundle hash was independently read
from the completed install-set on the host.

At build completion, this candidate was host-built and inspected but had not been installed or
physically accepted on the camera. The installed session-clock candidate above
was then the latest verified camera state. First Listen after cold boot, both
streams, playback after temporary visibility loss, first-login lifetime and
clock-change session behavior still require acceptance on this new candidate.
Source-side notice additions and the verified source inventory do not close
legal redistribution gates. Public CI and the remaining firmware-release gates
remain open.

## Required microphone-enable playback acceptance

The operator subsequently completed the two installation phases. Strict-pinned
SSH readback of the full system partition matched the Preview candidate padded
to 6,619,136 bytes, SHA-256
`6118866901cf3e12b2513e04d48c7c1b10f67293cc46cc6f5d4d35b81daae6f0`.
Normal universal verification stopped at mDNS resolution; complete runtime
acceptance is still open.

A later management-only verification on 2026-09-18 passed through the normal
`universal verify` route, with phase
`camera-bound-universal-management-verified`. The existing station host pin was
reused, mDNS resolved successfully, and the reported full system-partition digest
matched the c1 candidate above. No service restart or configuration change was
needed. The earlier discovery timeout remains unexplained. This retry proves
management access to the installed c1 candidate, not cold-boot discovery
reliability, audio acceptance, or installation of the later audio-transport fix.

The operator enabled the microphone manually, reported silence on the first
Listen attempt, then heard audio after Reload and another Listen click. A later
RAD observation reported input disabled; the operator confirmed they had
manually disabled it afterward. That later observation does not explain the
initial silence. Failure to update an existing WebRTC connection after microphone
enable is a hypothesis requiring evidence, not an established root cause.

The release-readiness goal explicitly includes diagnosing and fixing this case:
with Preview already open and the microphone off, enable the microphone and
press Listen once. Audio must become audible without Reload or a second Listen
attempt. Verify this on both main and substream, including after a cold boot.
If it fails, retain the original session and collect input state and bounded
inbound-audio diagnostics before retrying. Physical audibility is required;
ICE/DTLS establishment, a live track, or a resolved play promise alone is
insufficient. Keep this acceptance requirement open until verified.

Source inspection of the installed candidate identified a specific missing
transport path. In reconstructed `raptor/rwd/rwd_media.c`, client setup creates
`c->rtp_audio` only when `srv->audio_ring` is already open. The audio reader can
open that ring later, but does not create the missing client transport. The
send routine returns immediately when `c->rtp_audio` is null. SDP can meanwhile
advertise sendonly PCMU audio even before the ring exists. Thus a client that
connects while the microphone is off can remain silent after it is enabled;
a fresh connection after the ring opens can obtain its audio transport.
This source defect fits the operator sequence. The failed browser session's
packet counters were not captured, so occurrence in that session is not proven.

The next correction must separate negotiated client audio transport lifetime
from temporary microphone-ring availability, retain offer-direction checks,
and test connection-before-microphone, later enable, disable/re-enable and
rejected audio directions. Codec/clock consistency between SDP and the sender
must also be retained. The currently shipped camera profile uses L16 capture
transcoded to PCMU; broader codec transitions need explicit handling rather
than assuming every ring re-open uses the same RTP clock.

The follow-up source patch removes the ring-availability condition from client
audio transport setup while retaining offer-presence, payload and direction
checks. The camera profile explicitly selects PCMU so SDP and client transport
remain at payload 0 and 8 kHz across supported L16/PCMU/PCMA capture changes.
The existing reader decodes/transcodes capture frames into that fixed wire
format. This does not enable microphone capture or browser playback.

A compiled C regression executes the setup predicate extracted from the
shipped patch. It fails before the fix with capture absent and passes afterward,
including subsequent ring presence/absence and rejected sendonly/inactive,
missing-audio and invalid-payload offers. It verifies the setup decision, not
end-to-end packet delivery or physical sound. Fifteen focused source/manifest
and inventory tests passed. Clean patch application reconstructed Raptor tree
`0f05599901aeb93e6d1ab65f46abb04c9a65884a`; all 13 source trees were
independently checked for the refreshed inventory. This correction has not
yet been built into a new firmware or installed on the camera.

The complete project check passed 904 tests. Independent Luna review found no
blocking issue for the shipped no-Opus profile and confirmed serialization of
client setup, send and teardown under the client lock. The review recommended
the explicit PCMU profile setting. Packet delivery after delayed capture,
ring reopen and audible playback remain required runtime acceptance; the host
predicate test alone does not close them. Generic Opus/video-only behavior and
pre-existing transport allocation-failure handling were not expanded by this fix.
