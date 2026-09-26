# September 26 verification of the fresh-default Raptor build

Source `645fec1ce5f0c262541eaff00d50e946265e99bd` was built in run
`build-20260925T170308006681Z-645fec1ce5f0`. The supported model-universal
builder used `--data-mode initialize --build-count 2`. Both clean base
containers exited with status 0 and without OOM. The complete-firmware
comparison reports two builds, no component cache, accepted inspections,
no differences and byte-identical results. The SHA-256 of
`reproducibility.json` is
`8e9a1b16e8cd8a9d34d8edd3fed205eeff00eabc7f2a1df024e5ccd119e86caa`.

The signed universal bundle is 7,958,609 bytes with SHA-256
`9744c5b3ce02ec53963492766cacc5351f350b60e132f53747593f5fcb60fc0b`.
Its final Raptor system SquashFS is 6,373,376 bytes with SHA-256
`bf2c27f5825e3650423f7368b5b5b1fbf61f6c83fdeee44b793d88b99a8aa836`.
The separate `inspect-install-set` command passed schema 2, the fixed
`dcs6100lhv2-a1-mtd3-split-v1` layout and initialize mode. System and data
spans are 6,619,136 and 1,507,328 bytes respectively.

`scripts/release_closure.py` independently validated the completed run and
signed bundle using the retained model public key. Its technical closure
binds the embedded system digest to the final Raptor system above. This does
not authorize redistribution or close legal review.

The run's pinned Buildroot `unsquashfs` was used through its generated
workspace wrapper to read `etc/raptor-media.conf` directly from the final
Raptor SquashFS. It contains `[sensor] antiflicker = 2` and
`[stream0] enabled = true`. These are the two fresh-install saved-state fixes.
A file-list check found `usr/bin/rad`, `usr/bin/rvd`, `usr/bin/rwd` and
`etc/init.d/S96raptor`; the checked `usr/bin/prudynt`, `usr/bin/prudyntctl`
and `etc/init.d/S31prudynt` paths were absent.

The first clean base SquashFS, Linux configuration, base manifest and source
preparation report are also byte-identical by `cmp` to the corresponding
first-build outputs of the installed `867d1182` version. The Raptor component
archive digest remains
`2695c60dae4d974be8da8c9147db03b885ff133b91cb8a9df85cfc4e1206b1e3`.
Neither comparison transfers the earlier camera acceptance to this new final
system image.

The build checks above did not write SD or camera storage. They do not by
themselves establish device acceptance or firmware-release readiness.

## First-camera installation and fresh-state reads

The same bundle was subsequently staged through the supported installer on
the first camera's identified SD card. The previous stock backup was copied,
verified and retained on the host before its card copy was removed. No card
formatting was performed. UART captured the stock updater's explicit success
line before the host handoff made its matching filename passive.

The second boot recorded provisioning data written and verified, final system
and kernel-tail writes, preactivation kernel verification, activation written
and verified, installation-file passivation, SD unmount and final reboot.
The following boot reached `thingino_verified_switch_root`.

`universal verify` passed authenticated management, application, media and
WebUI checks. The separate `universal verify-readback` passed against the new
signed bundle, covering logical partitions 0, 1, 3, 5 and 6. This is not a
whole-flash comparison: mutable data and exact physical mtd2 contents are
outside that readback. Stock bootstrap wrote physical mtd1/mtd2; final
installation wrote physical mtd1/mtd3. Protected physical mtd0/mtd4/mtd5
were verified by the selected readback.

Before any settings save, a separate authenticated HTTPS test session used
the camera certificate obtained through pinned SSH, with certificate and
hostname validation enabled. Image quality reported anti_flicker live,
configured and saved values all equal to 2, with `matches_saved: true` and
`verification: sdk-readback`. Main stream enable_control reported active,
configured and saved enabled values all true, `saved_available: true`,
`configured_matches_saved: true` and `pending_restart: false`. These reads
confirm the two fresh-default corrections on the installed bytes. The test
session logged out with HTTP 204; it did not save settings.

