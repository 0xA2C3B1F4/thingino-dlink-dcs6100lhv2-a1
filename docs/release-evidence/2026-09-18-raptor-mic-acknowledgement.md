# Raptor microphone acknowledgement candidate, September 18

## September 21 installed aad992b6 candidate

This section preserves the bounded `aad992b6` observations as historical
evidence. The first camera now runs `385ba106`; the Safari, audio, recorder and
Control observations below do not transfer to that later image.

Firmware-input commit `aad992b6ec46676fe8ad81319302cc91d3ca949f`, candidate
ID `69726e9a5b738f3db8fa8eaebd141399bc8b67f8f38a4462d35b9143d3518b11`,
completed both normal installation stages on the first camera. The supported
universal verifier then passed its application, Control, health, media and WebUI
gates. Independent readback compared five complete partitions: kernel and system
matched this candidate, and the three protected partitions matched the same
camera's retained originals. The install did not write protected partitions.

The bounded reproduction of the earlier state-completeness failure passed on
the new bytes. All 15 API responses succeeded, all five heartbeat samples
contained Day/Night plus boolean Privacy, microphone and speaker state, and the
checked service process IDs stayed stable through the 15-second settling period.
The probe closed its slow client, logged out and confirmed revocation of its own
session. This is short concurrent-client evidence, not overload or endurance
acceptance.

The operator used Safari with live WebRTC on both streams, connected while the
microphone was off, enabled it and pressed Listen once. Sound was audible on the
first press without Reload on each stream. This is a manual audibility result on
the exact candidate, not an automated audio measurement or a browser settings
matrix.

Separate authenticated probes decoded complete snapshot and MJPEG images at the
expected main and substream sizes without retaining frames. Both RTSP streams
enforced Digest authentication, completed setup and playback over interleaved
TCP, and decoded three H.264 video frames. The RTSP result does not cover audio,
UDP or long duration.

ONVIF Media1 and Media2 returned the expected profiles, stream URLs and snapshot
URLs, and both snapshots decoded. A follow-up Privacy check decoded three normal,
three Privacy-protected and three restored snapshots from each endpoint, 18 in
total. It independently restored Privacy off, microphone and speaker on, and the
two recorders and timelapse off. This closes the previously missing ONVIF Privacy
case, but does not claim ONVIF certification or Privacy coverage for RTSP and
WebRTC.

A later read-only session verified exactly the two videos already created by the
bounded recorder trial, without starting another recording or rebooting the
camera. The main-channel video decoded 60 H.264 frames at 1920x1080, and the
substream video decoded 55 H.264 frames at 640x360. All six files that predated
the trial remained present with unchanged sizes and modification times. Both
recorders were stopped with their files closed before and after the readback.
The immediate post-stop inventory ended in a generic assertion failure and
retained no HTTP response. A separate later read-only diagnostic returned HTTP
504, while every file-list request in the eventual readback returned HTTP 200.
The failures remain unresolved, so this does not establish immediate post-stop
file availability or complete recorder acceptance for the installed `aad992b6`
candidate.

The subsequent source adds diagnostic-only records at the files and storage-SD
backend gate and inside the Raptor SD worker. It uses static route, stage and
outcome labels plus elapsed time; it never records a path, query, body, token or
error payload. Each category logs its first eight events and then powers of two,
with successful events counted separately from failures. The records distinguish
gate success and timeout, submission full and unavailable, accepted-reply timeout
and disconnect, queued expiry, worker start and result, and late completion. No
deadline, retry, worker, route, response body or error mapping changes. This
instrumentation is included in the installed `385ba106` candidate. The new
records do not resolve the historical assertion failure and separate HTTP 504
or close full recorder acceptance. The earlier recording trial was not repeated
on the new image.

The exact diagnostic source passed the full pinned Rust 1.95 Control gate in both
default and Raptor configurations. Each configuration passed 450 library tests
with one ignored, 17 binary tests, 22 contract tests and its 1,000-request
resource soak. The WebUI gate passed one packaging test, 237 tests, the production
build and the bundle scan. These are host results, not camera evidence.

Source `385ba1064a5a2a0d543681d89099be596473db19` subsequently completed the
normal two-clean-build complete-firmware workflow with `--data-mode initialize`.
Both base and split-kernel containers exited zero without OOM. All 16 artifact
pairs were byte-identical without compiled component-cache reuse. An independent
check rehashed all 32 files, compared the bytes of all 16 pairs, and ran fresh
schema-2 inspection on both install sets.

- Complete comparison report SHA-256: `ee46cecfdbeb4d61dc529abc98797157ba0faf53ba0763b9341ba49dfef3f279`.
- Raw system SquashFS, 6,365,184 bytes: `23cb7d1604351a031fe183a4c8650c2086ef5a7358030f361472739a009ae586`.
- Signed universal bundle, 7,950,417 bytes: `651072af35c04822106681ecde2830344b3d31985a029025a40517a445fc11bf`.

