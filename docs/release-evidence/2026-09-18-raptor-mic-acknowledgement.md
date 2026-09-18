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