The same main-stream response also reported unavailable saved rate-control
state and a null saved encoder profile. These are follow-up observations,
not accepted settings behavior; their cause and UI impact remain to be
classified. New-candidate browser/audio acceptance, the rest of the full
candidate matrix and release/legal gates remain open.

The source follow-up identified missing `rc_mode` and `profile` keys in both
fresh stream sections. The exact build input's `rvd/rvd_pipeline.c`
`load_stream_config` uses defaults `cbr` and `2`, whereas Control's
`stream_disk_encoding`, `stream_disk_rc_config` and `stream_disk_profile`
require explicit saved values for their observations. Both stream sections
now declare those existing defaults. A regression test failed for both
streams before the change and checks their saved encoding defaults; the
composition test also checks the keys in the packed-root fixture.
`init_qp` remains absent: CBR permits no explicit QP and RVD uses its unset
sentinel. The GOP-mode reader already handles its documented `default` value
when the key is absent. The stream audio field has a separate explicit-user-
selection contract; this correction does not change it.

These additional source corrections are not included in the installed
`645fec1c` bundle. Its installation and readback evidence remains valid for
that bundle only.

## Follow-up build with explicit stream encoding defaults

Source `46fe7be5fa061630de620738e96965da4d8663f1` was subsequently built in
`build-20260926T061621701161Z-46fe7be5fa06` through the same supported
initialize-mode, two-clean-build workflow. Both base containers exited 0
without OOM. The complete-firmware comparison reports two byte-identical
builds, no component cache, no differences and accepted inspections. Its
`reproducibility.json` SHA-256 is
`b5e317c4e8d8863d5392c26f2f82d853364b5592b9e92d7bcb6e95f7b47a93b0`.

The new signed bundle is 7,958,609 bytes with SHA-256
`f891e0b010de1007bf432b69976226b7d2c6d01872e35cf0fafa85577ef83d06`.
The final system SquashFS is 6,373,376 bytes with SHA-256
`a5c0bf7ea4b19adda5c478f5d65f746de55c8489a25a8aa27379a3da6223ce54`.
The separate `inspect-install-set` check and `scripts/release_closure.py`
both passed. The latter used the retained model public key and establishes
technical provenance, not redistribution permission.

Reading `etc/raptor-media.conf` from the final system with the run's pinned
`unsquashfs` confirmed both streams have `profile = 2` and `rc_mode = cbr`.
The earlier `antiflicker = 2` and main-stream `enabled = true` fixes remain.
The Raptor component archive digest is unchanged from the build above.

The new schema-2 Raptor candidate ID is
`82860c27f42e2a65314504cc242f52a187ee73761997e0687290ec82afbf0a44`.
No camera or SD writes were made by the build and package checks. Earlier
firmware's browser/audio results are not claimed as acceptance of this bundle.

### Follow-up installation and fresh defaults

The new bundle was then installed on the first camera through supported SD
staging and handoff. The old stock backup and checkpoint were copied and
verified on the host before their SD copies were removed. No formatting was
performed. UART captured the new stock-updater success line, then final
provisioning verification, system and kernel-tail writes, preactivation and
activation verification, installation-file passivation, SD unmount and final
reboot. The following boot reached `thingino_verified_switch_root`.

`universal verify` passed authenticated management, application, media and
WebUI checks with the retained SSH pin. Independent `universal verify-readback`
passed against this signed bundle for logical partitions 0, 1, 3, 5 and 6.
The exact comparison excludes mutable data and physical mtd2; this is not
whole-flash verification. Physical writes were mtd1/mtd2 in the stock phase
and mtd1/mtd3 in the final phase.