This proves host reproducibility and inspection for these exact bytes only. The
same source was later installed on the first camera. The supported read-only
split-layout verifier matched the complete padded kernel and system plus the
same camera's three protected originals, with a stable boot identity throughout
the check. It did not compare mutable data or physical mtd2 and therefore does
not claim a full-flash readback. No new Safari or recording trial was run on
these bytes. An API-only storage preflight returned HTTP 504, with a
zero-millisecond gate, a reply timeout at 150 ms and queued expiry at 157 ms
before `Reader.run`.
Both stream-root directory listings then succeeded; worker execution took 81 ms
and 20 ms. No recording was started. This does not fix the older recorder
failure or close recorder acceptance. Legal, notice, binary-distribution and
other release gates are unchanged.

Source `c4b6d6eaa95396968d857c4e64e7e6d633014ccd` subsequently adds bounded
startup readiness for the Raptor SD reader before the server accepts requests,
along with the supported selected-partition verifier. Its full two-clean-build
workflow completed without component-cache reuse. Independent verification
rehashed all 32 files, compared all 16 raw artifact pairs byte-for-byte and ran
fresh inspection on both install sets.

- Raw system SquashFS, 6,369,280 bytes: `9b769de5b84791c941b3074da311ceb03636560e0f1c969672b91e245fea6037`.
- Signed universal bundle, 7,954,513 bytes: `a5843451d6ab8086edb39bf9cf151b38c1f7e19e47b97afd1fdb102db8dfd859`.

Build A emitted a tolerated host-finalization `patchelf` warning; build B did
not, and the retained artifact pairs were byte-identical. This does not claim a
clean host SDK run. The technical closure check passed, but it does not close
legal, notice or binary-distribution gates. The c4b6 firmware has not been
installed or accepted on the camera, so it adds no new device, recorder, audio
or Safari evidence.

One `aad992b6` probe session remained authenticated at 0, 10, 20 and 40 seconds,
then logout revoked it. That checks the early-session regression only. Natural
expiry and clock-change behavior remain open. The transient Privacy exercise
also does not
establish every control, the configuration field matrix or persistence across a
restart.

Three historical `aad992b6` supported runtime snapshots covered a baseline, one
held slow MJPEG connection, and 15 seconds after closing it and revoking the
probe's session.
All 12 measured processes retained their IDs. Thread and file-descriptor counts
returned to baseline, as did established TCP connections and the 8,500 KiB
available-memory proxy. The collector reported zero error counts, but review
found that it also emits zero for an unavailable log file. An independent read
confirmed that the expected log file was absent. Those counters are missing
evidence, not a kernel/media pass; the candidate ledger preserves and supersedes
the initial interpretation. Actual kernel and component log review remains open.
RHD RSS remained 348 KiB above baseline and
uhttpd RSS 116 KiB above baseline; that single-cycle measurement did not
distinguish caching from growth over repeated cycles.

A second resource probe opened and closed one MJPEG reader four times, with
15-second settling periods and six snapshots including its authenticated
baseline and post-logout state. All 12 process IDs, thread counts and file
descriptor counts stayed unchanged; established TCP connections stayed at one.
CPU ticks, RSS, PSS, private/shared memory and system memory were retained for
every sample. RHD private memory ended at its 656 KiB baseline. Its RSS reached
1,424 KiB on cycles three and four, then fell to 1,412 KiB. uhttpd RSS varied
between 848 and 952 KiB and ended below its baseline. The available-memory proxy
varied between 8,280 and 8,340 KiB. This supports the bounded resource check;
it establishes neither long-duration stability nor general absence of leaks.
The missing syslog counters remain excluded from error-log acceptance.

No older candidate result was imported. The full
candidate checklist and firmware release remain incomplete. Binary redistribution
and license review, recovery and wider provisioning, second-camera evidence and
equivalent claimed-host-platform checks remain open.

### Missing-log diagnostic correction, source only

Review of the installed collector explained the misleading zero error counts:
it emitted zero when its expected syslog file was absent. The corrected source
emits `null` for missing, unreadable or failed scans and records the source's read
status. A readable empty or no-match scan still emits zero. The host validator
checks count types and provenance consistency without relabeling older snapshots
as verified evidence.

Focused regression tests cover those cases, malformed scanner output, invalid
metadata, and legacy schema-1 input. The collector still does not read Raptor's
per-process logs or the kernel ring. Those require separate evidence. The
correction is included in the installed `385ba106` image described above. Two
supported runtime snapshots reported `null` counts with source
`/var/log/messages` marked missing. A separate pinned Control-log scan captured
the storage HTTP 504. This is not complete kernel or media log acceptance.

## September 20 heartbeat read-budget candidate