At 2026-09-26T14:28:59Z, authenticated HTTPS reads before any settings save
confirmed both streams expose saved CBR encoding, matching live bitrates
of 1,500,000 and 400,000, and saved/live profile 2. Both rate-control
observations report `saved_available: true`, `matches_saved: true` and
`pending_restart: false`. Stream enable state and anti-flicker 2 also match
their saved values. Logout returned 204. An initial host reader incorrectly
assumed all routes used the same response envelope; it was corrected without
changing firmware or saving settings.

These observations establish the fresh-default fixes on the new installed
bytes. The candidate is not yet accepted: its remaining browser, device,
field-matrix and release/legal requirements are still open.

### Bounded live media observations on the follow-up candidate

On September 26 between 14:33 and 14:41 UTC, the installed candidate also
passed authenticated snapshots and MJPEG transport checks on both streams.
Snapshot JPEGs fully decoded as 1920x1080 and 640x360. Each MJPEG response
provided three distinct, fully decoded JPEG frames with the correct dimensions;
multipart boundaries and content lengths were checked. HTTPS used hostname
validation and the camera certificate obtained through pinned SSH. Test
sessions logged out with 204. Images were processed in memory, not published.

The repository's bounded RTSP verifier confirmed unauthenticated 401 responses,
Digest-authenticated OPTIONS/DESCRIBE/SETUP/PLAY, H.264 SPS dimensions,
complete IDR pictures and advancing RTP timestamps over interleaved TCP for
both streams. An initial test invocation used the legacy `viewer` username;
the current Raptor provisioning source specifies `root`. The corrected test
passed without changing credentials or weakening authentication. These are
video transport observations, not RTSP audio or endurance claims.

ONVIF Media1 and Media2 GetProfiles, GetStreamUri and GetSnapshotUri returned
both expected profiles and matching stream/snapshot routes. The two ONVIF
snapshot routes separately enforced HTTP Digest and returned fully decoded
JPEGs at the expected dimensions. SOAP requests used the camera's clock for
WSSE, without modifying it; this does not establish clock synchronization.
These observations do not establish discovery or event behavior.

The two snapshot, two MJPEG and RTSP checks are recorded in this candidate's
ledger. The real Chromium test reached the new installation's certificate
warning after a stale tab displayed `Failed to fetch`; browser acceptance
awaits the user's certificate handling and login. No browser warning was
bypassed, and the direct protocol checks do not substitute for WebRTC/audio
or rendered browser acceptance. No camera settings were saved during these
bounded media reads.
## Bounded runtime observations on the installed 46fe7be5 candidate

At 14:50 UTC on September 26, a separate authenticated test session issued two
concurrent imaging GETs while another HTTPS connection held incomplete request
headers. Both GETs returned HTTP 200 in 0.858 and 0.844 seconds. The incomplete
connection was then closed. Eight subsequent GETs all returned HTTP 200 in
0.086–0.181 seconds. No settings were written and no service was restarted.
The test session logged out with HTTP 204; reusing its cookie then returned
HTTP 401. Natural expiry and expiry across clock changes remain untested.

Across the roughly five-second observation, the main Control process retained
16 threads, five file descriptors and one socket; RSS changed from 1944 to
1852 KiB. RVD retained 28 threads and 30 descriptors; RWD retained three threads
and 12 descriptors. Their PIDs remained unchanged. Shared memory stayed at
5736 KiB and established TCP connections at two. The sampled kernel ring had
no matches for the checked OOM, panic, segfault, Oops or BUG patterns. The
sampled syslog had no checked RVD/RAD/RWD/Control error, failure or timeout
matches. Pattern counts do not establish complete ISP/media-log coverage.
This short run does not establish a soak result, PSS stability or low CPU
consumption; raw numeric CPU counters are retained with the private evidence.

Chromium reached Main stream Live WebRTC. The microphone changed from Off to
On with confirmed UI readback, then one Listen press enabled playback and
Chromium reported Audio playing. No Preview Reload was used. Audible output
has not yet been confirmed by the operator on this candidate. The operator
asked to complete other work first, so the audio check remains open.

## Imaging setting changes and restoration

On the same installed candidate, the imaging API saved anti-flicker from
mode 2 to 1 and back to 2. Fresh independent SDK reads and the settings file
agreed after each save. Brightness, contrast, saturation and sharpness each
then changed from 128 to 129 and back to 128. Each POST returned HTTP 200,
fresh SDK reads reported the requested value, and a separate pinned-SSH read
of the saved file confirmed both the change and restoration. The test session
logged out with HTTP 204.

The initial image probe stopped before writing because the fresh configuration
had no image section; its live values came from defaults. Saving anti-flicker
through RVD's normal config-save path also materialized those unchanged image
defaults. The four image tests followed only after those saved values were
available. Effective values were restored, but the file is therefore not
byte-identical to the fresh-install file. This is bounded GET/POST and
hardware-readback evidence, not visual-quality, reboot-persistence or complete
settings-matrix acceptance. No service or camera restart was performed.

A later extended run changed and restored the individual readback values for
backlight compensation, dynamic range, highlight tone, hue, defective-pixel
correction, exposure compensation and both flips. Defog remained unavailable;
noise reduction exposed setter acknowledgement only and was not changed.
These individual successes do not prove restoration of the whole image state:
highlight tone was observed at 2 before the batch, but at 0 by the time its
own test began after backlight and dynamic-range changes. Its own 0-to-1-to-0
test passed. The cause of the earlier transition remains unresolved, so the
settings matrix stays open and global restoration is not claimed. Optional
absent settings retain sensor defaults at startup; the test materialized
several formerly implicit defaults. No restart has tested those saved values.

The targeted follow-up isolated the transition. With tone set to 2, setting
backlight to 1 changed live tone to 0. Restoring backlight to 0 left tone at 0.
After reseeding tone to 2, changing dynamic range from 128 to 129 and back
left tone at 2. Final independent SDK and disk reads verified backlight 0,
dynamic range 128 and tone 2. This establishes a backlight-to-tone dependency,
not whether the reverse direction has the same effect. Save/readback handling
must account for affected companion settings before full acceptance.

The exact build's T31 1.1.4 SDK header documents highlight suppression strength
as 0–10. Current RVD and Control declare a tone maximum of 255, and their test
fixtures include tone 20. That is a source-level range defect requiring a
regression-tested correction; the live probe used only values 0, 1 and 2.

The subsequent source-only correction limits tone to 0–10 in Control and RVD.
Focused Control imaging tests passed 21/21, including rejection of 11 before
IPC and a checked save at 10. The native image-persistence suite passed with
ASan/UBSan, including rejection of 11 without invoking setters. WebUI imaging
tests passed 9/9 with corrected fixtures. The locked Raptor patch and tree
identities were updated. This correction is not installed on the camera;
backlight/tone companion-setting handling remains unfinished. No new firmware
build or device acceptance is claimed for the changed source.

The reverse live probe then confirmed that enabling tone also clears
backlight. Disabling tone leaves both off. The camera was restored to
backlight 0, dynamic range 128 and tone 2, with independent hardware and saved
readback. Source now treats backlight and tone as a mutually exclusive pair:
conflicting positive values are rejected before setters, omitted companions
are read from current hardware rather than assumed defaults, and switching
requires an explicit zero for the previously active control. RVD applies the
disabled control first, verifies both final values before caching the pair,
and Control verifies both live and saved values. WebUI explains the constraint
and rejects a conflicting form before sending it.

Focused Control imaging tests passed 22/22 and WebUI imaging tests 10/10.
The native ASan/UBSan suite now models the SDK's reciprocal clearing, verifies
switching in both directions and rejects an injected incorrect final companion
without committing the pair to the configuration cache. These source fixes
are not yet built into or installed on the camera. Full configuration and
physical acceptance remain open.