Firmware-input commit `aad992b6ec46676fe8ad81319302cc91d3ca949f` completed
the normal two-clean-build universal workflow with `--data-mode initialize`.
Both base-build containers exited zero without OOM. All 16 complete-firmware
artifact pairs were byte-identical, without compiled Raptor component cache
reuse. A separate host check reopened and rehashed all 32 files, compared each
pair's bytes, and independently validated both schema-2 install sets.

- Universal bundle SHA-256: `e4933a663622b3c501fb890026ae1bb2f1f9ec48cda719e35c65e5aa4cb367ce`.
- Complete comparison report SHA-256: `3d1c7caae55c93d9709af23d15b08eba3e9762909dd2679813e6a96461bc3d31`.

The preceding diagnostic candidate, `fb43b075`, also passed two clean builds
and complete kernel, system and protected-partition readback after installation.
Its bounded concurrent-client test nevertheless returned unknown Day/Night and
null Privacy observations. The payload-free Control diagnostics recorded a
Day/Night/RIC timeout at 151 ms and aggregate Privacy timeouts at 150 and 151 ms.
All 15 HTTP responses succeeded and checked process IDs stayed unchanged. Those
transport results did not pass the state-completeness check. The candidate was
rejected for release evaluation, with the failed test retained.

The correction increases the optional RIC read allowance from 150 to 300 ms and
the aggregate allowance for the sequential Privacy participants from 150 to
600 ms. Both remain capped by the caller's absolute deadline. Participant
agreement, unknown/null results on failure and bounded diagnostics are unchanged.
These values are a candidate correction, not proof of runtime reliability.

The final pinned Rust 1.95 Linux ARM64 Control gate passed both the default and
explicit Raptor configurations. Each round passed 446 library tests with one
ignored test, 17 binary tests and 22 contract tests, plus formatting, strict
Clippy, release builds and a 1,000-request resource soak. Seven new tests cover
delayed replies, serial Privacy participants, stalled or disagreeing readers,
and independent RIC and Privacy enforcement of the caller deadline. WebUI
typechecking, its packaging test, 237 tests and the production build passed.
The source commit's normal checks passed 1,010 Python tests with one skip.

The technical closure and notice manifests were generated locally. They do not
authorize redistribution or close legal review. At this September 20 checkpoint,
the new schema-2 candidate had not been installed, and no older audio or
physical-control passes were imported. Its first required device check was the
failed state-completeness scenario on the new bytes, followed by the remaining
full candidate matrix. The September 21 section above records subsequent
installation and bounded checks without rewriting this historical state.

## September 20 cold-boot candidate: installed and audio observed

Firmware-input commit `9902268604fd9eae52d671c616eee2e865dec933` adds a
bounded 300-second IPv4 wait before starting RWD. The installed older candidate
was observed with eight media owners alive but no RWD configuration or Control
process. Its boot log showed DHCP continuing in the background and IPv4 readiness
after the login prompt. This supports a startup race; the exact shell exit was
not traced. The new candidate subsequently completed installation and booted
successfully, as recorded below. That boot alone does not prove that the
delayed-DHCP branch was exercised.

The normal universal build completed two clean builds with `--data-mode
initialize`, intended for new installations. Both build containers exited zero
without OOM. All 16 complete-firmware comparison files were byte-identical,
without compiled Raptor component cache reuse. An independent host verifier
rehashed all 32 files against the recorded comparison; both schema-2 install-set
inspections passed. The builder image remains
`sha256:8abae43028bd1155572c76e268b9b8c5dce4686efe70ff68c0b7c5f9b93b361e`.

- Universal bundle SHA-256: `c331dbd21df864c424471d76e0af8198dc5d335b59be6d6c1c670af08881b5de`.
- Complete comparison report SHA-256: `0aec3f7bdbc744150e21e2a079ae5a84ef566f7b4f4568d779db91021cd9c20b`.

Source checks passed 1010 tests with one skip. The locked Rust 1.95 Linux ARM64
Control gate passed Clippy, release builds, real local MQTT fixtures and both
host-soak runs. Each of its default/explicit-Raptor rounds passed 438 library
tests with one ignored test, 17 binary tests and 22 contract tests. These two
rounds select the same Raptor backend; they are not independent backend coverage.
The gate also exposed ARM64-specific file-open flag errors. Those were corrected
without changing MIPS flag values. This is local Linux evidence, not hosted CI,
Windows acceptance or camera acceptance. The temporary test image contained
Mosquitto; it did not replace the locked firmware builder.

The normal staged installation and handoff completed on the first camera.
Passive installation output recorded provisioning readback, final system/kernel
readback, activation verification, installation-file passivation, SD unmount,
automatic reboot and verified switch root. The normal pinned `universal verify`
then passed application, Control, health, media and WebUI gates without writes.
An independent host readback matched the complete padded kernel and system
partitions to this build, and three protected partitions to the same camera's
retained pre-installation copies. The boot identity did not change during that
readback. The full system-span SHA-256 was
`1ca92528ef0f2a93d3f21caa94126312bfc5fc60c89da47408b85457e39a674b`.
The Raptor ready marker and live Control, storage and RWD processes were checked
after boot. No manual service restart was used.

On September 20 the operator reported audible playback on both main and
substream in response to the request to use Listen once without Reload on this
installed version. This is human audibility evidence, not a packet-count
inference. Browser version and measured audio-start latency were not recorded;
microphone-Off connection establishment and delayed-DHCP timing were not
independently observed. Do not treat this observation as the entire first-attempt
audio matrix, resource soak or failure-recovery acceptance.

The full candidate checklist and remaining firmware release gates stay open.
Prior physical acceptance below belongs to its named older artifacts, not
automatically this candidate.

## Earlier candidate evidence

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

The later audio-transport correction was built from source commit
`38607f3468cf4df58840d18ae5ea802732c67e9f` in two clean complete builds.
The normal local-build command exited successfully. Its final comparison covers
16 artifacts, including both signed installation packages, system image, full
Raptor component and split kernels. All pairs were byte-identical, with
`component_cache_used: false`. Independent host rehashing of all 32 files matched
that report; both schema-2 install-set inspections passed.

The new universal bundle is 7,938,129 bytes, SHA-256
`a3a5696a53da55766a19e22ae0f3b97ec201a27bfca1a1c47fc9cc3b2c8566b7`.
The system image is 6,356,992 bytes, SHA-256
`dd4b4bdcb72c2344aa470e4679537ee04d0cf38dadaf9838b7df22bbac643672`.
Its expected full 6,619,136-byte system-partition readback digest after padding
with `0xff` is
`67085cde490b1242bebf607788ed1d05943ccb528366bbfc2ab2429d0136280c`.
At this build checkpoint, the newer candidate had not yet been staged or
installed. The following initial camera observations concern the earlier c1
candidate; the later installation and runtime-override result is recorded below.

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
independently checked for the refreshed inventory. At that source-test checkpoint,
the correction had not yet been built or installed. Subsequent build and camera
results are recorded in this document.

The complete project check passed 904 tests. Independent Luna review found no
blocking issue for the shipped no-Opus profile and confirmed serialization of
client setup, send and teardown under the client lock. The review recommended
the explicit PCMU profile setting. Packet delivery after delayed capture,
ring reopen and audible playback remain required runtime acceptance; the host
predicate test alone does not close them. Generic Opus/video-only behavior and
pre-existing transport allocation-failure handling were not expanded by this fix.

## Installed candidate and persistent RWD override

Both normal SD installation phases completed for source commit `38607f3`.
Each host phase passed independent remount/readback of all six card files.
Native management, application, WebUI and media verification passed, and the
full system partition matched the new padded digest recorded above.

Independent component readback nevertheless failed: `/overlay/usr/bin/rwd`
contained the previous RWD and masked the new SquashFS binary. The running
process matched the old override, SHA-256
`4fd3a82152cbd2b59b11c372a160780a69e3981214f31b30d04f58e07533e106`, while
`/rom/usr/bin/rwd` matched the new component, SHA-256
`c064acfa1d805156e2e5bb71ddad8cf12505e6dfee4b2a14658b5f904f85288d`.
Thus matching firmware flash does not establish effective runtime identity when
persistent overrides are preserved.

The operator explicitly approved backing up and quarantining that one override
and rebooting once. The old binary was hash-verified in backups on the host and
camera. No configuration or recordings were intentionally changed. The existing
RWD configuration digest was unchanged; its absent audio_mode entry retains the
no-Opus build's PCMU fallback. After the reboot and service startup, the running
RWD process matched the new digest. Native verification passed again, as did
independent readback of the full system partition, IPC library and effective RWD.

With capture initially off, the operator opened Preview, waited for live WebRTC,
enabled the microphone and pressed Listen once. The operator confirmed audible
sound on that first press, without Reload or retry. The operator then disabled
capture, switched to the other stream, waited for live WebRTC, enabled capture
and pressed Listen once. Sound was immediately audible on that stream too.
Both stream attempts are therefore physically reported passed, following a
software reboot.

The operator then performed the requested power-off cold boot and reported that
WebUI responded. A fresh pinned read-only probe confirmed a changed boot ID,
the same expected full system-partition digest and the new running RWD process
digest, with no active binary override. Microphone owner readback was available
and input was disabled. The operator repeated the first-attempt sequence on
both streams and confirmed that both worked. This closes the specific
capture-disabled-at-connection -> microphone-On -> one-Listen regression for
this candidate and camera, including the reported cold boot. Physical audibility
is operator-reported, not inferred from packet counters or service readiness.

The remaining candidate matrix and public firmware-release gates remain open.
These successful audio observations do not establish licensing closure, other
host platforms, second-camera acceptance or failure/recovery coverage.

### Post-acceptance resource baseline

Two read-only `diagnose-runtime` snapshots at 14:46:41Z and 14:47:15Z on
2026-09-18 still matched the expected full system and running RWD digests.
Across this 34-second baseline, all 12 sampled processes retained their PIDs,
thread counts and file-descriptor counts. Their RSS did not increase. The
available-memory proxy changed from 9,944 to 9,920 KiB; this is a reclaimability
estimate, not the kernel's `MemAvailable`. The network counts were unchanged.
No new media sessions or settings changes were introduced for this collection.

`/var/log/messages` was absent. The snapshot's zero Raptor/ISP error counters
therefore cannot establish absence of errors. A separate successful `dmesg`
read captured 20,858 bytes, SHA-256
`bbb5ae96f32beb72a596ab9582062e784ef7dea84599573724b6f64c7d4d87c1`,
with no matches for the recorded OOM, panic, allocation,
segfault and selected ISP/encoder failure patterns. This is limited to the
retained kernel ring and that filter, not a complete media-log review.

Private content-addressed snapshots and the kernel acquisition receipt retain
the collection details. This short baseline does not close the resource,
concurrency or kernel/media-error acceptance rows under media load.

### Snapshot and MJPEG ingress acceptance

Two authenticated HTTPS snapshot requests through the production ingress
returned HTTP 200 and `image/jpeg`. Complete JPEGs decoded on the host at
1920x1080 for stream 0 and 640x360 for stream 1. Images were processed in
memory, not retained. This verifies the snapshot endpoints, not browser saving.

Serial four-second HTTPS MJPEG samples returned HTTP 200 and multipart JPEG.
The host decoded four complete, distinct main-stream frames and three complete,
distinct substream frames at the same respective dimensions. Incomplete final
parts were excluded. Curl's deadline exit was expected for the continuous
response; success required valid HTTP, MIME framing and decoded frames.

Pinned SSH authenticated the camera; HTTPS requests stayed on its loopback
interface with its self-signed certificate. The API key remained in camera
pipe memory. No camera settings, persistent files or services were changed.
Before and after each probe group, boot identity, full system digest and running
RWD digest matched the accepted candidate. The private candidate ledger records
the four endpoint checks. No browser rendering, reconnect, concurrency or
long-duration resource acceptance is inferred from these bounded samples.

### Main-stream RTSP transport probe

The existing bounded installer RTSP verifier passed on the same candidate's
`/stream0` over interleaved TCP. It observed unauthenticated rejection,
successful Digest authentication, 26 H.264 RTP packets with two timestamps,
a complete IDR and SPS dimensions of 1920x1080. Boot, system and running RWD
identities still matched before and after the test. The credential was derived
from the existing private installer session in memory and not logged.

This checks RTP framing and SPS metadata, not decoded pixels, RTSP audio or the
substream. The combined RTSP acceptance row remains open pending those coverage
decisions and further tests; ONVIF was not exercised in this probe.

### ONVIF read-only ingress observations

Unauthenticated SOAP `GetSystemDateAndTime` and `GetCapabilities` returned
HTTP 200 with their expected XML response elements and no SOAP fault through
production HTTPS ingress. Unauthenticated `GetProfiles` returned HTTP 401 and
a SOAP fault. Boot and full system identity matched before and after collection.

A subsequent `GetProfiles` attempt using HTTP Digest and the existing installer
service credential also returned HTTP 401. Authenticated profile acceptance
therefore remains unresolved. Check the service's supported authentication
mechanism and current credential binding before attributing this to a firmware
defect; HTTP Digest and SOAP WS-Security are distinct mechanisms. No credentials
or service settings were changed, and the ONVIF acceptance row remains open.

The subsequent source check at the exact built ONVIF revision,
[`4ca8f063`](https://github.com/themactep/thingino-onvif/blob/4ca8f063563b96631d04e1ed7ceb2244d413f736/src/onvif_simple_server.c),
showed that protected SOAP requests validate a WS-Security UsernameToken and
PasswordDigest. Retesting `GetProfiles` with that mechanism and the same
credential returned HTTP 200 with the expected response and two profiles,
1920x1080 and 640x360. No firmware or credential change was needed. The earlier
HTTP-Digest-only 401 is retained as a test-method limitation, not evidence of a
camera authentication defect. Discovery, returned media URIs and wider ONVIF
operations still require acceptance; a successful profile read alone does not
close the combined protocol row.

Subsequent authenticated URI lookup exposed a release blocker. Both profiles
returned legacy RTSP paths `/ch0` and `/ch1`, while current RSD access status
reported `/stream0` and `/stream1`. The same bounded verifier that passed
`/stream0` failed at authenticated DESCRIBE for ONVIF's returned `/ch0`.
The returned snapshot paths were also legacy `/x/ch0.jpg` and `/x/ch1.jpg`, not
the current ingress routes. URI query values are redacted from evidence.
The source of these stale URLs, including preserved configuration versus image
defaults, still requires diagnosis. ONVIF must not be accepted on profile reads
alone; advertised media URLs must work end to end.

Read-only comparison then found the same legacy `profiles.stream0/stream1`
`url` and `snapurl` fields in both effective `/etc/onvif.json` and
`/rom/etc/onvif.json`. Thus this is not only a preserved-overlay artifact.
At the pinned upstream revision, Media service constructs returned URIs from
those profile fields. Current image composition and provisioning update ONVIF
credentials but do not reconcile these media endpoints with live RSD settings.

The fix must also address snapshot authentication. The existing Control
contract deliberately rejects unauthenticated `/onvif/image.cgi` and
`/onvif/image1.cgi`, including requests carrying only the internal proxy marker;
its tests authorize them with the WebUI API key. Merely changing `snapurl` to
those routes would not establish interoperability for an ONVIF client that has
no WebUI cookie or API key. Do not bypass authentication to make this pass.

Required regression coverage includes fresh and preserved old configuration,
both profiles, runtime RTSP endpoint/port changes, an ONVIF-authenticated client
fetching the returned snapshot without a WebUI session, rejected unauthorized
snapshot requests, and failure when live media state cannot be established.
The implementation and physical retest are still pending.

During local implementation, a short-lived snapshot-ticket design was tested
but rejected before publication or installation. Media2 section 5.5.1 requires
the returned snapshot URI to remain valid indefinitely, so expiring the URI
after 30 seconds would break that contract even though the local ticket tests
passed. The experimental ticket state and HTTP routes were removed; the separate
live RTSP URI bridge changes remain under development.

The snapshot correction must instead expose a stable URI with independent HTTP
Digest authentication, retaining normal access checks and avoiding credentials
in URLs. Nonce expiry must not expire the resource URI. References:
[Media2 21.12, section 5.5.1](https://www.onvif.org/specs/2112/ONVIF-Media2-Service-Spec-v2112.pdf)
and [Core 22.12 authentication](https://www.onvif.org/specs/2212/ONVIF-Core-Spec-v2212.pdf).

Read-only design review selected the persistent ONVIF daemon as the Digest
authentication owner, reusing its effective SOAP credentials. Control's WebUI
authentication remains unchanged. Implementation must cover the entire proxy
path: exact snapshot GET routes to the ONVIF daemon, forwarded Authorization,
Digest challenge response headers and binary JPEG responses, without the old
proxy-marker/local-file authorization flow. The fixed internal Control snapshot
action delegates capture to RHD. RHD checks privacy before reading camera frames
and returns a privacy image, or 503 if that image is unavailable. Physical
acceptance must verify that private camera pixels never reach the snapshot.

The snapshot relay needs a separate binary transfer path, not the existing
32-KiB JSON helper or 256-KiB CGI response buffer. Control admits JPEGs up to
2 MiB. Use bounded headers and a small transfer buffer, validate framing and
status, and enforce one monotonic request deadline across client input, backend
access and output. Nonces and replay counters must have bounded storage;
discarding replay counters must invalidate their nonce. Stable URLs must remain
usable with fresh authentication after nonce expiry and daemon restart.

These are implementation requirements, not completed checks. Actual ONVIF
crypto build capability, credential rotation, proxy challenge round-trip,
large JPEGs, privacy denial and slow-client recovery still require validation.
No snapshot authentication fix has been installed. A separate bridge parser
change now checks boolean value length before advancing its pointer; the
focused bridge and source-preparation suite passed 36 tests, including short
and malformed boolean rejection. This host result does not close the failed
physical ONVIF candidate check.

The new `0002-snapshot-digest.patch` now supplies a standalone C authentication
core and is included in the source profile. It is not yet linked into the daemon
or exposed through HTTP. It implements MD5 `qop=auth`, random nonces with
monotonic expiry, bounded replay counters and credential-change invalidation.
Challenge reuse prevents unauthenticated requests from continuously evicting
pending nonces. The caller contract requires exact GET routing and rejection of
embedded NUL bytes and duplicate Authorization headers before verification.
Core 22.12 section 5.9.2 specifies MD5 as the default algorithm and SHA-256 as
optional; this does not claim general Digest algorithm support.

After read-only review and corrections, 53 focused host tests passed. Coverage
includes both snapshot paths with an actual curl Digest client against a local
test server, nonce expiry and restart, observed credential rotation, replay
counter maximum and wrap, bounded-table pressure, malformed headers and crypto
callback failures. AddressSanitizer and UndefinedBehaviorSanitizer passed an
exact-allocation header-truncation sweep and single-byte mutations. Hashlib
provides the test hash callback; these checks do not prove the production crypto
binding, uhttpd challenge forwarding, camera JPEG transfer or deadline behavior.
Those integration steps and physical ONVIF acceptance remain open. No new
firmware has been built, published or installed for this work.

The same patch now contains the bounded binary snapshot relay and deadline I/O
helpers. They connect only to loopback Control, use the fixed stream action with
a Bearer token, validate response headers and JPEG framing, and cap the image
at 2 MiB. The relay uses a small transfer buffer rather than allocating the full
image. One monotonic deadline governs connect, input and output. After a partial
response, failure closes the stream instead of appending a second HTTP response.
Host network tests cover both streams with 1,280,004-byte binary images,
fragmented headers and JPEG prefix, malformed and denied backend responses,
truncation, slow headers/body and client backpressure. This does not yet test
the production daemon or HTTPS proxy integration.

Inspection of the existing universal SquashFS found a defined global dynamic
`mbedtls_md5` function in its MIPS `libmbedcrypto.so.3.6.6`. Library SHA-256:
`8aad205f70da256100a0206053de4123151798e85328ae45d965de843ffb591b`.
The separate production callback calls that API and fails compilation if
`MBEDTLS_MD5_C` is absent. Target compilation/linking, effective-credential
loading, strict HTTP ingress, structural Control-token loading and proxy wiring
remain to be completed before this is an installable correction.

The expanded host suite passed 61 tests after catching and correcting a blocked
socket send on macOS. Per-call nonblocking flags alone did not bound that send;
the I/O helpers now explicitly set `O_NONBLOCK` before the deadline-driven
operation. The backpressure regression runs in a child process with an outer
timeout so a future regression cannot hang the whole suite. The interrupted
earlier run is not a passing result. Its small generated test library and raw
stack sample are disposable diagnostics; the regression and this note retain
the useful evidence in source.

The local implementation now includes daemon credential loading, strict snapshot
HTTP parsing, a structural Control-token reader and uhttpd Digest forwarding.
The focused host suite passed 77 tests. A follow-up review found that the relay
previously emitted the complete advertised body before checking the JPEG ending.
It now withholds the final two bytes until validating the EOI marker. Regression
tests cover both an ending received in the initial read and a fragmented ending;
invalid images leave an incomplete response instead of a completed success.
These host checks do not prove target compilation, the running HTTPS proxy or
physical ONVIF acceptance. No new ONVIF firmware has been installed or published.

Astra high's follow-up review accepted the revised JPEG tail handling but found
a blocking lifetime issue in pinned upstream `conf.c`: `process_json_conf_file`
retains its parsed JSON tree until process exit. Repeated requests in the
persistent daemon therefore leak that tree. Retained configuration strings are
copies, so the tree can be released on each post-load exit. Before rebuilding,
also correct failed profile reallocation and relay-event overflow counts, plus
pointer-owned relay token, event and PTZ cleanup. These findings remain open;
passing host snapshot tests do not cover this upstream configuration lifecycle.

The full source check passed policy, documentation, source-lock and Control
contract checks, then ran 950 tests with one failure: the ingress manifest lacked
the new uhttpd patch. The manifest now pins patch 0018 and its targeted test passes.
The full suite has not yet been rerun after that manifest correction.

## Target compile checks before configuration lifetime correction

The ONVIF daemon with patches 0001 through 0003 compiled and linked with the
existing MIPS toolchain and target mbedTLS library. Two media include hunks first
failed GNU `patch -F0`; their context and coordinates were corrected, then the
entire patch sequence applied with zero fuzz and the daemon build passed.
uhttpd with patch 0018 also compiled and linked with TLS enabled and Lua, ubus and
ucode disabled. Both outputs are MIPS32r2 ELF executables. Neither successful
compile log contains a compiler warning or error.

These checks used immutable builder image
`sha256:8abae43028bd1155572c76e268b9b8c5dce4686efe70ff68c0b7c5f9b93b361e`
and the earlier 38607f3468cf build-a workspace mounted read-only with
`loop,ro,noload`. New build trees were separate scratch copies. The pristine
ONVIF source archive from that workspace has SHA-256
`67998a0e2a2a7edf2641e32532fdbf2ebd0297f82f62dca1d6fb383e4b59229d`.
This is a component compile check, not a clean firmware rebuild, runtime HTTPS
test or camera acceptance. The configuration lifetime finding remains open.

The subsequent full `scripts/check.sh` run passed all 950 tests after the
manifest and patch-context corrections, along with source policy, source-lock,
documentation and Control contract checks. This run precedes the pending
configuration-lifetime patch and does not close its regression requirement.

## Native HTTPS process integration before lifetime correction

Seven tests passed using actual native Linux uhttpd and ONVIF daemon processes,
an actual TLS connection and a bounded fake Control HTTP server. Both snapshot
routes authenticated with curl Digest and relayed an exact 1,048,580-byte body.
Missing/wrong credentials, plaintext HTTP, duplicate Authorization, a query
suffix and a forged proxy marker did not reach the snapshot backend. Effective
credential rotation worked without a process restart. A backend 503 produced
502, not a successful JPEG. The test is retained as
`scripts/check_onvif_https_chain.py`; the promoted copy was rerun successfully.

This tests proxy, authentication and byte transport, not camera capture or JPEG
decoding. It used patches 0001 through 0003 and uhttpd 0018, before the pending
configuration-lifetime correction. It must be rerun after that correction.
The test uses generated short-lived TLS credentials and dummy service credentials
only, and refuses to run without `RAPTOR_TEST_CONTAINER=1` and `/deps` present.

The isolated `--network none` ARM64 builder reused cached mbedTLS 3.6.6,
Mini-XML 4.0.4, JCT 1.2.0, libubox 1fe93d2fefb213ec987763e7e94ce5eaa757bfc3
and ustream-ssl 99f1c0db5a729f615bc5114b3b52fd8ac8083f34 sources. Native uhttpd
needed `-Wno-error=stringop-overread` for the host GCC warning in the existing
blob header iterator. The warning remained visible. Production MIPS flags were
unchanged and both earlier MIPS component builds passed without warnings.

Reproduction requires the native binaries at `/deps/build-uhttpd/uhttpd` and
`/deps/onvif-host/onvif_httpd`, with their native libraries at `/deps/prefix/lib`.
Mount these task-owned test builds into the immutable builder, set
`LD_LIBRARY_PATH=/deps/prefix/lib` and `TMPDIR=/deps/tmp`, mount the retained
script read-only, then run it with Python 3. The script starts and terminates its
own processes and removes generated certificates and response fixtures.

## Configuration lifetime correction and revalidation

ONVIF patch 0004 now frees the parsed JSON tree on every post-load exit, preserves
allocation ownership and valid counts on failure, and frees relay tokens and
disabled PTZ/event configuration. The previous relay capacity is preserved.
Configured credentials that are incomplete, invalid, empty or unsuccessfully
copied cause loading to fail before SOAP dispatch. Existing per-field nested
server precedence is unchanged. Both credential fields absent still retains the
upstream anonymous-configuration behavior; snapshots separately reject absent
credentials. Astra high reviewed the correction and closed its credential-copy
finding without reporting another blocker in that delta.

Five explicit lifecycle tests passed using the pinned full `conf.c` and JCT
sources, not only extracted snippets. The harness tracks allocations through
repeated loads and sweeps allocation-failure positions, including root, nested
and overriding credential fixtures. Each successful credentialed load must
retain the exact expected pair. Disabled PTZ/events, relay-event overflow and
cleanup are covered. These full-source tests require `ONVIF_LIFECYCLE_SOURCE`,
`ONVIF_LIFECYCLE_JCT` and `TMPDIR`; an ordinary run without them skips that class.

The corrected daemon compiled and linked for MIPS and native ARM64. A forced
`make -B` rebuild compiled every daemon translation unit again. Make reported a
14–16 ms future timestamp on the shared scratch Makefile, but no compiler or
linker errors occurred; the forced rebuild did not rely on timestamp freshness.
All seven actual-process HTTPS tests then passed with patch 0004 included.
This still does not constitute two clean firmware builds or device acceptance.

The complete `scripts/check.sh` run with both lifecycle source environment
variables explicitly set passed 955 tests, along with policy, source-lock,
documentation and Control contract checks. The corrected source is ready for
the next full firmware build, not for a firmware release declaration.

## Corrected ONVIF full-firmware build pair

The subsequent normal `local-build build-universal --build-count 2
--data-mode preserve` run completed successfully from source commit
`14b81c671931b038545b0d9177a4609ad8818e91`, using the immutable builder image
recorded above. Both clean builds included ONVIF patches 0001 through 0004
and uhttpd patch 0018. The complete-firmware report compared 16 artifacts
byte-for-byte, with no compiled component cache reuse. Both schema-2 install-set
inspections passed. An independent host read rehashed all 32 files against
the report and checked their sizes and both inspection results.

| Artifact | SHA-256 |
| --- | --- |
| Signed universal bundle | `89d59476baa455bd67aa15f96f3a48d7a5f5c88f74aa15baccc35d0fbc221f37` |
| System SquashFS, 6,365,184 bytes | `bc31911422c0359bb0fbefb557a1e21496acb2bf2d3d545f8e987da5bde9a13d` |
| Full Raptor component | `47744a5e3411b870b083ddec896e7c1c42d5b83270b59b399c3d6f553ef6e524` |
| System image padded to its installation span | `5d929ca52e5cc5e185a95784f796d316e8ebbe9fbd41cb02af4c5d5da5b18b0e` |

The packaged RWD hash remains
`c064acfa1d805156e2e5bb71ddad8cf12505e6dfee4b2a14658b5f904f85288d`.
That equality does not transfer earlier device acceptance to this new firmware.
The build result establishes host reproducibility only. This version has not
yet been staged or installed, and its physical ONVIF, browser, first-press audio
and remaining candidate checks are open. Source/license delivery, recovery,
provisioning, second-camera and host-platform release gates remain separate.
